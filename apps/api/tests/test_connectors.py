import base64
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from app.config import Settings
from app.connectors.base import ConnectorError
from app.connectors.chronodrive import ChronodriveAdapter
from app.connectors.picnic import PicnicAdapter


class PicnicProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_package_login_sms_and_snapshot_share_session(self):
        calls = []
        async def communicate(payload):
            data = json.loads(payload)
            calls.append(data)
            session = {"token":data.get("token"), "otp_required":data.get("otp_required",False)}
            result = None
            if data["action"] == "login":
                session = {"token":"provisional", "otp_required":True}
            elif data["action"] == "verify-code":
                session = {"token":"verified", "otp_required":False}
            elif data["action"] == "cart":
                result = {"lines":[], "total_cents":0, "currency":"EUR"}
            return json.dumps({"data":result, "session":session}).encode(), b""
        process = Mock(returncode=0)
        process.communicate = communicate
        adapter = PicnicAdapter(Settings(picnic_username="test",picnic_password="private-password"))
        try:
            with patch("app.connectors.picnic.asyncio.create_subprocess_exec",new_callable=AsyncMock,return_value=process) as spawn:
                with self.assertRaises(ConnectorError) as error:
                    await adapter.health()
                self.assertEqual(error.exception.status,"otp_required")
                await adapter.send_code()
                await adapter.verify_code("123456")
                self.assertEqual(adapter.session["token"],"verified")
                self.assertEqual((await adapter.get_cart_details())["total_cents"],0)
                self.assertNotIn("private-password",str(spawn.call_args_list))
                self.assertEqual([call["action"] for call in calls],["login","send-code","verify-code","health","cart"])
        finally:
            await adapter.close()

    async def test_search_does_not_invent_ean_matches(self):
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(json.dumps({"data":[{"id":"s123","name":"Nutella","eans":[],"price_cents":399,"size":"400 g"}]}).encode(),b""))
        adapter = PicnicAdapter(Settings(),{"token":"private-valid"})
        try:
            with patch("app.connectors.picnic.asyncio.create_subprocess_exec",new_callable=AsyncMock,return_value=process) as spawn:
                result = await adapter.search("Nutella")
                self.assertEqual(result[0].price_cents,399)
                self.assertEqual(await adapter.search_by_ean("3017620422003"),[])
                self.assertEqual(spawn.await_count,1)
                self.assertNotIn("private-valid",str(spawn.call_args))
                self.assertEqual(json.loads(process.communicate.call_args.args[0])["token"],"private-valid")
        finally:
            await adapter.close()

    async def test_errors_are_sanitized_and_auth_failure_expires_session(self):
        for status, expected in [(401,"auth_error"),(403,"access_denied"),(429,"rate_limited"),(404,"search_error")]:
            process = Mock(returncode=1)
            process.communicate = AsyncMock(return_value=(json.dumps({"error":"request_failed","http_status":status}).encode(),b"private-error"))
            adapter = PicnicAdapter(Settings(),{"token":"private"})
            try:
                with patch("app.connectors.picnic.asyncio.create_subprocess_exec",new_callable=AsyncMock,return_value=process):
                    with self.assertRaises(ConnectorError) as error:
                        await adapter.search("Nutella")
                    self.assertEqual(error.exception.status,expected)
                    self.assertNotIn("private",str(error.exception))
                    if status == 401:
                        self.assertNotIn("token",adapter.session)
            finally:
                await adapter.close()

    async def test_missing_runtime_returns_a_controlled_error(self):
        adapter = PicnicAdapter(Settings(),{"token":"private"})
        try:
            with patch("app.connectors.picnic.asyncio.create_subprocess_exec",new_callable=AsyncMock,side_effect=FileNotFoundError("node")):
                with self.assertRaises(ConnectorError) as error:
                    await adapter.search("Nutella")
                self.assertEqual(error.exception.status,"search_error")
        finally:
            await adapter.close()


class ChronodriveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_reads_current_nuxt_auth_key_and_legacy_alias(self):
        for key in ["API_AUTH_CLIENT_ID", "CLIENT_ID"]:
            page = f'window.__NUXT__={{config:{{public:{{{key}:"web-client",CHR_API_CUSTOMERS_APIKEY:"c",CHR_API_PRODUCTS_APIKEY:"p",CHR_API_CARTS_APIKEY:"w"}}}}}}'
            adapter = ChronodriveAdapter(Settings(), transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page)))
            try:
                self.assertEqual(await adapter.config(), {"client_id":"web-client","customers":"c","products":"p","carts":"w"})
            finally:
                await adapter.close()

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
