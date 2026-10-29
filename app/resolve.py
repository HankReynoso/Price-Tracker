"""Work out a searchable product name from whatever the user typed: a name or a product link."""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote
from urllib.request import Request, urlopen

URL_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?$", re.I)
SEPARATOR_RE = re.compile(r"\s+[|–—:-]\s+")
MAX_WORDS = 12
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class ResolveError(Exception):
    pass


def is_url(text):
    text = text.strip()
    return " " not in text and bool(URL_RE.match(text))


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.title = ""
        self.ld_json = []
        self._in = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:title", "twitter:title", "og:image") and attrs.get("content"):
                self.meta.setdefault(key, attrs["content"])
        elif tag == "title":
            self._in = "title"
        elif tag == "script" and attrs.get("type") == "application/ld+json":
            self._in = "ld"
            self.ld_json.append("")

    def handle_endtag(self, tag):
        if tag in ("title", "script"):
            self._in = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "ld":
            self.ld_json[-1] += data


def _ld_product_name(blocks):
    def walk(node):
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds and isinstance(node.get("name"), str):
                return node["name"]
            return walk(node.get("@graph"))
        return None

    for block in blocks:
        try:
            found = walk(json.loads(block))
        except ValueError:
            continue
        if found:
            return found
    return None


def page_info(page):
    """Return (title, image url) from a product page's markup."""
    parser = _MetaParser()
    parser.feed(page)
    title = (
        _ld_product_name(parser.ld_json)
        or parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title
        or None
    )
    image = parser.meta.get("og:image")
    return title, image if image and image.startswith("https://") else None


def _site_label(host):
    parts = host.lower().removeprefix("www.").split(".")
    return parts[0] if parts else ""


def clean_title(title, host=""):
    """Strip store branding from a page title so it works as a search query."""
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()
    title = re.sub(r"^amazon(\.[a-z.]+)?\s*:\s*", "", title, flags=re.I)
    site = _site_label(host)
    segments = [s for s in SEPARATOR_RE.split(title) if s.strip()]
    if site:
        kept = [s for s in segments if site not in re.sub(r"[^a-z0-9]", "", s.lower())]
        segments = kept or segments
    title = segments[0] if segments else title
    if len(title.split()) > 6 and "," in title:
        title = title.split(",", 1)[0]
    return " ".join(title.split()[:MAX_WORDS]).strip()


def title_from_slug(url):
    """Fallback for stores that block page fetches: product names often live in the URL path."""
    best = ""
    for segment in urlparse(url).path.split("/"):
        segment = re.sub(r"\.(html?|php|aspx?)$", "", unquote(segment), flags=re.I)
        words = [w for w in re.split(r"[-_+]+", segment) if w]
        if len([w for w in words if re.search(r"[a-z]", w, re.I)]) >= 2 and len(words) > len(best.split()):
            best = " ".join(words)
    return " ".join(best.split()[:MAX_WORDS]) or None


def fetch_page(url, timeout=8):
    with urlopen(Request(url, headers=HEADERS), timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read(2_000_000).decode(charset, errors="replace")


def product_name(query):
    """Return (search name, source url or None, image url or None)."""
    query = query.strip()
    if not is_url(query):
        return " ".join(query.split()), None, None

    url = query if query.startswith(("http://", "https://")) else f"https://{query}"
    host = urlparse(url).hostname or ""
    name = image = None
    try:
        raw, image = page_info(fetch_page(url))
        if raw:
            name = clean_title(raw, host)
    except Exception:
        # Many large stores block automated fetches; the URL slug is the fallback.
        pass
    name = name or title_from_slug(url)
    if not name:
        raise ResolveError("Couldn't read a product name from that link. Try typing the product name instead.")
    return name, url, image
