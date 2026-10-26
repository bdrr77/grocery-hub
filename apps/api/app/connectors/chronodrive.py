"""Chronodrive web OAuth/PKCE and product/cart protocols."""
import base64
import hashlib
import re
import secrets
import time

from .base import ConnectorError, Product, StoreAdapter, cents

WEB = "https://www.chronodrive.com"
CONNECT = "https://connect.chronodrive.com"
API = "https://api.chronodrive.com/v1"
SCOPE = "openid profile email phone full_write offline_access"


class ChronodriveAdapter(StoreAdapter):
    name = "chronodrive"

    async def config(self):
        if self.session.get("config"):
            return self.session["config"]
        response = await self.request("GET", WEB)
        text = response.text.replace('\\"', '"')
        config = {}
        keys = {"client_id": ("API_AUTH_CLIENT_ID", "CLIENT_ID"), "customers": ("CHR_API_CUSTOMERS_APIKEY",), "products": ("CHR_API_PRODUCTS_APIKEY",), "carts": ("CHR_API_CARTS_APIKEY",)}
        for field, aliases in keys.items():
            match = next((match for key in aliases if (match := re.search(r'\b' + key + r'''["':\s]+([A-Za-z0-9_-]+)''', text))), None)
            if not match:
                raise ConnectorError("protocol_error", f"Configuration publique Chronodrive introuvable ({field}). Le site a peut-être changé.")
            config[field] = match[1]
        self.session["config"] = config
        return config

    async def login(self):
        config = await self.config()
        response = await self.request("POST", CONNECT + "/identity/v1/password/login", json={
            "client_id": config["client_id"], "scope": SCOPE,
            "email": self.settings.chronodrive_email or self.settings.chronodrive_username,
            "password": self.settings.chronodrive_password,
        })
        ticket = self.json(response).get("tkn")
        if not ticket:
            raise ConnectorError("auth_error", "Chronodrive n’a pas fourni de session. Vérifiez vos identifiants.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(24)
        response = await self.request("GET", CONNECT + "/oauth/authorize", headers={"Origin": WEB, "Referer": WEB + "/"}, params={
            "client_id": config["client_id"], "response_type": "code", "redirect_uri": WEB,
            "scope": SCOPE, "response_mode": "web_message", "prompt": "none",
            "nonce": secrets.token_urlsafe(24), "state": state, "persistent": "true",
            "code_challenge": challenge, "code_challenge_method": "S256", "tkn": ticket,
        })
        code = re.search(r'"code"\s*:\s*"([^"\\]+)"', response.text)
        returned_state = re.search(r'"state"\s*:\s*"([^"\\]+)"', response.text)
        if not code or not returned_state or not secrets.compare_digest(returned_state[1], state):
            raise ConnectorError("auth_error", "Connexion Chronodrive non confirmée. Une vérification sur le site peut être nécessaire.")
        response = await self.request("POST", CONNECT + "/oauth/token", json={
            "client_id": config["client_id"], "grant_type": "authorization_code",
            "code_verifier": verifier, "code": code[1], "redirect_uri": WEB,
        })
        self.save_tokens(self.json(response))

    def save_tokens(self, data):
        if not data.get("access_token"):
            raise ConnectorError("auth_error", "Chronodrive n’a pas fourni de jeton de connexion.")
        self.session["token"] = data["access_token"]
        if data.get("refresh_token"):
            self.session["refresh_token"] = data["refresh_token"]
        self.session["expires_at"] = time.time() + int(data.get("expires_in", 3600))

    async def call(self, method, path, service="customers", data=None, params=None):
        config = await self.config()
        headers = {
            "Authorization": "Bearer " + self.session.get("token", ""),
            "x-api-key": config[service], "x-device-type": "WEB",
            "x-chronodrive-site-id": self.settings.chronodrive_store_id,
            "x-chronodrive-site-mode": self.settings.chronodrive_site_mode,
            "Origin": WEB, "Referer": WEB + "/", "Content-Type": "application/json",
        }
        response = await self.request(method, API + path, headers=headers, params=params, **({"json": data} if data is not None else {}))
        return self.json(response)

    async def health(self):
        if not self.session.get("token"):
            await self.login()
        elif self.session.get("expires_at", 0) < time.time() + 60:
            if self.session.get("refresh_token"):
                config = await self.config()
                response = await self.request("POST", CONNECT + "/oauth/token", json={
                    "client_id": config["client_id"], "grant_type": "refresh_token", "refresh_token": self.session["refresh_token"],
                })
                self.save_tokens(self.json(response))
            else:
                await self.login()
        user = await self.call("GET", "/customers/me")
        if not isinstance(user, dict) or not user.get("id"):
            raise ConnectorError("auth_error", "Chronodrive n’a pas reconnu le compte.")
        await self.get_cart()
        return True

    async def search(self, query):
        data = await self.call("GET", "/products", "products", params={"searchTerm": query, "page": 1, "size": 30})
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ConnectorError("protocol_error", "Format de recherche Chronodrive non reconnu.")
        result = []
        for item in data["content"]:
            labels = item.get("labels") or {}
            name = labels.get("productLabel") or item.get("name") or item.get("label")
            if not item.get("id") or not name:
                continue
            result.append(Product(str(item["id"]), name, [str(ean) for ean in item.get("eans", [])], cents((item.get("prices") or {}).get("defaultPrice")), labels.get("unitQuantityLabel", "")))
        return result

    async def get_cart(self):
        data = await self.call("GET", "/customers/me/carts", params={"withCoupons": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ConnectorError("protocol_error", "Format de panier Chronodrive non reconnu.")
        active = [cart for cart in data["content"] if cart.get("isOrdered") is False]
        if len(active) != 1 or not active[0].get("id"):
            raise ConnectorError("no_cart", "Ouvrez Chronodrive, sélectionnez votre magasin et créez un panier actif, puis reconnectez.")
        self.session["cart_id"] = str(active[0]["id"])
        cart = {}
        lines = []
        for line in active[0].get("items", []):
            product_id = str((line.get("product") or {}).get("id", ""))
            if not product_id or not isinstance(line.get("quantity"), int):
                raise ConnectorError("protocol_error", "Ligne de panier Chronodrive non reconnue.")
            cart[product_id] = line["quantity"]
            product = line["product"]
            labels = product.get("labels") or {}
            lines.append({"id":product_id,"name":labels.get("productLabel") or product.get("name", ""),
                          "quantity":line["quantity"],"size":labels.get("unitQuantityLabel", ""),
                          "line_total_cents":cents((product.get("prices") or {}).get("totalAmount"))})
        amounts = active[0].get("amounts") or {}
        self.last_cart = {"lines":lines,"currency":"EUR", "total_cents":cents(amounts.get("totalOrderAmount")),
                          "deposit_cents":cents(amounts.get("totalDepositAmount")), "fees_cents":cents(amounts.get("totalAdditionalCostsAmount")),
                          "discount_cents":cents(amounts.get("totalDiscountAmount"))}
        return cart

    async def get_cart_details(self):
        await self.get_cart()
        return self.last_cart

    async def set_quantity(self, product_id, quantity):
        current = (await self.get_cart()).get(product_id, 0)
        delta = quantity - current
        if delta:
            path = "/carts/" + self.session["cart_id"] + "/items"
            if quantity == 0:
                await self.call("DELETE", path + "/" + product_id, "carts")
            else:
                result = await self.call("POST", path, "carts", data={"content": [{"clientOrigin": "", "productId": product_id, "quantity": delta}], "optimizedMode": True})
                if not isinstance(result, dict) or not result.get("content") or any(line.get("returnType") != "SUCCESS" for line in result["content"]):
                    raise ConnectorError("quantity_mismatch", "Chronodrive n’a pas accepté la quantité demandée (stock ou limite d’achat).")
        if (await self.get_cart()).get(product_id, 0) != quantity:
            raise ConnectorError("quantity_mismatch", "La quantité du panier Chronodrive ne correspond pas à la liste.")
