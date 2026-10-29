"""Drive the app in headless Chrome and check what a user sees and does.

Run: python3 tests/browser/run.py [--shots]
Starts its own server on a free port with a throwaway data dir and demo prices.
Set CHROME to the browser binary if it isn't at the default macOS path.
--shots saves screenshots to tests/browser/shots/.
"""
import atexit
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = Path(__file__).resolve().parent / "shots"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WORK = Path(tempfile.mkdtemp(prefix="price-tracker-browser-"))
SAVE_SHOTS = "--shots" in sys.argv


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
DATA = WORK / "data"


def start_server():
    env = {**os.environ, "PORT": str(PORT), "DATA_DIR": str(DATA), "SERPAPI_KEY": ""}
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(proc.kill)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/status")
            return
        except OSError:
            time.sleep(0.1)
    sys.exit("server did not start")


def _wire(read_fd, write_fd):
    # Chrome's --remote-debugging-pipe reads fd 3 and writes fd 4. Move both ends clear first so dup2 can't clobber.
    hi_r, hi_w = os.dup(read_fd) + 100, os.dup(write_fd) + 100
    os.dup2(read_fd, hi_r)
    os.dup2(write_fd, hi_w)
    os.dup2(hi_r, 3)
    os.dup2(hi_w, 4)


class Chrome:
    def __init__(self):
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--remote-debugging-pipe", "--window-size=1000,1100",
             f"--user-data-dir={WORK / 'profile'}", "about:blank"],
            close_fds=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=lambda: _wire(r1, w2),
        )
        atexit.register(self.proc.kill)
        os.close(r1)
        os.close(w2)
        self.out = os.fdopen(w1, "wb", buffering=0)
        self.inp = os.fdopen(r2, "rb", buffering=0)
        self.buf = b""
        self.next_id = 0
        self.session = None
        target = self.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.send("Page.enable")

    def send(self, method, params=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if self.session:
            msg["sessionId"] = self.session
        self.out.write(json.dumps(msg).encode() + b"\0")
        while True:
            while b"\0" not in self.buf:
                self.buf += self.inp.read(65536)
            raw, self.buf = self.buf.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def js(self, expr):
        result = self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS error in {expr!r}: {result['exceptionDetails']}")
        return result["result"].get("value")

    def wait(self, expr, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return True
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for {expr}")

    def goto(self, url):
        self.send("Page.navigate", {"url": url})
        time.sleep(0.3)
        self.wait("document.readyState === 'complete'")

    def size(self, width, height=1000, mobile=False):
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 2 if mobile else 1, "mobile": mobile})

    def dark(self, on):
        self.send("Emulation.setEmulatedMedia",
                  {"features": [{"name": "prefers-color-scheme", "value": "dark" if on else "light"}]})

    def key(self, key, vk, text=None):
        down = {"type": "rawKeyDown", "key": key, "code": key, "windowsVirtualKeyCode": vk}
        self.send("Input.dispatchKeyEvent", down)
        if text:
            self.send("Input.dispatchKeyEvent", {**down, "type": "char", "text": text})
        self.send("Input.dispatchKeyEvent", {**down, "type": "keyUp"})

    def click_at(self, x, y):
        for kind in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    def shot(self, name):
        if not SAVE_SHOTS:
            return
        SHOTS.mkdir(exist_ok=True)
        data = self.send("Page.captureScreenshot", {"format": "png"})["data"]
        (SHOTS / f"{name}.png").write_bytes(base64.b64decode(data))


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


failures = []


def check(ok, label):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def seed_history(product_id, prices):
    path = DATA / "products.json"
    products = json.loads(path.read_text())
    product = next(p for p in products if p["id"] == product_id)
    start = datetime.now(timezone.utc) - timedelta(days=len(prices))
    product["history"] = [
        {"at": (start + timedelta(days=i)).isoformat(timespec="seconds"), "price": price, "store": "Amazon"}
        for i, price in enumerate(prices)
    ] + product["history"][-1:]
    path.write_text(json.dumps(products))


