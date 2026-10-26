"""Offline evaluation of the installed python-picnic-api2 distribution.

Run with Python 3.13 and python-picnic-api2==2.0.1. No real account is used.
"""
import json
import unittest
from unittest.mock import Mock, patch

import requests
from python_picnic_api2 import PicnicAPI, Picnic2FARequired


def response(request, data=None, status=200, token=None, raw=None):
    result = requests.Response()
    result.status_code = status
    result.url = request.url
    result.request = request
    result._content = raw if raw is not None else json.dumps(data or {}).encode()
    result.headers["Content-Type"] = "application/json" if raw is None else "text/x-component"
    if token:
        result.headers["x-picnic-auth"] = token
    return result


class PythonPicnicEvaluation(unittest.TestCase):
    def test_french_search_uses_page_endpoint_and_returns_pack_price(self):
        client = PicnicAPI(country_code="FR", auth_token="test-token")
        def handle(request, **kwargs):
            self.assertIn("storefront-prod.fr.", request.url)
            self.assertIn("/pages/search-page-results?search_term=", request.url)
            self.assertIn("x-picnic-agent", request.headers)
            return response(request, {"body":{"child":{"type":"SELLING_UNIT_TILE", "sellingUnit":{"id":"s123", "name":"Nutella", "display_price":399, "unit_quantity":"400 g"}}}})
        with patch("requests.Session.send", side_effect=handle):
            result = client.search("Nutella")
        self.assertEqual((result.items[0].id, result.items[0].display_price, result.items[0].unit_quantity), ("s123",399,"400 g"))

    def test_login_and_sms_rotate_the_same_session(self):
        client = PicnicAPI(country_code="FR")
        def handle(request, **kwargs):
            if request.url.endswith("/user/login"):
                return response(request, {"second_factor_authentication_required":True}, token="provisional")
            self.assertEqual(request.headers["x-picnic-auth"],"provisional")
            return response(request, status=204, token="verified" if request.url.endswith("/verify") else None)
        with patch("requests.Session.send", side_effect=handle):
            with self.assertRaises(Picnic2FARequired):
                client.login("test@example.invalid","test-password")
            client.generate_2fa_code("SMS")
            client.verify_2fa_code("123456")
        self.assertEqual(client.session.auth_token,"verified")

    def test_cart_prices_and_raw_quantity_overrides_are_available(self):
        client = PicnicAPI(country_code="FR",auth_token="test-token")
        cart = {"id":"cart", "items":[{"id":"line","items":[{"id":"s123","name":"Nutella","decorators":[{"type":"QUANTITY","quantity":1}]}]}],
                "total_count":3,"total_price":1197,"checkout_total_price":1497,
                "decorator_overrides":{"line":[{"type":"QUANTITY","quantity":3}]}, "fees":[{"amount":200}], "deposit_breakdown":[{"value":100,"count":1}]}
        with patch("requests.Session.send",side_effect=lambda request,**kwargs:response(request,cart)):
            result = client.get_cart()
        self.assertEqual(result.checkout_total_price,1497)
        self.assertEqual(result.total_price,1197)
        self.assertEqual(result.raw["decorator_overrides"]["line"][0]["quantity"],3)
        # The typed article itself retains the un-overridden value; normalization is needed.
        self.assertEqual(result.items[0].items[0].decorators[0].quantity,1)
        self.assertEqual(result.raw["fees"][0]["amount"],200)

    def test_add_and_remove_return_updated_carts(self):
        client = PicnicAPI(country_code="FR",auth_token="test-token")
        calls = []
        def handle(request,**kwargs):
            calls.append((request.url, json.loads(request.body)))
            return response(request,{"id":"cart","total_price":798,"checkout_total_price":798,"items":[]})
        with patch("requests.Session.send",side_effect=handle):
            self.assertEqual(client.add_product("s123",2).checkout_total_price,798)
            self.assertEqual(client.remove_product("s123",1).id,"cart")
        self.assertTrue(calls[0][0].endswith("/cart/add_product"))
        self.assertEqual(calls[0][1],{"product_id":"s123","count":2})
        self.assertTrue(calls[1][0].endswith("/cart/remove_product"))

    def test_ean_resolver_uses_french_qr_path(self):
        client = PicnicAPI(country_code="FR",auth_token="test-token")
        calls = []
        def handle(request,**kwargs):
            calls.append(request.url)
            result = response(request)
            if "/qr/gtin/" in request.url:
                result.status_code = 302
                result.headers["Location"] = "https://picnic.app/fr/article;id=s123"
            return result
        client.get_article = Mock(return_value="resolved-article")
        with patch("requests.Session.send",side_effect=handle):
            self.assertEqual(client.get_article_by_gtin("3017620422003"),"resolved-article")
        self.assertIn("/fr/qr/gtin/3017620422003",calls[0])
        client.get_article.assert_called_once_with("s123")

    def test_rsc_response_is_not_supported(self):
        client = PicnicAPI(country_code="FR",auth_token="test-token")
        with patch("requests.Session.send",side_effect=lambda request,**kwargs:response(request,raw=b'0:["$","div",null,{}]')):
            with self.assertRaises(requests.exceptions.JSONDecodeError):
                client.search("Nutella")

    def test_http_errors_need_an_application_guard(self):
        client = PicnicAPI(country_code="FR",auth_token="test-token")
        with patch("requests.Session.send",side_effect=lambda request,**kwargs:response(request,{"message":"Service unavailable"},status=503)):
            cart = client.get_cart()
        self.assertIsNone(cart.id)
        self.assertIsNone(cart.checkout_total_price)


if __name__ == "__main__":
    unittest.main(verbosity=2)
