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
from sqlalchemy import select, update


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
        self.assertEqual(saved.json()[0],{"ean":"8076800376999","label":"Barilla Lasagne","quantity":1,"size":"500 g","source":"openfoodfacts","brand":None})
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
            self.assertEqual(result[0]["brand"],"Barilla")
            _product_search("Lasagne",123)
            self.assertEqual(request.call_count,1)
        _product_search.cache_clear()

    def test_selecting_off_replaces_ean_and_keeps_shopping_quantity(self):
        self.add(barcode="00000000")
        self.client.patch("/api/v1/items/00000000",json={"quantity":3})
        with engine.begin() as connection:
            connection.execute(product_facts.insert().values(ean="8076800376999",data={"name":"Barilla Lasagne","size":"500 g"},fetched_at=time.time()))
        response=self.client.post("/api/v1/items/00000000/identity",json={"code":"8076800376999"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()[0]["ean"],"8076800376999")
        self.assertEqual(response.json()[0]["quantity"],3)
        with engine.connect() as connection:
            self.assertFalse(connection.execute(select(targets).where(targets.c.ean=="00000000")).first())
            self.assertEqual(len(connection.execute(select(targets).where(targets.c.ean=="8076800376999")).all()),2)

    def test_store_result_rekeys_to_its_verified_ean(self):
        self.add(barcode="00000000")
        product={"id":"c1","name":"Lasagne","size":"500 g","eans":["8076800376999"],"price_cents":250}
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="chronodrive").values(candidates=[product]))
        result=self.client.post("/api/v1/items/00000000/select",json={"source":"chronodrive","product_id":"c1"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()[0]["ean"],"8076800376999")
        with engine.connect() as connection:
            target=connection.execute(select(targets).where(targets.c.store=="chronodrive",targets.c.ean=="8076800376999")).mappings().one()
            self.assertEqual(target["product_id"],"c1")

    def test_refresh_bypasses_negative_off_cache_and_resolves_connected_shop(self):
        test_api.ShoppingListTests.configured_store(self)
        with patch("app.main.product_label",return_value="EAN 3017620422003"):
            self.add()
        with patch("app.integration_service.adapter_for",return_value=test_api.FakeStore()):
            asyncio.run(operate("picnic"))
        with engine.begin() as connection:
            connection.execute(product_facts.insert().values(ean="3017620422003",data={},fetched_at=time.time()))
            connection.execute(update(targets).where(targets.c.store=="picnic").values(product_id=None,status="unmatched"))
        details={"name":"Nutella","brand":"Ferrero","size":"400 g"}
        with patch("app.catalog.product_details",return_value=details) as lookup,patch("app.integration_service.adapter_for",return_value=test_api.FakeStore()):
            result=self.client.post("/api/v1/items/3017620422003/refresh",json={})
        self.assertEqual(result.status_code,200)
        lookup.assert_called_once_with("3017620422003")
        self.assertEqual(self.client.get("/api/v1/items").json()[0]["label"],"Nutella")
        with engine.connect() as connection:
            target=connection.execute(select(targets).where(targets.c.store=="picnic")).mappings().one()
            self.assertEqual(target["product_id"],"p1")
            self.assertEqual(target["status"],"synced")

    def test_refresh_tries_barcode_then_fresh_facts_then_automatic_name_search(self):
        test_api.ShoppingListTests.configured_store(self)
        self.add()
        store=test_api.FakeStore()
        store.matches=[]
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        calls=[]
        async def barcode(ean):
            calls.append(("ean",ean))
            return []
        def facts(ean):
            calls.append(("off",ean))
            return {"name":"Lasagne","brand":"Barilla","size":"500 g"}
        async def search(query):
            calls.append(("search",query))
            return store.matches
        store.search_by_ean=barcode
        store.search=search
        with patch("app.catalog.product_details",side_effect=facts),patch("app.integration_service.adapter_for",return_value=store):
            result=self.client.post("/api/v1/items/3017620422003/refresh",json={})
        self.assertEqual(result.status_code,200)
        self.assertEqual(calls,[("ean","3017620422003"),("off","3017620422003"),("search","Barilla Lasagne 500 g"),("search","Lasagne 500 g"),("search","Lasagne")])

    def test_deletion_invalidates_snapshot_and_tracks_removal_until_confirmed(self):
        from app.integration_service import public_status
        test_api.ShoppingListTests.configured_store(self)
        self.add()
        store=test_api.FakeStore()
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
            self.assertEqual(public_status()[0]["pricing"]["cart_total_cents"],1995)
            self.client.delete("/api/v1/items/3017620422003")
            status=public_status()[0]
            self.assertEqual(status["items"],[])
            self.assertTrue(status["pricing"]["updating"])
            self.assertIsNone(status["pricing"]["cart_total_cents"])
            asyncio.run(operate("picnic"))
        status=public_status()[0]
        self.assertFalse(status["pricing"]["updating"])
        self.assertEqual(status["pricing"]["cart_total_cents"],1596)
        self.assertEqual(status["pricing"]["list_total_cents"],0)
        self.assertEqual(store.cart,{"unrelated":4})

    def test_new_card_exposes_off_brand_without_a_second_lookup(self):
        self.lookup.stop()
        with patch("app.products.product_details",return_value={"name":"Lasagne","brand":"Barilla","size":"500 g"}) as lookup:
            response=self.add(barcode="8076800376999")
            self.assertEqual(response.json()[0]["brand"],"Barilla")
            self.assertEqual(self.client.get("/api/v1/items").json()[0]["brand"],"Barilla")
            lookup.assert_called_once_with("8076800376999")

    def test_card_falls_back_to_matched_store_brand(self):
        self.add()
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="picnic").values(product_id="p1",candidates=[{"id":"p1","brand":"Ferrero"}]))
        self.assertEqual(self.client.get("/api/v1/items").json()[0]["brand"],"Ferrero")

    def test_new_ean_automatically_searches_off_equivalents_without_refresh(self):
        test_api.ShoppingListTests.configured_store(self)
        self.add(barcode="3256224060963")
        store=test_api.FakeStore()
        store.matches=[]
        queries=[]
        async def search(query):
            queries.append(query)
            return [test_api.Product("p2","Equivalent",[],250,"500 g")]
        store.search=search
        details={"name":"Produit U","brand":"U","description":"Produit comparable","size":"500 g"}
        with patch("app.matching.product_details",return_value=details),patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        self.assertEqual(queries,["Produit U 500 g"])
        result=self.client.get("/api/v1/items/3256224060963/equivalents")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["results"][0]["offers"][0]["id"],"p2")
        self.assertNotIn("p2",store.cart)
        selected=self.client.post("/api/v1/items/3256224060963/select",json={"source":"picnic","product_id":"p2"})
        self.assertEqual(selected.status_code,200)
        self.assertEqual(selected.json()[0]["label"],"Equivalent")
        self.assertEqual(selected.json()[0]["ean"],"3256224060963")

    def test_no_off_product_stops_automatic_fallback(self):
        test_api.ShoppingListTests.configured_store(self)
        self.add()
        store=test_api.FakeStore()
        store.matches=[]
        store.search=AsyncMock()
        with patch("app.matching.product_details",return_value=None),patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        store.search.assert_not_awaited()
        self.assertEqual(self.client.get("/api/v1/items/3017620422003/equivalents").json()["results"],[])

    def test_equivalent_search_relaxes_a_retailer_brand_to_generic_description(self):
        from app.matching import search_equivalents
        store=test_api.FakeStore()
        calls=[]
        async def search(query):
            calls.append(query)
            return [] if len(calls)==1 else store.matches
        store.search=search
        products=asyncio.run(search_equivalents(store,{"name":"Produit U","brand":"U","description":"Comte AOP","size":"200 g"}))
        self.assertEqual(calls,["Produit U 200 g","Comte AOP 200 g"])
        self.assertEqual(len(products),1)

    def test_tissue_barcode_uses_universal_product_lookup_and_unbranded_query(self):
        from app.products import product_details
        from app.matching import equivalent_queries
        with patch("app.products.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value=json.dumps({"product":{"product_name":"U douceur 110 mouchoirs blanc","brands":"U","quantity":"110"}}).encode()
            details=product_details("3256224060963")
        self.assertIn("product_type=all",request.call_args.args[0].full_url)
        self.assertEqual(equivalent_queries(details)[-1],"mouchoirs")

    def test_second_shop_can_have_a_different_equivalent_without_losing_first(self):
        self.add(barcode="3256224060963")
        first={"id":"p2","name":"Tissues A","brand":"Brand A","eans":[],"size":"100 pieces","price_cents":200}
        second={"id":"c2","name":"Tissues B","brand":"Brand B","eans":["12345678"],"size":"100 pieces","price_cents":210}
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="picnic").values(candidates=[first]))
            connection.execute(update(targets).where(targets.c.store=="chronodrive").values(candidates=[second]))
            connection.execute(product_facts.insert().values(ean="3256224060963",data={"brand":"U"},fetched_at=time.time()))
        self.assertEqual(self.client.post("/api/v1/items/3256224060963/select",json={"source":"picnic","product_id":"p2"}).status_code,200)
        result=self.client.post("/api/v1/items/3256224060963/select",json={"source":"chronodrive","product_id":"c2"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()[0]["label"],"Tissues A")
        self.assertEqual(result.json()[0]["brand"],"Brand A")
        with engine.connect() as connection:
            jobs=connection.execute(select(targets)).mappings().all()
        self.assertEqual({job["product_id"] for job in jobs},{"p2","c2"})

    def test_named_addition_and_refresh_search_stores_without_off(self):
        test_api.ShoppingListTests.configured_store(self)
        item=self.client.post("/api/v1/items/named",json={"label":"mouchoirs","event_id":"named-search-1"}).json()[0]
        store=test_api.FakeStore()
        original=store.search
        store.search=AsyncMock(side_effect=original)
        with patch("app.integration_service.adapter_for",return_value=store),patch("app.matching.product_details") as off:
            asyncio.run(operate("picnic"))
        off.assert_not_called()
        store.search.assert_awaited_once_with("mouchoirs")
        result=self.client.get(f"/api/v1/items/{item['ean']}/equivalents").json()
        self.assertEqual(result["results"][0]["offers"][0]["id"],"p1")
        self.assertNotIn("p1",store.cart)
        with patch("app.integration_service.adapter_for",return_value=store),patch("app.catalog.product_details") as off:
            refreshed=self.client.post(f"/api/v1/items/{item['ean']}/refresh",json={})
        self.assertEqual(refreshed.status_code,200)
        off.assert_not_called()
        self.assertEqual(refreshed.json()["results"][0]["offers"][0]["id"],"p1")

    def test_refresh_without_off_metadata_stops_and_clears_old_candidates(self):
        test_api.ShoppingListTests.configured_store(self)
        self.add()
        store=test_api.FakeStore()
        store.matches=[]
        with patch("app.integration_service.adapter_for",return_value=store):
            asyncio.run(operate("picnic"))
        with engine.begin() as connection:
            connection.execute(update(targets).where(targets.c.store=="picnic").values(candidates=[{"id":"old","name":"Old","eans":[]}]))
        store.search=AsyncMock()
        with patch("app.catalog.product_details",return_value=None),patch("app.integration_service.adapter_for",return_value=store):
            response=self.client.post("/api/v1/items/3017620422003/refresh",json={})
        self.assertEqual(response.json()["results"],[])
        store.search.assert_not_awaited()
        self.assertEqual(self.client.get("/api/v1/items/3017620422003/equivalents").json()["results"],[])


def tearDownModule():
    engine.dispose()
    test_api.temporary.cleanup()


