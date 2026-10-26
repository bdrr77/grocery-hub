from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


class ConnectorError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Product:
    id: str
    name: str
    eans: list[str]
    price_cents: int | None = None
    size: str = ""
    brand: str = ""

    def public(self):
        return asdict(self)


def cents(value: Any) -> int | None:
    try:
        return int(Decimal(str(value)) * 100) if value is not None else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class StoreAdapter:
    def __init__(self, settings, session=None, transport=None):
        self.settings = settings
        self.session = dict(session or {})
        self.client = httpx.AsyncClient(timeout=12, transport=transport, follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, method, url, **kwargs):
        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise ConnectorError("unavailable", "Le magasin est injoignable. Réessayez plus tard.") from error
        if response.status_code == 401:
            self.session.pop("token", None)
            raise ConnectorError("auth_error", "Session expirée ou identifiants refusés. Reconnectez le magasin.")
        if response.status_code == 403:
            raise ConnectorError("access_denied", "Accès refusé par le magasin. Vérifiez votre compte ou réessayez plus tard.")
        if response.status_code == 429:
            raise ConnectorError("rate_limited", "Trop de requêtes. Nouvelle tentative dans quelques minutes.")
        if not 200 <= response.status_code < 300:
            raise ConnectorError("upstream_error", f"Le magasin a refusé la requête (HTTP {response.status_code}).")
        return response

    @staticmethod
    def json(response):
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError("protocol_error", "Réponse du magasin non reconnue. Le connecteur doit peut-être être mis à jour.") from error

    async def search_by_ean(self, ean):
        return [product for product in await self.search(ean) if ean in product.eans]

    async def add_item(self, product_id, quantity=1):
        cart = await self.get_cart()
        await self.set_quantity(product_id, cart.get(product_id, 0) + quantity)

    async def remove_item(self, product_id):
        await self.set_quantity(product_id, 0)

    async def clear_cart(self):
        # No caller in the sync pipeline clears an entire account cart.
        for product_id in await self.get_cart():
            await self.remove_item(product_id)
