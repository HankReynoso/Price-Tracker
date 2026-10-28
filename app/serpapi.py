"""Google Shopping results via SerpApi: https://serpapi.com/google-shopping-api"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API_URL = "https://serpapi.com/search.json"


class SerpApiError(Exception):
    pass


def search(query, api_key, country="us", timeout=25):
    params = urlencode(
        {"engine": "google_shopping", "q": query, "gl": country, "hl": "en", "api_key": api_key}
    )
    try:
        with urlopen(f"{API_URL}?{params}", timeout=timeout) as resp:
            data = json.load(resp)
    except HTTPError as e:
        try:
            data = json.load(e)
        except ValueError:
            raise SerpApiError(f"SerpApi returned HTTP {e.code}") from e
    except URLError as e:
        raise SerpApiError(f"Couldn't reach SerpApi: {e.reason}") from e

    error = data.get("error")
    if error:
        if "hasn't returned any results" in error:
            return []
        raise SerpApiError(error)
    return data.get("shopping_results", []) + data.get("inline_shopping_results", [])
