"""Picnic operations delegated to the pinned maintained Node client."""
import asyncio
import json
import os
import re
import logging
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .base import ConnectorError, Product, StoreAdapter

log = logging.getLogger(__name__)


class PicnicAdapter(StoreAdapter):
    name = "picnic"

    def __init__(self, settings, session=None, transport=None):
        if settings.picnic_country_code.lower() not in {"fr", "nl", "de"} or not settings.picnic_api_version.isdigit():
            raise ConnectorError("not_configured", "Vérifiez PICNIC_COUNTRY_CODE (FR, NL ou DE) et PICNIC_API_VERSION.")
        super().__init__(settings, session, transport)

    async def _run(self, action, **values):
        payload = {"action": action, "token": self.session.get("token"), "otp_required": self.session.get("otp_required", False),
                   "country": self.settings.picnic_country_code.upper(), "version": self.settings.picnic_api_version,
                   "device": self.settings.picnic_device_id, "agent": self.settings.picnic_agent, **values}
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                os.environ.get("PICNIC_NODE_BINARY", "node"), str(Path(__file__).with_name("picnic-search.mjs")),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout=65)
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("Invalid bridge response")
            if result.get("http_status") == 401:
                self.session.pop("token", None)
                raise ConnectorError("auth_error", "Session Picnic expirée ou identifiants refusés. Reconnectez le compte.")
            if result.get("http_status") == 429:
                raise ConnectorError("rate_limited", "Picnic limite les requêtes. Réessayez dans quelques minutes.")
            if result.get("http_status") == 403:
                raise ConnectorError("access_denied", "Picnic a refusé l’accès. Vérifiez le compte ou réessayez plus tard.")
            if result.get("code") == "QUANTITY_MISMATCH":
                raise ConnectorError("quantity_mismatch", "Picnic n’a pas accepté la quantité demandée (stock ou limite d’achat).")
            if process.returncode or result.get("error") or "data" not in result:
                stages = {"search-page":"page de recherche", "search-action":"action de recherche", "bootstrap":"configuration du catalogue", "client-chunk":"composant de recherche", "search-format":"format de recherche", "product-page":"fiche produit", "search-parameters":"paramètres de recherche", "search-action-discovery":"détection de l’action"}
                step = result.get("step")
                details = [stages[step]] if step in stages else []
                http_status = result.get("http_status")
                if isinstance(http_status, int) and 100 <= http_status <= 599:
                    details.append(f"HTTP amont {http_status}")
                if result.get("received_format") in {"fusion", "rsc"}:
                    details.append("format " + result["received_format"])
                reasons = {"missing-client-config","missing-search-parameters","missing-search-action","unexpected-action-response","unexpected-fusion-layout","unexpected-search-result","unexpected-search-sections","unexpected-search-products","rsc-reference-cycle","decode-error","client-type-error","unspecified"}
                reason = result.get("reason") if result.get("reason") in reasons else "unspecified"
                log.warning("Picnic operation=%s step=%s upstream_status=%s reason=%s", action, step if step in stages else "unknown", http_status if isinstance(http_status,int) else None, reason)
                state = "search_error" if action == "search" else "ean_lookup_error" if action == "product" else "upstream_error"
                message = " ; ".join(details) or "réponse du catalogue non reconnue"
                raise ConnectorError(state, f"Picnic : {message}. Réessayez ou modifiez la recherche.")
            if isinstance(result.get("session"), dict):
                self.session.update({key: result["session"][key] for key in ("token", "otp_required") if key in result["session"]})
            return result["data"]
        except (OSError, ValueError, TypeError, asyncio.TimeoutError) as error:
            state = "search_error" if action == "search" else "upstream_error"
            raise ConnectorError(state, f"Picnic : opération {action} indisponible. Réessayez plus tard.") from error
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()

    async def health(self):
        if not self.session.get("token"):
            await self._run("login", username=self.settings.picnic_username, password=self.settings.picnic_password)
            if not self.session.get("token"):
                raise ConnectorError("protocol_error", "Picnic n’a pas fourni de session.")
        if self.session.get("otp_required"):
            raise ConnectorError("otp_required", "Vérification Picnic nécessaire. Demandez un code SMS puis saisissez-le ici.")
        await self._run("health")
        return True

    async def send_code(self):
        if not self.session.get("token"):
            try:
                await self.health()
            except ConnectorError as error:
                if error.status != "otp_required":
                    raise
        await self._run("send-code")

    async def verify_code(self, code):
        if not self.session.get("token") or not self.session.get("otp_required"):
            raise ConnectorError("auth_error", "Reconnectez Picnic avant de saisir le code.")
        await self._run("verify-code", code=code)
        await self.health()

    async def search(self, query):
        data = await self._run("search", query=query)
        if not isinstance(data, list):
            raise ConnectorError("search_error", "Réponse de recherche Picnic non reconnue.")
        return [Product(**product) for product in data]

    async def search_by_ean(self, ean):
        self.ean_lookup_message = "Le service EAN Picnic ne renvoie pas de produit vérifiable. Une recherche par nom est proposée."
        if not re.fullmatch(r"[0-9]{8,32}", ean):
            return []
        country = self.settings.picnic_country_code.lower()
        url = f"https://picnic.app/{country}/qr/gtin/{ean}"
        allowed = {"picnic.app", "www.picnic.app", f"gtin-resolver-prod.{country}.picnicinternational.com"}
        product_id = None
        try:
            # The QR route is public: never send account tokens or follow arbitrary hosts.
            for _ in range(6):
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password or parsed.port not in {None,443}:
                    break
                response = await self.client.get(url)
                if response.status_code in {404,410}:
                    self.ean_lookup_message = "EAN absent du service de résolution Picnic. Une recherche par nom est proposée."
                    return []
                if response.status_code not in {200,301,302,303,307,308}:
                    raise ConnectorError("ean_lookup_error", f"Résolution EAN Picnic indisponible (HTTP amont {response.status_code}). Une recherche par nom reste possible.")
                target = urljoin(url, response.headers.get("location", ""))
                if not response.headers.get("location"):
                    return []
                destination = urlparse(target)
                if destination.scheme not in {"https", "picnic"} or destination.username or destination.password or destination.scheme == "https" and (destination.hostname not in allowed or destination.port not in {None,443}):
                    return []
                match = re.search(r"(?:;|[?&])id=([A-Za-z0-9_-]{1,128})(?=$|[;&?#/])", unquote(target))
                if match:
                    product_id = match[1]
                    break
                url = target
        except httpx.HTTPError as error:
            raise ConnectorError("ean_lookup_error", "Le service EAN Picnic est injoignable. Une recherche par nom reste possible.") from error
        if not product_id:
            return []
        data = await self._run("product", product_id=product_id)
        if data is None:
            self.ean_lookup_message = "L’EAN correspond à un produit Picnic, mais sa fiche n’est pas disponible pour ce compte. Une recherche par nom est proposée."
            return []
        if not isinstance(data, dict) or data.get("id") != product_id:
            raise ConnectorError("ean_lookup_error", "La fiche Picnic ne confirme pas le produit résolu par EAN.")
        return [Product(**{**data, "eans":[ean]})]

    async def get_cart_details(self):
        data = await self._run("cart")
        if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
            raise ConnectorError("protocol_error", "Format de panier Picnic non reconnu.")
        return data

    async def get_cart(self):
        quantities = {}
        for line in (await self.get_cart_details())["lines"]:
            quantities[line["id"]] = quantities.get(line["id"], 0) + line["quantity"]
        return quantities

    async def set_quantity(self, product_id, quantity):
        await self._run("set-quantity", product_id=product_id, quantity=quantity)
