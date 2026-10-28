"""Turn raw shopping results into one clean, comparable offer per store."""
import re
from statistics import median

USED_RE = re.compile(
    r"\b(used|refurbished|renewed|pre-?owned|open[- ]box|second[- ]hand|for parts)\b", re.I
)
STOPWORDS = {"the", "and", "for", "with", "a", "an", "of", "in", "new", "by"}
# Listings far below the typical price are almost always accessories or bundles of parts.
OUTLIER_RATIO = 0.4
MIN_RELEVANCE = 0.5
# Marketplaces list sellers as "eBay - seller name"; the filter and per-store grouping want the marketplace.
SELLER_SUFFIX_RE = re.compile(r"\s+-\s+.*$")


def tokens(text):
    words = re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", text.lower())
    return [re.sub(r"[-./]", "", w) for w in words if w not in STOPWORDS and len(w) >= 2]


def relevance(query, title):
    """Fraction of the query's words that appear in the listing title."""
    wanted = tokens(query)
    if len(wanted) <= 1:
        return 1.0
    have = set(tokens(title))
    joined = re.sub(r"[^a-z0-9]", "", title.lower())
    hits = sum(1 for w in wanted if w in have or w in joined)
    return hits / len(wanted)


def normalize(raw):
    price = raw.get("extracted_price")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    title = raw.get("title") or ""
    return {
        "store": SELLER_SUFFIX_RE.sub("", (raw.get("source") or "").strip()) or "Unknown store",
        "title": title,
        "price": float(price),
        "url": raw.get("link") or raw.get("product_link"),
        "thumbnail": raw.get("thumbnail"),
        "delivery": raw.get("delivery"),
        "rating": raw.get("rating"),
        "reviews": raw.get("reviews"),
        "used": bool(raw.get("second_hand_condition")) or bool(USED_RE.search(title)),
    }


def build(raw_results, query):
    """Filter to new, relevant listings and keep the cheapest one per store."""
    hidden = {"used": 0, "unrelated": 0}
    offers = []
    for raw in raw_results:
        offer = normalize(raw)
        if offer is None:
            continue
        if offer.pop("used"):
            hidden["used"] += 1
        elif relevance(query, offer["title"]) < MIN_RELEVANCE:
            hidden["unrelated"] += 1
        else:
            offers.append(offer)

    if len(offers) >= 3:
        floor = median(o["price"] for o in offers) * OUTLIER_RATIO
        kept = [o for o in offers if o["price"] >= floor]
        hidden["unrelated"] += len(offers) - len(kept)
        offers = kept

    best = {}
    for offer in offers:
        key = offer["store"].lower()
        if key not in best or offer["price"] < best[key]["price"]:
            best[key] = offer
    offers = sorted(best.values(), key=lambda o: o["price"])

    return {
        "offers": offers,
        "hidden": hidden,
        "lowest": offers[0] if offers else None,
        "highest": offers[-1]["price"] if offers else None,
    }
