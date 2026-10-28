import os
from pathlib import Path


def load_env(path):
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def serpapi_key():
    return os.environ.get("SERPAPI_KEY", "").strip()


def country():
    return os.environ.get("COUNTRY", "us").strip().lower() or "us"


def port():
    return int(os.environ.get("PORT", "8000"))
