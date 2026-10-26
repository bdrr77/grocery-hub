import base64
import hashlib
import json
import unittest

import httpx

from app.config import Settings
from app.connectors.base import ConnectorError
from app.connectors.chronodrive import ChronodriveAdapter
from app.connectors.picnic import PicnicAdapter


class PicnicProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_sms_verification_and_rotated_session(self):
        calls = []
        def handle(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/user/login"):
                data = json.loads(request.content)
                self.assertNotIn("password", data)
                self.assertEqual(data["secret"], hashlib.md5(b"password", usedforsecurity=False).hexdigest())
                return httpx.Response(200, json={"second_factor_authentication_required":True}, headers={"x-picnic-auth":"provisional"})
            self.assertIn(request.headers["x-picnic-auth"], {"provisional", "verified"})
            if request.url.path.endswith("/2fa/verify"):
                self.assertEqual(json.loads(request.content), {"otp":"123456"})
                return httpx.Response(204, headers={"x-picnic-auth":"verified"})
            return httpx.Response(200, json={})
        adapter = PicnicAdapter(Settings(picnic_username="test", picnic_password="password"), transport=httpx.MockTransport(handle))
        try:
            with self.assertRaises(ConnectorError) as error:
                await adapter.health()
            self.assertEqual(error.exception.status, "otp_required")
            self.assertFalse(any("generate" in path for path in calls))
            await adapter.send_code()
            await adapter.verify_code("123456")
            self.assertEqual(adapter.session["token"], "verified")
            self.assertFalse(adapter.session["otp_required"])
            self.assertTrue(await adapter.health())
        finally:
            await adapter.close()

    async def test_cart_timeout_retry_reads_current_quantity_without_double_add(self):
        quantity, writes = 0, []
        def handle(request):
            nonlocal quantity
            if request.url.path.endswith("/cart"):
                lines = [{"type":"ORDER_LINE", "items":[{"id":"p1", "type":"ORDER_ARTICLE"} for _ in range(quantity)]}] if quantity else []
                return httpx.Response(200, json={"items":lines})
            data = json.loads(request.content)
            writes.append(data)
            quantity += data["count"]
            raise httpx.ReadTimeout("response lost", request=request)
        adapter = PicnicAdapter(Settings(), {"token":"valid"}, httpx.MockTransport(handle))
        try:
            with self.assertRaises(ConnectorError):
                await adapter.set_quantity("p1", 2)
            await adapter.set_quantity("p1", 2)
            self.assertEqual(quantity, 2)
            self.assertEqual(len(writes), 1)
        finally:
            await adapter.close()

    async def test_search_does_not_invent_ean_matches(self):
        def handle(request):
            return httpx.Response(200, json=[{"items":[{"type":"SINGLE_ARTICLE", "id":"p1", "name":"Nutella", "price":399, "unit_quantity":"400 g"}]}])
        adapter = PicnicAdapter(Settings(), {"token":"valid"}, httpx.MockTransport(handle))
        try:
            self.assertEqual(len(await adapter.search("Nutella")), 1)
            self.assertEqual(await adapter.search_by_ean("3017620422003"), [])
        finally:
            await adapter.close()

    async def test_http_errors_never_expose_upstream_secrets(self):
        for status, expected in [(401,"auth_error"),(403,"access_denied"),(429,"rate_limited"),(500,"upstream_error")]:
            adapter = PicnicAdapter(Settings(), {"token":"private"}, httpx.MockTransport(lambda request: httpx.Response(status, text="secret-token private-password")))
            try:
                with self.assertRaises(ConnectorError) as error:
                    await adapter.health()
                self.assertEqual(error.exception.status, expected)
                self.assertNotIn("secret", str(error.exception))
            finally:
                await adapter.close()


class ChronodriveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_pkce_product_shape_and_signed_quantity_deltas(self):
        quantity = 2
        writes = []
        challenge = None
        def handle(request):
            nonlocal quantity, challenge
            path = request.url.path
            if request.url.host == "www.chronodrive.com":
                return httpx.Response(200, text='CLIENT_ID:"public-client",CHR_API_CUSTOMERS_APIKEY:"customer-key",CHR_API_PRODUCTS_APIKEY:"product-key",CHR_API_CARTS_APIKEY:"cart-key"')
            if path.endswith("/password/login"):
                self.assertEqual(json.loads(request.content)["email"], "test@example.invalid")
                return httpx.Response(200, json={"tkn":"ticket"})
            if path.endswith("/oauth/authorize"):
                challenge = request.url.params["code_challenge"]
                self.assertEqual(request.url.params["code_challenge_method"], "S256")
                return httpx.Response(200, json={"code":"code", "state":request.url.params["state"]})
            if path.endswith("/oauth/token"):
                data = json.loads(request.content)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")
                self.assertEqual(digest, challenge)
                return httpx.Response(200, json={"access_token":"token", "refresh_token":"refresh", "expires_in":3600})
            self.assertEqual(request.headers["Authorization"], "Bearer token")
            self.assertEqual(request.headers["x-chronodrive-site-id"], "1234")
            if path == "/v1/customers/me":
                return httpx.Response(200, json={"id":"customer"})
            if path == "/v1/customers/me/carts":
                self.assertEqual(request.headers["x-api-key"], "customer-key")
                return httpx.Response(200, json={"content":[{"id":"cart", "isOrdered":False, "items":[{"product":{"id":"p1"},"quantity":quantity}] if quantity else []}]})
            if path == "/v1/products":
                self.assertEqual(request.headers["x-api-key"], "product-key")
                return httpx.Response(200, json={"content":[{"id":"p1", "eans":["3017620422003"], "labels":{"productLabel":"Nutella", "unitQuantityLabel":"400 g"}, "prices":{"defaultPrice":3.99}}]})
            self.assertEqual(request.headers["x-api-key"], "cart-key")
            if request.method == "DELETE":
                quantity = 0
                return httpx.Response(200)
            delta = json.loads(request.content)["content"][0]["quantity"]
            writes.append(delta)
            quantity += delta
            return httpx.Response(200, json={"content":[{"returnType":"SUCCESS"}]})
        adapter = ChronodriveAdapter(Settings(chronodrive_email="test@example.invalid", chronodrive_password="password", chronodrive_store_id="1234"), transport=httpx.MockTransport(handle))
        try:
            self.assertTrue(await adapter.health())
            products = await adapter.search_by_ean("3017620422003")
            self.assertEqual(products[0].price_cents, 399)
            self.assertEqual(products[0].name, "Nutella")
            self.assertEqual(products[0].size, "400 g")
            await adapter.set_quantity("p1", 3)
            await adapter.set_quantity("p1", 3)
            await adapter.set_quantity("p1", 1)
            self.assertEqual(writes, [1, -2])
            await adapter.remove_item("p1")
            self.assertEqual(quantity, 0)
        finally:
            await adapter.close()

    async def test_partial_stock_response_is_not_success(self):
        def handle(request):
            if request.method == "GET":
                return httpx.Response(200, json={"content":[{"id":"cart", "isOrdered":False, "items":[]}]})
            return httpx.Response(207, json={"content":[{"returnType":"NOT_ENOUGH_STOCK", "quantity":1}]})
        adapter = ChronodriveAdapter(Settings(chronodrive_store_id="1234"), {"token":"valid", "config":{"customers":"c","carts":"w"}}, httpx.MockTransport(handle))
        try:
            with self.assertRaises(ConnectorError) as error:
                await adapter.set_quantity("p1", 5)
            self.assertEqual(error.exception.status, "quantity_mismatch")
        finally:
            await adapter.close()

    async def test_does_not_use_an_already_ordered_cart(self):
        adapter = ChronodriveAdapter(Settings(chronodrive_store_id="1234"), {"token":"valid", "config":{"customers":"c"}}, httpx.MockTransport(lambda request:httpx.Response(200,json={"content":[{"id":"ordered", "isOrdered":True, "items":[]}]})))
        try:
            with self.assertRaises(ConnectorError) as error:
                await adapter.get_cart()
            self.assertEqual(error.exception.status, "no_cart")
        finally:
            await adapter.close()
