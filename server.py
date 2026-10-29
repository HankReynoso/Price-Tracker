"""Price Tracker web server. Run: python3 server.py"""
import json
import math
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import config

ROOT = Path(__file__).resolve().parent
config.load_env(ROOT / ".env")

from app import tracker  # noqa: E402  (reads config after .env is loaded)

PRODUCT_RE = re.compile(r"/api/products/([0-9a-f]+)")
REFRESH_RE = re.compile(r"/api/products/([0-9a-f]+)/refresh")
MAX_NAME = 200
MAX_STORES = 100
CHECK_EVERY = 3600
wake_checker = threading.Event()


def parse_changes(body):
    """Validate a PATCH body into {name?, target_price?}. Raises ValueError with a user-facing message."""
    if not isinstance(body, dict) or not body.keys() & {"name", "target_price"}:
        raise ValueError("Send a name or target_price to change.")
    changes = {}
    if "name" in body:
        name = " ".join(str(body["name"] or "").split())
        if not name or len(name) > MAX_NAME:
            raise ValueError("Enter a product name.")
        changes["name"] = name
    if "target_price" in body:
        target = body["target_price"]
        if target is not None:
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or target <= 0:
                raise ValueError("Target price must be a positive number.")
            target = round(float(target), 2)
        changes["target_price"] = target
    return changes


def parse_settings(body):
    """Validate a settings PATCH. `stores` is the shopper's chosen stores; empty means every store."""
    if not isinstance(body, dict) or not body.keys() & {"daily_checks", "stores"}:
        raise ValueError("Send daily_checks or stores to change.")
    changes = {}
    if "daily_checks" in body:
        if not isinstance(body["daily_checks"], bool):
            raise ValueError("Send daily_checks as true or false.")
        changes["daily_checks"] = body["daily_checks"]
    if "stores" in body:
        stores = body["stores"]
        if (
            not isinstance(stores, list)
            or len(stores) > MAX_STORES
            or not all(isinstance(s, str) and 0 < len(s.strip()) <= MAX_NAME for s in stores)
        ):
            raise ValueError("Send stores as a list of store names.")
        changes["stores"] = sorted({s.strip() for s in stores}, key=str.casefold)
    return changes


def daily_checker():
    while True:
        if tracker.settings.get()["daily_checks"]:
            try:
                checked = tracker.refresh_due()
                if checked:
                    print(f"Daily check: re-checked {len(checked)} product(s)", flush=True)
            except Exception as e:  # keep the checker alive; the next pass retries
                print(f"Daily check failed: {e}", flush=True)
        wake_checker.wait(CHECK_EVERY)
        wake_checker.clear()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "public"), **kwargs)

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def _handle(self, action):
        try:
            return action()
        except tracker.TrackerError as e:
            self._json(e.status, {"error": str(e)})

    def do_GET(self):
        if self.path == "/api/status":
            return self._json(200, {"demo": tracker.demo_mode(), **tracker.settings.get()})
        if self.path == "/api/products":
            return self._json(200, tracker.list_products())
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "Not found"})
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/products":
            query = str(self._body().get("query") or "").strip()
            if not query:
                return self._json(400, {"error": "Enter a product name or link."})
            def add():
                product, created = tracker.add_product(query)
                self._json(201 if created else 200, product)
            return self._handle(add)
        if m := REFRESH_RE.fullmatch(self.path):
            return self._handle(lambda: self._json(200, tracker.refresh_product(m[1])))
        self._json(404, {"error": "Not found"})

    def do_PATCH(self):
        if self.path == "/api/settings":
            try:
                changes = parse_settings(self._body())
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            saved = tracker.settings.update(changes)
            if changes.get("daily_checks"):
                wake_checker.set()
            return self._json(200, saved)
        if m := PRODUCT_RE.fullmatch(self.path):
            try:
                changes = parse_changes(self._body())
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._handle(lambda: self._json(200, tracker.update_product(m[1], changes)))
        self._json(404, {"error": "Not found"})

    def do_DELETE(self):
        if m := PRODUCT_RE.fullmatch(self.path):
            return self._handle(lambda: (tracker.delete_product(m[1]), self._json(200, {"ok": True})))
        self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    port = config.port()
    mode = "DEMO mode (no SERPAPI_KEY set)" if tracker.demo_mode() else "live SerpApi prices"
    print(f"Price Tracker running at http://localhost:{port}  [{mode}]")
    threading.Thread(target=daily_checker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