def eventually(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


def columns():
    return chrome.js("new Set([...document.querySelectorAll('#products .tile:not([hidden])')].map(t => t.offsetLeft)).size")


def tile(product_id):
    return f"document.querySelector('.tile[data-id=\"{product_id}\"]')"


DETAIL_OPEN = "document.querySelector('#detail-dialog').open"
DETAIL = "document.querySelector('#detail')"

start_server()
chrome = Chrome()
chrome.size(1000, 1100)
chrome.dark(False)

chrome.goto(BASE + "/")
chrome.wait("!document.querySelector('#empty').hidden")
check(True, "empty state shows on first run")
chrome.shot("empty-1000-light")

products = [api("POST", "/api/products", {"query": q}) for q in
            ["Sony WH-1000XM5", "Nintendo Switch OLED", "Kindle Paperwhite", "Dyson V15 Detect",
             "Apple AirPods Pro 2", "LEGO Millennium Falcon 75192", "Instant Pot Duo 7-in-1"]]
sony = products[0]
seed_history(sony["id"], [352.99, 349.99, 341.00, 336.50, 329.99])

chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('#products .tile').length === 7")
check(columns() >= 5, f"1000px: tiles lay out in a grid ({columns()} columns)")
tile_text = chrome.js(f"{tile(sony['id'])}.textContent")
check("Sony WH-1000XM5" in tile_text and "$" not in tile_text, "a tile shows the picture and name, not the price")
chrome.shot("grid-1000-light")

chrome.js(f"{tile(sony['id'])}.click()")
chrome.wait(DETAIL_OPEN)
check(chrome.js(f"{DETAIL}.querySelector('.name').textContent") == sony["name"], "clicking a tile opens its details")
check(chrome.js(f"location.hash === '#open={sony['id']}'"), "opening details writes #open=<id>")
check(chrome.js(f"{DETAIL}.querySelectorAll('.offers tbody tr').length") > 0, "details list the stores")
check(chrome.js(f"{DETAIL}.querySelector('time[data-ago]').textContent").startswith("checked "),
      "details show 'checked X ago'")
chrome.shot("detail-1000-light")

box = chrome.js(f"(() => {{ const b = {DETAIL}.querySelector('.plot').getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; }})()")
chrome.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box[0] + box[2] * 0.5, "y": box[1] + box[3] / 2})
tip = chrome.js(f"(() => {{ const t = {DETAIL}.querySelector('.tip'); return t.hidden ? null : t.textContent; }})()")
check(bool(tip) and "$" in tip and "·" in tip, f"chart hover shows date, price and store: {tip!r}")
label = chrome.js(f"{DETAIL}.querySelector('.spark').getAttribute('aria-label')")
check(label.startswith("Lowest price over 6 checks"), f"chart has a text equivalent: {label!r}")

chrome.js(f"(() => {{ const i = {DETAIL}.querySelector('.target-form input'); i.value = '999'; i.form.requestSubmit(); }})()")
chrome.wait(f"{DETAIL}.querySelector('.cue') && {DETAIL}.querySelector('.cue').textContent.includes('Under target')")
check(next(p for p in api("GET", "/api/products") if p["id"] == sony["id"])["target_price"] == 999,
      "setting a target saves it")
check(chrome.js(DETAIL_OPEN), "details stay open while saving")
chrome.js(f"{DETAIL}.querySelector('.clear-target').click()")
chrome.wait(f"!{DETAIL}.querySelector('.cue').textContent.includes('target')")
check(True, "clearing the target removes the target cue")

chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")
check(chrome.js(f"document.activeElement === {tile(sony['id'])}"), "Escape closes details and returns focus to the tile")
check(chrome.js("location.hash === ''"), "closing details clears the hash")

