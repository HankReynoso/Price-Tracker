"""Sample shopping results in SerpApi's shape, used when no API key is set."""
import hashlib
import random
from datetime import date
from urllib.parse import quote_plus

STORES = ["Amazon", "Walmart", "Best Buy", "Target", "eBay", "B&H Photo", "Newegg", "Costco"]


def search(query):
    seed = hashlib.sha256(query.lower().encode()).hexdigest()
    base = random.Random(seed).uniform(40, 600)
    # Reseed daily so refreshing on a later day shows price movement.
    rng = random.Random(f"{seed}:{date.today().isoformat()}")
    link = f"https://www.google.com/search?tbm=shop&q={quote_plus(query)}"

    results = []
    for store in rng.sample(STORES, 6):
        price = round(base * rng.uniform(0.85, 1.15)) - 0.01
        results.append({
            "title": query,
            "source": store,
            "price": f"${price:,.2f}",
            "extracted_price": price,
            "product_link": link,
            "delivery": rng.choice(["Free delivery", "Free 2-day delivery", "$5.99 delivery", None]),
            "rating": round(rng.uniform(3.8, 4.9), 1),
            "reviews": rng.randint(20, 9000),
        })
    # Listings the filters should hide: a used unit and a cheap accessory.
    results.append({"title": f"{query} (Renewed)", "source": "Amazon", "extracted_price": round(base * 0.6, 2)})
    results.append({"title": f"Case for {query}", "source": "eBay", "extracted_price": 12.99})
    return results
