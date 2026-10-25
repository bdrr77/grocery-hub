import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

temporary = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///" + temporary.name.replace("\\", "/") + "/test.db")
os.environ["SCANNER_DEVICE_TOKENS"] = "test:secret,other:other-secret"

from fastapi.testclient import TestClient
from app.main import app
from app.database import engine, metadata
from app.products import product_label


class ShoppingListTests(unittest.TestCase):
    def setUp(self):
        metadata.drop_all(engine)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.lookup = patch("app.main.product_label", return_value="Test product")
        self.lookup.start()

    def tearDown(self):
        self.lookup.stop()
        self.client_context.__exit__(None, None, None)

    def add(self, event="web-event-1", barcode="3017620422003"):
        return self.client.post("/api/v1/items", json={"barcode": barcode, "event_id": event})

    def test_add_persist_increment_and_remove(self):
        self.assertEqual(self.client.get("/api/v1/items").json(), [])
        first = self.add()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()[0]["label"], "Test product")
        engine.dispose()  # A new database connection must see the saved item.
        self.assertEqual(self.client.get("/api/v1/items").json()[0]["quantity"], 1)
        self.assertEqual(self.add("web-event-2").json()[0]["quantity"], 2)
        self.assertEqual(self.client.delete("/api/v1/items/3017620422003").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/items").json(), [])

    def test_retry_does_not_increment_and_conflicting_event_is_rejected(self):
        self.add()
        self.assertEqual(self.add().json()[0]["quantity"], 1)
        self.assertEqual(self.add(barcode="5449000214911").status_code, 409)
        self.assertEqual(len(self.client.get("/api/v1/items").json()), 1)

    def test_scanner_auth_and_shared_persistent_list(self):
        payload = {"barcode": "3017620422003", "event_id": "scanner-event-1"}
        self.assertEqual(self.client.post("/api/v1/items/scan", json=payload).status_code, 401)
        headers = {"X-Device-ID": "test", "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.post("/api/v1/items/scan", json=payload, headers=headers).status_code, 401)
        headers["Authorization"] = "Bearer secret"
        for _ in range(2):
            self.assertTrue(self.client.post("/api/v1/items/scan", json=payload, headers=headers).json()["accepted"])
        self.assertEqual(self.client.get("/api/v1/items").json()[0]["quantity"], 1)
        headers = {"X-Device-ID": "other", "Authorization": "Bearer other-secret"}
        self.client.post("/api/v1/items/scan", json=payload, headers=headers)
        self.assertEqual(self.client.get("/api/v1/items").json()[0]["quantity"], 2)

    def test_invalid_barcode_and_missing_item(self):
        self.assertEqual(self.add(barcode="not-an-ean").status_code, 422)
        self.assertEqual(self.add(barcode="123").status_code, 422)
        self.assertEqual(self.client.delete("/api/v1/items/3017620422003").status_code, 404)

    def test_connections_never_claim_stub_adapters_are_connected(self):
        statuses = self.client.get("/api/v1/integrations").json()
        self.assertEqual({store["id"] for store in statuses}, {"picnic", "chronodrive"})
        self.assertTrue(all(store["status"] == "not_implemented" and not store["connected"] for store in statuses))

    def test_catalog_failure_still_saves_item(self):
        self.lookup.stop()
        with patch("app.products.urlopen", side_effect=URLError("offline")):
            response = self.add()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["label"], "EAN 3017620422003")

    def test_product_lookup_prefers_french_label(self):
        with patch("app.products.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"product":{"product_name":"Milk","product_name_fr":"Lait"}}'
            self.assertEqual(product_label("3017620422003"), "Lait")


def tearDownModule():
    engine.dispose()
    temporary.cleanup()
