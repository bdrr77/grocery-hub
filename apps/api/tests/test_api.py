import os
import asyncio
import json
import tempfile
import unittest
import httpx
from unittest.mock import patch
from urllib.error import URLError

temporary = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///" + temporary.name.replace("\\", "/") + "/test.db")
os.environ["SCANNER_DEVICE_TOKENS"] = "test:secret,other:other-secret"

from fastapi.testclient import TestClient
from app.main import app
from app.database import cart_snapshots, ean_lookups, engine, integrations, metadata, targets
from app.config import settings
from app.connectors.base import ConnectorError, Product
from app.connectors.picnic import PicnicAdapter
from app.integration_service import acquire, operate, public_status, save_state
from sqlalchemy import select, update
from app.products import product_label


class ShoppingListTests(unittest.TestCase):
    def setUp(self):
        self.credentials = patch.multiple(settings, picnic_username="", picnic_password="", chronodrive_username="", chronodrive_email="", chronodrive_password="", chronodrive_store_id="")
        self.credentials.start()
        metadata.drop_all(engine)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.lookup = patch("app.main.product_label", return_value="Test product")
        self.lookup.start()
        self.off_fallback = patch("app.matching.product_details",return_value=None)
        self.off_fallback.start()

    def tearDown(self):
        self.credentials.stop()
        self.lookup.stop()
        self.off_fallback.stop()
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

    def test_named_item_retry_and_quantity_controls_queue_changes(self):
        payload = {"label":"Pâtes", "event_id":"named-event-1"}
        first = self.client.post("/api/v1/items/named",json=payload)
        self.assertEqual(first.status_code,200)
        key = first.json()[0]["ean"]
        self.assertTrue(key.startswith("n"))
        self.client.post("/api/v1/items/named",json=payload)
        self.assertEqual(len(self.client.get("/api/v1/items").json()),1)
        changed = self.client.patch(f"/api/v1/items/{key}",json={"quantity":3})
        self.assertEqual(changed.json()[0]["quantity"],3)
        self.assertEqual(self.rows()[0]["quantity"],3)
        self.assertEqual(self.client.patch(f"/api/v1/items/{key}",json={"quantity":0}).status_code,422)
        self.assertEqual(self.client.delete(f"/api/v1/items/{key}").status_code,204)
        self.assertEqual(self.rows()[0]["quantity"],0)

    def test_snapshot_exposes_native_total_separately_from_associated_list(self):
        self.configured_store()
        self.add()
        with patch("app.integration_service.adapter_for",return_value=FakeStore()):
            asyncio.run(operate("picnic"))
        pricing = public_status()[0]["pricing"]
        self.assertEqual(pricing["list_total_cents"],399)
        self.assertEqual(pricing["cart_total_cents"],1995)
        self.assertEqual(pricing["other_cart_items"],1)

    def test_named_item_requires_selection_even_when_store_can_match_eans(self):
        self.configured_store()
        self.client.post("/api/v1/items/named",json={"label":"Pâtes","event_id":"named-event-2"})
        store = FakeStore()
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        self.assertEqual(self.rows()[0]["status"],"unmatched")
        self.assertNotIn("p1",store.cart)

    def test_connections_require_configuration(self):
        statuses = self.client.get("/api/v1/integrations").json()
        self.assertEqual({store["id"] for store in statuses}, {"picnic", "chronodrive"})
        self.assertTrue(all(store["status"] == "not_configured" and not store["connected"] for store in statuses))

    def configured_store(self):
        settings.picnic_username = "test@example.invalid"
        settings.picnic_password = "test-password"

    def rows(self):
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(targets).where(targets.c.store == "picnic")).mappings()]

    def test_list_mutation_and_outbox_are_idempotent(self):
        self.add()
        self.add()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["quantity"], 1)
        self.assertEqual(self.rows()[0]["revision"], 1)
        self.add("new-event-2")
        self.assertEqual(self.rows()[0]["quantity"], 2)
        self.assertEqual(self.rows()[0]["revision"], 2)
        self.client.delete("/api/v1/items/3017620422003")
        self.assertEqual(self.rows()[0]["quantity"], 0)

    def test_exact_match_sync_retry_and_removal_preserve_unrelated_cart_items(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        with patch("app.integration_service.adapter_for", return_value=store):
            asyncio.run(operate("picnic"))
            self.assertEqual(store.cart, {"unrelated": 4, "p1": 1})
            self.assertEqual(self.rows()[0]["status"], "synced")
            self.client.post("/api/v1/integrations/picnic/sync")
            asyncio.run(operate("picnic"))
            self.assertEqual(store.cart["p1"], 1)
            self.client.delete("/api/v1/items/3017620422003")
            asyncio.run(operate("picnic"))
            self.assertEqual(store.cart, {"unrelated": 4})

    def test_ambiguous_ean_needs_manual_choice(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        store.matches = []
        with patch("app.integration_service.adapter_for", return_value=store):
            asyncio.run(operate("picnic"))
            self.assertEqual(self.rows()[0]["status"], "unmatched")
            self.assertNotIn("p1", store.cart)
            found = self.client.post("/api/v1/integrations/picnic/search", json={"ean":"3017620422003", "query":"Test"})
            self.assertEqual(found.status_code, 200)
            invalid = self.client.post("/api/v1/integrations/picnic/match", json={"ean":"3017620422003", "product_id":"invented"})
            self.assertEqual(invalid.status_code, 409)
            selected = self.client.post("/api/v1/integrations/picnic/match", json={"ean":"3017620422003", "product_id":"p1"})
            self.assertEqual(selected.status_code, 202)
            asyncio.run(operate("picnic"))
            self.assertEqual(store.cart["p1"], 1)

    def test_worker_failure_keeps_list_and_hides_session_secrets(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        async def fail():
            raise ConnectorError("auth_error", "Connexion refusée.")
        store.health = fail
        with patch("app.integration_service.adapter_for", return_value=store):
            asyncio.run(operate("picnic", background=True))
        self.assertEqual(len(self.client.get("/api/v1/items").json()), 1)
        state = self.client.get("/api/v1/integrations").json()[0]
        self.assertEqual(state["status"], "auth_error")
        self.assertNotIn("secret", str(state))
        self.assertNotIn("session", state)

    def test_connector_lease_blocks_concurrent_workers(self):
        owner, _ = acquire("picnic")
        try:
            with self.assertRaises(ConnectorError) as result:
                acquire("picnic")
            self.assertEqual(result.exception.status, "busy")
        finally:
            save_state("picnic", owner, lease_owner="", lease_until=0)

    def test_new_revision_during_sync_remains_queued(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        original = store.set_quantity
        async def concurrent(product_id, quantity):
            self.add("concurrent-event")
            await original(product_id, quantity)
        store.set_quantity = concurrent
        with patch("app.integration_service.adapter_for", return_value=store):
            asyncio.run(operate("picnic"))
        self.assertEqual(self.rows()[0]["quantity"], 2)
        self.assertEqual(self.rows()[0]["status"], "queued")

    def test_stale_worker_does_not_claim_connected(self):
        self.configured_store()
        with patch("app.integration_service.adapter_for", return_value=FakeStore()):
            asyncio.run(operate("picnic"))
        with engine.begin() as connection:
            connection.execute(update(integrations).where(integrations.c.store == "picnic").values(checked_at=1))
        self.assertEqual(public_status()[0]["status"], "stale")

    def test_catalog_failure_does_not_disconnect_healthy_account(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        async def fail_search(query):
            raise ConnectorError("search_error", "Catalogue indisponible.")
        store.search = fail_search
        with patch("app.integration_service.adapter_for", return_value=store):
            response = self.client.post("/api/v1/integrations/picnic/search", json={"ean":"3017620422003","query":"Nutella"})
        self.assertEqual(response.status_code,502)
        self.assertTrue(public_status()[0]["connected"])

    def test_search_suggestions_prefer_chronodrive_pack_then_off_then_user(self):
        self.add()
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="chronodrive").values(product_id="c1",product_name="Nutella",status="synced"))
            connection.execute(cart_snapshots.insert().values(store="chronodrive",fingerprint="test",fetched_at=1,data={"lines":[{"id":"c1","name":"Nutella","size":"400 g","quantity":3}]}))
        with patch("app.matching.product_details",return_value={"name":"Pâte à tartiner","brand":"Ferrero","size":"400 g"}) as lookup:
            response = self.client.get("/api/v1/items/3017620422003/search-suggestions")
            self.assertEqual(response.status_code,200)
            suggestions = response.json()["suggestions"]
            self.assertEqual([hint["source"] for hint in suggestions],["chronodrive","openfoodfacts","user"])
            self.assertEqual(suggestions[0]["query"],"Nutella 400 g")
            self.assertNotIn("3",suggestions[0]["query"])
            self.assertEqual(suggestions[1]["query"],"Ferrero Pâte à tartiner 400 g")
            self.client.get("/api/v1/items/3017620422003/search-suggestions")
            self.assertEqual(lookup.call_count,1)

    def test_ean_attempt_is_recorded_and_not_inferred_from_name_search(self):
        self.configured_store()
        self.add()
        store = FakeStore()
        store.matches = []
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        job = public_status()[0]["items"][0]
        self.assertEqual(job["ean_lookup"]["status"],"not_found")
        self.assertIsNone(job["product_id"])

    def test_search_suggestions_fall_back_to_user_when_off_is_unavailable(self):
        self.add()
        with patch("app.matching.product_details",return_value=None) as lookup:
            first = self.client.get("/api/v1/items/3017620422003/search-suggestions")
            self.client.get("/api/v1/items/3017620422003/search-suggestions")
        self.assertEqual([hint["source"] for hint in first.json()["suggestions"]],["user"])
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(first.json()["suggestions"][0]["query"],"Test product")
        self.assertEqual(self.client.get("/api/v1/items/00000000/search-suggestions").status_code,404)

    def test_old_skipped_ean_is_requeued_only_until_a_real_attempt(self):
        from app.integration_service import initialize
        self.add()
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="picnic").values(status="unmatched"))
        initialize()
        self.assertEqual(self.rows()[0]["status"],"queued")
        self.configured_store()
        store = FakeStore()
        store.matches = []
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        initialize()
        self.assertEqual(self.rows()[0]["status"],"unmatched")

    def test_unknown_store_and_otp_input_validation(self):
        self.assertEqual(self.client.post("/api/v1/integrations/unknown/connect").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/integrations/picnic/verify-code", json={"code":"invalid"}).status_code, 422)

    def test_sms_session_survives_api_requests_and_bad_code_can_be_retried(self):
        self.configured_store()
        calls = []
        async def run(adapter, action, **values):
            calls.append(action)
            if action == "login":
                adapter.session.update(token="private-provisional",otp_required=True)
            elif action == "verify-code":
                if values["code"] == "000000":
                    raise ConnectorError("upstream_error","Code refus?.")
                adapter.session.update(token="private-verified",otp_required=False)
            elif action == "cart":
                return {"lines":[],"total_cents":0,"currency":"EUR"}
            return None
        with patch("app.connectors.picnic.PicnicAdapter._run",new=run):
            self.assertEqual(self.client.post("/api/v1/integrations/picnic/connect").status_code, 400)
            self.assertEqual(self.client.post("/api/v1/integrations/picnic/send-code").status_code, 200)
            invalid = self.client.post("/api/v1/integrations/picnic/verify-code", json={"code":"000000"})
            self.assertEqual(invalid.status_code, 400)
            self.assertNotIn("private", invalid.text)
            self.assertEqual(public_status()[0]["status"], "otp_required")
            self.assertEqual(self.client.post("/api/v1/integrations/picnic/verify-code", json={"code":"123456"}).status_code, 200)
            asyncio.run(operate("picnic"))
        self.assertEqual(calls.count("login"), 1)
        self.assertTrue(public_status()[0]["connected"])
        self.assertNotIn("private", str(public_status()))

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


class FakeStore:
    def __init__(self):
        self.session = {"token": "server-secret-token"}
        self.cart = {"unrelated": 4}
        self.matches = [Product("p1", "Test product", ["3017620422003"], 399, "400 g")]

    async def health(self):
        return True

    async def close(self):
        pass

    async def search_by_ean(self, ean):
        return self.matches

    async def search(self, query):
        return [Product("p1", "Test product", [], 399, "400 g")]

    async def set_quantity(self, product_id, quantity):
        if quantity:
            self.cart[product_id] = quantity
        else:
            self.cart.pop(product_id, None)

    async def get_cart_details(self):
        return {"lines":[{"id":key,"quantity":quantity,"line_total_cents":quantity*399,"name":key,"size":"400 g"} for key,quantity in self.cart.items()],
                "total_cents":sum(self.cart.values())*399,"currency":"EUR","deposit_cents":0,"fees_cents":0,"discount_cents":0}


def tearDownModule():
    engine.dispose()
    temporary.cleanup()
