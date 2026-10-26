import unittest
import os
import time
import asyncio
import json
from unittest.mock import AsyncMock, patch

import test_api
from app.catalog import group_results
from app.connectors.base import ConnectorError
from app.database import engine, product_facts, targets
from app.integration_service import operate
from app.products import _product_search
from sqlalchemy import select


class CatalogTests(unittest.TestCase):
    def setUp(self):
        # The shared fixture module may already have cleaned its SQLite directory.
        if engine.dialect.name == "sqlite":
            os.makedirs(os.path.dirname(engine.url.database), exist_ok=True)
        test_api.ShoppingListTests.setUp(self)
    tearDown = test_api.ShoppingListTests.tearDown
    add = test_api.ShoppingListTests.add
    def test_catalog_search_groups_verified_eans_and_keeps_packs_separate(self):
        p = {"id":"p1","name":"Lasagne","size":"500 g","eans":["8076800376999"],"price_cents":250}
        grouped = group_results([("picnic",[p]),("chronodrive",[{**p,"id":"c1","price_cents":275}]),("openfoodfacts",[{**p,"id":"8076800376999","price_cents":None}])])
        self.assertEqual(len(grouped),1)
        self.assertEqual(len(grouped[0]["offers"]),3)
        self.assertIsNone(grouped[0]["offers"][2]["price_cents"])
        self.assertEqual(grouped[0]["offers"][0]["unit_price_cents"],500)
        grouped = group_results([("picnic",[p]),("chronodrive",[{**p,"id":"c2","eans":["12345678"],"size":"1 kg"}])])
        self.assertEqual(len(grouped),2)

    def test_directory_selection_updates_description_not_barcode_or_carts(self):
        self.add(barcode="8076800376999")
        product = {"id":"8076800376999","name":"Barilla Lasagne","size":"500 g","eans":["8076800376999"],"price_cents":None}
        with patch("app.catalog.product_search",return_value=[product]):
            result=self.client.post("/api/v1/items/8076800376999/search",json={"query":"Lasagne"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["results"][0]["offers"][0]["source"],"openfoodfacts")
        saved=self.client.post("/api/v1/items/8076800376999/identity",json={"code":product["id"]})
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json()[0],{"ean":"8076800376999","label":"Barilla Lasagne","quantity":1,"size":"500 g","source":"openfoodfacts"})
        with engine.connect() as connection:
            jobs=connection.execute(select(targets)).mappings().all()
            self.assertTrue(all(job["product_id"] is None for job in jobs))
            self.assertTrue(all(job["price_cents"] is None for job in jobs))
        self.client.delete("/api/v1/items/8076800376999")
        self.add("repeat-item",barcode="8076800376999")
        self.assertIsNone(self.client.get("/api/v1/items").json()[0]["source"])

    def test_one_store_failure_does_not_hide_other_results(self):
        self.add()
        stores=[{"id":"picnic","name":"Picnic","connected":True,"items":[]},{"id":"chronodrive","name":"Chronodrive","connected":True,"items":[]}]
        product={"id":"c1","name":"Pasta","size":"500 g","eans":[],"price_cents":200}
        async def operate(store,*args,**kwargs):
            if store=="picnic":raise ConnectorError("search_error","private upstream details")
            return [product]
        with patch("app.catalog.public_status",return_value=stores),patch("app.catalog.operate",side_effect=operate),patch("app.catalog.product_search",return_value=[]):
            response=self.client.post("/api/v1/items/3017620422003/search",json={"query":"Pasta"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["results"][0]["offers"][0]["source"],"chronodrive")
        self.assertEqual(response.json()["messages"][0]["source"],"picnic")
        self.assertNotIn("private",response.text)

    def test_queued_lookup_is_not_started_again_by_manual_search(self):
        self.add()
        stores=[{"id":"picnic","name":"Picnic","connected":True,"items":[{"ean":"3017620422003","status":"queued"}]}]
        with patch("app.catalog.public_status",return_value=stores),patch("app.catalog.operate",new_callable=AsyncMock) as operate,patch("app.catalog.product_search",return_value=[]):
            response=self.client.post("/api/v1/items/3017620422003/search",json={"query":"Pasta"})
        operate.assert_not_awaited()
        self.assertIn("automatique",response.json()["messages"][0]["message"])

    def test_identity_must_have_a_recent_product_and_existing_item(self):
        self.add()
        response=self.client.post("/api/v1/items/3017620422003/identity",json={"code":"12345678"})
        self.assertEqual(response.status_code,409)
        with engine.begin() as connection:
            connection.execute(product_facts.insert().values(ean="12345678",data={"name":"Old"},fetched_at=time.time()-90000))
        self.assertEqual(self.client.post("/api/v1/items/3017620422003/identity",json={"code":"12345678"}).status_code,409)
        self.assertEqual(self.client.post("/api/v1/items/00000000/search",json={"query":"Pasta"}).status_code,404)

    def test_store_match_repairs_unknown_title_and_preserves_quantity(self):
        test_api.ShoppingListTests.configured_store(self)
        with patch("app.main.product_label",return_value="EAN 3017620422003"):
            self.add()
        with patch("app.integration_service.adapter_for",return_value=test_api.FakeStore()):
            asyncio.run(operate("picnic"))
        item=self.client.get("/api/v1/items").json()[0]
        self.assertEqual(item["label"],"Test product")
        self.assertEqual(item["quantity"],1)
        self.assertEqual(item["ean"],"3017620422003")
        self.assertEqual(item["source"],"picnic")

    def test_off_name_search_parses_pack_and_never_adds_a_price(self):
        _product_search.cache_clear()
        with patch("app.products.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value=json.dumps({"products":[{"code":"8076800376999","product_name":"Lasagne","brands":"Barilla","quantity":"500 g"},{"code":"bad","product_name":"invalid"}]}).encode()
            result=_product_search("Lasagne",123)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]["name"],"Barilla Lasagne")
            self.assertEqual(result[0]["size"],"500 g")
            self.assertIsNone(result[0]["price_cents"])
            _product_search("Lasagne",123)
            self.assertEqual(request.call_count,1)
        _product_search.cache_clear()


def tearDownModule():
    engine.dispose()
    test_api.temporary.cleanup()


