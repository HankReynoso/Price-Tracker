import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from app import tracker
from app.store import Settings, Store


class ApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [
            mock.patch.object(tracker, "store", Store(Path(tmp.name) / "products.json")),
            mock.patch.object(tracker, "settings", Settings(Path(tmp.name) / "settings.json")),
            mock.patch.dict(os.environ, {"SERPAPI_KEY": ""}),
            mock.patch.object(server.Handler, "log_message"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req) as resp:
                return resp.status, json.load(resp)
        except HTTPError as e:
            return e.code, json.load(e)

    def add(self, query):
        return self.call("POST", "/api/products", {"query": query})

    def test_new_product_has_no_target(self):
        status, product = self.add("Sony WH-1000XM5")
        self.assertEqual(status, 201)
        self.assertIsNone(product["target_price"])

    def test_set_and_clear_target_price(self):
        _, product = self.add("Sony WH-1000XM5")
        status, updated = self.call("PATCH", f"/api/products/{product['id']}", {"target_price": 249.5})
        self.assertEqual(status, 200)
        self.assertEqual(updated["target_price"], 249.5)
        _, listed = self.call("GET", "/api/products")
        self.assertEqual(listed[0]["target_price"], 249.5)

        _, cleared = self.call("PATCH", f"/api/products/{product['id']}", {"target_price": None})
        self.assertIsNone(cleared["target_price"])

    def test_rejects_invalid_target_price(self):
        _, product = self.add("Sony WH-1000XM5")
        for bad in ["250", -5, 0, True]:
            status, body = self.call("PATCH", f"/api/products/{product['id']}", {"target_price": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn("error", body)
        _, listed = self.call("GET", "/api/products")
        self.assertIsNone(listed[0]["target_price"])

    def test_rename_rechecks_prices_and_restarts_history(self):
        _, product = self.add("Sony WH-1000XM5")
        self.call("POST", f"/api/products/{product['id']}/refresh")
        status, renamed = self.call("PATCH", f"/api/products/{product['id']}", {"name": "  Nintendo   Switch OLED "})
        self.assertEqual(status, 200)
        self.assertEqual(renamed["name"], "Nintendo Switch OLED")
        self.assertTrue(renamed["offers"])
        self.assertTrue(all(o["title"] == "Nintendo Switch OLED" for o in renamed["offers"]))
        self.assertEqual(len(renamed["history"]), 1)
        self.assertEqual(renamed["history"][0]["price"], renamed["lowest"]["price"])

    def test_rename_keeps_target(self):
        _, product = self.add("Sony WH-1000XM5")
        self.call("PATCH", f"/api/products/{product['id']}", {"target_price": 200})
        _, renamed = self.call("PATCH", f"/api/products/{product['id']}", {"name": "Sony WH-1000XM4"})
        self.assertEqual(renamed["target_price"], 200)

    def test_rejects_blank_name_and_empty_body(self):
        _, product = self.add("Sony WH-1000XM5")
        self.assertEqual(self.call("PATCH", f"/api/products/{product['id']}", {"name": "   "})[0], 400)
        self.assertEqual(self.call("PATCH", f"/api/products/{product['id']}", {})[0], 400)
        _, listed = self.call("GET", "/api/products")
        self.assertEqual(listed[0]["name"], "Sony WH-1000XM5")

    def test_patch_unknown_product_is_404(self):
        self.assertEqual(self.call("PATCH", "/api/products/abc123", {"target_price": 10})[0], 404)

    def test_adding_a_duplicate_rechecks_the_existing_product(self):
        _, first = self.add("Sony WH-1000XM5")
        status, again = self.add("  sony wh-1000xm5 ")
        self.assertEqual(status, 200)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(again["history"]), 2)
        _, listed = self.call("GET", "/api/products")
        self.assertEqual([p["id"] for p in listed], [first["id"]])

    def test_rename_to_a_tracked_name_is_rejected(self):
        _, sony = self.add("Sony WH-1000XM5")
        self.add("Nintendo Switch OLED")
        status, body = self.call("PATCH", f"/api/products/{sony['id']}", {"name": "nintendo switch oled"})
        self.assertEqual(status, 409)
        self.assertIn("already tracking", body["error"])

    def test_list_order_is_kept_after_refresh_and_update(self):
        _, older = self.add("Sony WH-1000XM5")
        _, newer = self.add("Nintendo Switch OLED")
        self.call("POST", f"/api/products/{older['id']}/refresh")
        self.call("PATCH", f"/api/products/{older['id']}", {"target_price": 200})
        _, listed = self.call("GET", "/api/products")
        self.assertEqual([p["id"] for p in listed], [newer["id"], older["id"]])


    def test_daily_checks_are_off_until_turned_on(self):
        self.assertFalse(self.call("GET", "/api/status")[1]["daily_checks"])
        status, saved = self.call("PATCH", "/api/settings", {"daily_checks": True})
        self.assertEqual(status, 200)
        self.assertTrue(saved["daily_checks"])
        self.assertTrue(self.call("GET", "/api/status")[1]["daily_checks"])

    def test_rejects_non_boolean_daily_checks(self):
        for bad in ("yes", 1, None):
            self.assertEqual(self.call("PATCH", "/api/settings", {"daily_checks": bad})[0], 400)
        self.assertFalse(self.call("GET", "/api/status")[1]["daily_checks"])

    def test_daily_check_rechecks_only_products_a_day_old(self):
        _, stale = self.add("Sony WH-1000XM5")
        _, fresh = self.add("Nintendo Switch OLED")
        later = datetime.fromisoformat(fresh["checked_at"]) + timedelta(hours=23)
        self.assertEqual(tracker.refresh_due(now=later), [])
        products = tracker.store.all()
        for p in products:
            if p["id"] == stale["id"]:
                p["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=1, minutes=1)).isoformat(timespec="seconds")
            tracker.store.upsert(p)
        self.assertEqual(tracker.refresh_due(), [stale["id"]])
        _, listed = self.call("GET", "/api/products")
        by_id = {p["id"]: p for p in listed}
        self.assertEqual(len(by_id[stale["id"]]["history"]), 2)
        self.assertEqual(len(by_id[fresh["id"]]["history"]), 1)


if __name__ == "__main__":
    unittest.main()
