"""Tracked products persisted to a JSON file."""
import json
import os
import threading
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text())

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2))
        os.replace(tmp, self.path)

    def all(self):
        with self._lock:
            return self._read()

    def get(self, product_id):
        return next((p for p in self.all() if p["id"] == product_id), None)

    def upsert(self, product):
        """Replace a product in place, or add a new one to the top of the list."""
        with self._lock:
            items = self._read()
            index = next((i for i, p in enumerate(items) if p["id"] == product["id"]), None)
            if index is None:
                items.insert(0, product)
            else:
                items[index] = product
            self._write(items)

    def delete(self, product_id):
        with self._lock:
            items = self._read()
            kept = [p for p in items if p["id"] != product_id]
            self._write(kept)
            return len(kept) != len(items)