chrome.key("Enter", 13, "\r")
chrome.wait(DETAIL_OPEN)
check(True, "Enter on a focused tile opens details")
chrome.click_at(5, 5)
chrome.wait(f"!{DETAIL_OPEN}")
check(True, "clicking the backdrop closes details")

nintendo = products[1]
chrome.js(f"{tile(sony['id'])}.click()")
chrome.wait(DETAIL_OPEN)
chrome.js(f"document.querySelector('#detail .close').click(); {tile(nintendo['id'])}.click()")
time.sleep(0.5)
check(chrome.js(f"{DETAIL_OPEN} && {DETAIL}.querySelector('.name')?.textContent") == nintendo["name"],
      "closing one product and opening another at once shows the second")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

chrome.js(f"location.hash = 'open={sony['id']}'")
chrome.wait(DETAIL_OPEN)
check(True, "following an #open=<id> link opens details")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

chrome.goto("about:blank")
chrome.goto(f"{BASE}/#open={sony['id']}")
chrome.wait(DETAIL_OPEN)
check(chrome.js(f"{DETAIL}.querySelector('.name').textContent") == sony["name"], "loading the page with #open=<id> opens details")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

placeholder = chrome.js("document.querySelector('#query').value = 'Bose QuietComfort Ultra'; document.querySelector('#add-form').requestSubmit(); document.querySelector('#products .tile.placeholder')?.textContent")
check(placeholder == "Bose QuietComfort UltraSearching stores…", "a placeholder tile shows while searching")
chrome.wait(DETAIL_OPEN)
bose = next(p for p in api("GET", "/api/products") if p["name"] == "Bose QuietComfort Ultra")
check(chrome.js(f"{DETAIL}.querySelector('.name').textContent") == bose["name"], "a newly added product opens its details")
check(chrome.js(f"{DETAIL}.querySelector('.history .hint').textContent").startswith("Price history appears"),
      "one check shows a hint instead of a chart")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

chrome.js("document.querySelector('#query').value = 'bose quietcomfort ULTRA'; document.querySelector('#add-form').requestSubmit()")
chrome.wait("document.querySelector('#form-status').textContent.startsWith('Already tracking')")
check(chrome.js("document.querySelectorAll('.tile[data-id]').length") == 8, "adding a duplicate keeps one tile")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

chrome.js(f"{tile(bose['id'])}.click()")
chrome.wait(DETAIL_OPEN)
chrome.js(f"{DETAIL}.querySelector('.remove').click()")
check(chrome.js(f"!{DETAIL_OPEN} && {tile(bose['id'])}.hidden && !document.querySelector('#toast').hidden"),
      "Remove closes details, hides the tile and shows Undo")
check(chrome.js("document.activeElement.className") == "toast-undo", "focus moves to Undo")
chrome.shot("toast-1000-light")
chrome.js("document.querySelector('.toast-undo').click()")
check(chrome.js(f"!{tile(bose['id'])}.hidden && document.querySelector('#toast').hidden"), "Undo brings the tile back")
time.sleep(5.5)
check(any(p["id"] == bose["id"] for p in api("GET", "/api/products")), "an undone removal never reaches the server")

chrome.js(f"{tile(bose['id'])}.click()")
chrome.wait(DETAIL_OPEN)
chrome.js(f"{DETAIL}.querySelector('.remove').click()")
time.sleep(5.6)
check(not any(p["id"] == bose["id"] for p in api("GET", "/api/products")), "removal is sent after the undo window")

dyson = next(p for p in api("GET", "/api/products") if p["name"].startswith("Dyson"))
chrome.js(f"{tile(dyson['id'])}.click()")
chrome.wait(DETAIL_OPEN)
chrome.js(f"{DETAIL}.querySelector('.remove').click()")
chrome.goto("about:blank")
time.sleep(0.5)
check(not any(p["id"] == dyson["id"] for p in api("GET", "/api/products")), "a pending removal is sent when the page closes")

chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('.tile[data-id]').length === 6")
chrome.js("window.__progress = []; new MutationObserver(() => __progress.push(document.querySelector('#refresh-all').textContent)).observe(document.querySelector('#refresh-all'), {childList: true, characterData: true, subtree: true}); document.querySelector('#refresh-all').click()")
chrome.wait("document.querySelector('#refresh-all').textContent === 'Refresh all' && !document.querySelector('#refresh-all').disabled", timeout=20)
progress = chrome.js("__progress")
check("Refreshing 1 of 6…" in progress and "Refreshing 6 of 6…" in progress, "Refresh all shows progress")

ghost = api("POST", "/api/products", {"query": "Roomba j7+"})
chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('.tile[data-id]').length === 7")
api("DELETE", f"/api/products/{ghost['id']}")
chrome.js(f"{tile(ghost['id'])}.click()")
chrome.wait(DETAIL_OPEN)
chrome.js(f"{DETAIL}.querySelector('.refresh').click()")
chrome.wait(f"{DETAIL}.querySelector('.meta').classList.contains('error')")
check(chrome.js(f"{DETAIL}.querySelector('.meta').textContent") == "! Product not found", "a failed refresh shows the error in details")
check(chrome.js(f"{tile(ghost['id'])}.querySelector('.tile-status').textContent") == "! Product not found",
      "the tile shows the error too")
chrome.wait(f"!{DETAIL}.querySelector('.meta').classList.contains('error')", timeout=10)
check(True, "the error clears itself")
chrome.key("Escape", 27)

chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('.tile[data-id]').length >= 6")
sony_now = next(p for p in api("GET", "/api/products") if p["id"] == sony["id"])
known = sorted({o["store"] for p in api("GET", "/api/products") for o in p["offers"]}, key=str.casefold)
check(chrome.js("document.querySelector('#store-label').textContent") == "All stores", "the store filter starts at All stores")
chrome.js("document.querySelector('#store-filter summary').click()")
options = chrome.js("[...document.querySelectorAll('#store-options input')].map(b => [b.value, b.checked])")
check([o[0] for o in options] == known and all(o[1] for o in options), "the dropdown lists every store, all ticked")

pick = sony_now["offers"][1]
for store in known:
    if store != pick["store"]:
        chrome.js(f"document.querySelector('#store-options input[value=\"{store}\"]').click()")
chrome.wait(f"document.querySelector('#store-label').textContent === '{pick['store']}'")
check(eventually(lambda: api("GET", "/api/status")["stores"] == [pick["store"]]), "the chosen store is saved")
chrome.key("Escape", 27)
check(not chrome.js("document.querySelector('#store-filter').open"), "Escape closes the store menu")

chrome.js(f"{tile(sony['id'])}.click()")
chrome.wait(DETAIL_OPEN)
price = chrome.js(f"{DETAIL}.querySelector('.price').textContent")
stores_shown = chrome.js(f"[...{DETAIL}.querySelectorAll('.offers tbody tr td:first-child')].map(td => td.firstChild.textContent)")
check(price == f"${pick['price']:,.2f}" and stores_shown == [pick["store"]],
      f"details show only the chosen store's price ({price} at {stores_shown})")
check("at stores you didn't choose" in chrome.js(f"{DETAIL}.querySelector('.note').textContent"),
      "details say how many stores the filter hides")
chrome.shot("filtered-detail-1000-light")
chrome.key("Escape", 27)
chrome.wait(f"!{DETAIL_OPEN}")

chrome.goto(BASE + "/")
chrome.wait(f"document.querySelector('#store-label').textContent === '{pick['store']}'")
check(True, "the store filter survives a reload")

