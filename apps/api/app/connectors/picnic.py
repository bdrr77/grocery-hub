"""Picnic storefront v15 protocol, including explicit SMS verification."""
from hashlib import md5
from urllib.parse import quote

from .base import ConnectorError, Product, StoreAdapter, walk


class PicnicAdapter(StoreAdapter):
    name = "picnic"

    def __init__(self, settings, session=None, transport=None):
        country = settings.picnic_country_code.lower()
        if country not in {"fr", "nl", "de"}:
            raise ConnectorError("not_configured", "PICNIC_COUNTRY_CODE doit être FR, NL ou DE.")
        if not settings.picnic_api_version.isdigit():
            raise ConnectorError("not_configured", "PICNIC_API_VERSION doit être numérique.")
        super().__init__(settings, session, transport)
        self.base = f"https://storefront-prod.{country}.picnicinternational.com/api/{settings.picnic_api_version}"

    async def call(self, method, path, data=None):
        headers = {
            "x-picnic-agent": self.settings.picnic_agent,
            "x-picnic-did": self.settings.picnic_device_id,
            "x-picnic-auth": self.session.get("token", ""),
        }
        response = await self.request(method, self.base + path, headers=headers, **({"json": data} if data is not None else {}))
        if response.headers.get("x-picnic-auth"):
            self.session["token"] = response.headers["x-picnic-auth"]
        result = self.json(response)
        if isinstance(result, dict) and result.get("error"):
            code = (result["error"] or {}).get("code", "")
            if code in {"AUTH_ERROR", "AUTH_INVALID_CRED"}:
                self.session.pop("token", None)
                raise ConnectorError("auth_error", "Identifiants Picnic refusés. Vérifiez le compte et le pays.")
            raise ConnectorError("upstream_error", "Picnic a refusé la requête.")
        return result

    async def health(self):
        if not self.session.get("token"):
            data = await self.call("POST", "/user/login", {
                "key": self.settings.picnic_username,
                "secret": md5(self.settings.picnic_password.encode(), usedforsecurity=False).hexdigest(),
                "client_id": 30100,
            })
            if not self.session.get("token"):
                raise ConnectorError("protocol_error", "Picnic n’a pas fourni de session.")
            self.session["otp_required"] = bool(data.get("second_factor_authentication_required"))
        if self.session.get("otp_required"):
            raise ConnectorError("otp_required", "Vérification Picnic nécessaire. Demandez un code SMS puis saisissez-le ici.")
        await self.call("GET", "/user")
        return True

    async def send_code(self):
        if not self.session.get("token"):
            try:
                await self.health()
            except ConnectorError as error:
                if error.status != "otp_required":
                    raise
        await self.call("POST", "/user/2fa/generate", {"channel": "SMS"})

    async def verify_code(self, code):
        if not self.session.get("token") or not self.session.get("otp_required"):
            raise ConnectorError("auth_error", "Reconnectez Picnic avant de saisir le code.")
        old_token = self.session["token"]
        await self.call("POST", "/user/2fa/verify", {"otp": code})
        if self.session.get("token") == old_token:
            raise ConnectorError("protocol_error", "Picnic n’a pas confirmé la vérification.")
        self.session["otp_required"] = False
        await self.health()

    async def search(self, query):
        data = await self.call("GET", "/search?search_term=" + quote(query, safe=""))
        products = {}
        for node in walk(data):
            if node.get("type") not in {"SINGLE_ARTICLE", "ARTICLE", "SELLING_UNIT"} or not node.get("id") or not node.get("name"):
                continue
            eans = node.get("eans") or node.get("gtins") or []
            if node.get("ean"):
                eans = [*eans, node["ean"]]
            price = node.get("price")
            product = Product(str(node["id"]), node["name"], [str(ean) for ean in eans], price if isinstance(price, int) else None, node.get("unit_quantity", ""))
            products[product.id] = product
        if not isinstance(data, (list, dict)) or isinstance(data, dict) and not products and data:
            raise ConnectorError("protocol_error", "Format de recherche Picnic non reconnu.")
        return list(products.values())[:30]

    async def get_cart(self):
        data = await self.call("GET", "/cart")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ConnectorError("protocol_error", "Format de panier Picnic non reconnu.")
        cart = {}
        for line in data["items"]:
            if line.get("type") != "ORDER_LINE":
                continue
            articles = line.get("items", [])
            if not articles:
                continue
            product_id = str(articles[0].get("id", ""))
            if not product_id or any(str(article.get("id")) != product_id for article in articles):
                raise ConnectorError("protocol_error", "Une ligne composite Picnic ne peut pas être synchronisée.")
            # Picnic represents units as repeated ORDER_ARTICLE entries, not a count field.
            cart[product_id] = cart.get(product_id, 0) + len(articles)
        return cart

    async def set_quantity(self, product_id, quantity):
        current = (await self.get_cart()).get(product_id, 0)
        delta = quantity - current
        if delta:
            await self.call("POST", "/cart/add_product" if delta > 0 else "/cart/remove_product", {"product_id": product_id, "count": abs(delta)})
        if (await self.get_cart()).get(product_id, 0) != quantity:
            raise ConnectorError("quantity_mismatch", "Picnic n’a pas accepté la quantité demandée (stock ou limite d’achat).")
