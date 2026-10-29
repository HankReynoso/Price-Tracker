# Price Tracker

Keep track of the items you want to purchase across many different websites.

Add a product you're looking at (a name or a product page link) and compare the price of a **new** one across stores. Prices come from Google Shopping via [SerpApi](https://serpapi.com/google-shopping-api).

## Run

```sh
cp .env.example .env      # then paste your SERPAPI_KEY into .env
python3 server.py         # open http://localhost:8000
```

Requires Python 3.9+. No packages to install. Without a key the app runs in **demo mode** with sample prices.

## How it works

- **Links:** the server reads the product name from the page (JSON-LD, `og:title`, `<title>`), strips store branding, and falls back to the URL slug when a store blocks the request.
- **New only:** used, refurbished, renewed and open-box listings are hidden, along with listings that don't match the product name and accessories priced far below the typical price. Each product says how many were hidden.
- **One price per store:** the cheapest matching listing per store, sorted low to high.
- **Grid:** each product is a tile with its picture and name. Click it for prices by store, history, target price and settings.
- **Refresh** records the lowest price each time, so each product shows how the price changed since you added it and a small history chart. **Refresh all** checks every product in turn.
- **Target price:** set one per product; it's marked when the best price reaches it.
- **Search name:** if a link produced an odd name, edit it. That re-checks prices and starts a new history.
- Adding a product you already track re-checks it instead of creating a duplicate.

- **Stores:** a dropdown above the grid picks which stores you'll shop at. The best price, store table, history and target use only those stores. Marketplace sellers ("eBay - seller") count as the marketplace.
- **Check prices daily** (off by default): a switch above the grid. While the server is running, it re-checks each product once a day. Each check is one SerpApi search, so 3 products checked daily use about 90 of the free tier's 100 searches a month.

Tracked products are saved in `data/products.json` and settings in `data/settings.json`. Each add or refresh uses one SerpApi search (the free tier gives 100 a month).

## Tests

```sh
python3 -m unittest -v
```

Browser checks drive the real page in headless Chrome against a throwaway server with demo prices:

```sh
python3 tests/browser/run.py           # add --shots to save screenshots to tests/browser/shots/
```

Set `CHROME` to the browser binary if it isn't at the default macOS path.