missing = [s for s in known if s not in {o["store"] for o in sony_now["offers"]}]
if missing:
    chrome.js("document.querySelector('#store-filter summary').click()")
    chrome.js(f"document.querySelector('#store-options input[value=\"{missing[0]}\"]').click()")
    chrome.js(f"document.querySelector('#store-options input[value=\"{pick['store']}\"]').click()")
    chrome.wait(f"document.querySelector('#store-label').textContent === '{missing[0]}'")
    chrome.click_at(900, 1000)
    check(not chrome.js("document.querySelector('#store-filter').open"), "clicking elsewhere closes the store menu")
    chrome.js(f"{tile(sony['id'])}.click()")
    chrome.wait(DETAIL_OPEN)
    check(chrome.js(f"{DETAIL}.querySelector('.no-offers').textContent").startswith("None of your chosen stores"),
          "a product none of the chosen stores sell says so")
    chrome.key("Escape", 27)
    chrome.wait(f"!{DETAIL_OPEN}")

chrome.js("document.querySelector('#store-filter summary').click(); document.querySelector('#all-stores').click()")
chrome.wait("document.querySelector('#store-label').textContent === 'All stores'")
check(eventually(lambda: api("GET", "/api/status")["stores"] == []), "All stores clears the filter")
chrome.key("Escape", 27)

check(not chrome.js("document.querySelector('#daily').checked"), "daily checks start off")
chrome.js("document.querySelector('#daily').click()")
chrome.wait("document.querySelector('#form-status').textContent.startsWith('Daily checks on')")
check(api("GET", "/api/status")["daily_checks"] is True, "turning on daily checks saves the setting")
chrome.goto(BASE + "/")
chrome.wait("document.querySelector('#daily').checked")
check(True, "the daily setting survives a reload")
chrome.js("document.querySelector('#daily').click()")
chrome.wait("document.querySelector('#form-status').textContent === 'Daily checks off.'")
check(api("GET", "/api/status")["daily_checks"] is False, "turning daily checks off saves it")

rechecked = api("POST", f"/api/products/{sony['id']}/refresh")
chrome.js("document.dispatchEvent(new Event('visibilitychange'))")
chrome.wait(f"state.products.find(p => p.id === '{sony['id']}').checked_at === '{rechecked['checked_at']}'")
check(True, "returning to the tab picks up prices checked in the background")

chrome.size(1440, 1000)
chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('.tile[data-id]').length >= 6")
check(columns() >= 6, f"1440px: grid widens ({columns()} columns)")

chrome.size(375, 812, mobile=True)
chrome.dark(True)
chrome.goto(BASE + "/")
chrome.wait("document.querySelectorAll('.tile[data-id]').length >= 6")
check(columns() == 3, f"375px: three tiles per row ({columns()} columns)")
check(chrome.js("document.documentElement.scrollWidth") <= 375, "375px: no horizontal scroll")
head = chrome.js("Math.max(...[...document.querySelectorAll('.list-head *')].filter(e => !e.closest('.menu')).map(e => e.getBoundingClientRect().right))")
check(head <= 375, f"375px: the list header fits ({head:.1f}px)")
chrome.js("document.querySelector('#store-filter summary').click()")
menu = chrome.js("(() => { const b = document.querySelector('.menu').getBoundingClientRect(); return [b.left, b.right]; })()")
check(menu[0] >= 0 and menu[1] <= 375, f"375px: the store menu fits on screen {menu}")
chrome.shot("store-menu-375-dark")
chrome.key("Escape", 27)
chrome.shot("grid-375-dark")
chrome.js(f"{tile(sony['id'])}.click()")
chrome.wait(DETAIL_OPEN)
rect = chrome.js("(() => { const b = document.querySelector('#detail-dialog').getBoundingClientRect(); return [b.left, b.right, b.bottom]; })()")
check(rect[0] >= 0 and rect[1] <= 375 and abs(rect[2] - 812) < 2, f"375px: details open as a bottom sheet within the screen {rect}")
widest = chrome.js("Math.max(...[...document.querySelectorAll('#detail *')].filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect().right))")
check(widest <= 375.5, f"375px: nothing in details runs off screen (rightmost {widest:.1f}px)")
chrome.shot("detail-375-dark")

print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
