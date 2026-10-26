import json
import logging
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def product_details(barcode: str) -> dict | None:
    request = Request(
        f"https://world.openfoodfacts.org/api/v3/product/{barcode}?fields=product_name,product_name_fr,brands,quantity",
        headers={"User-Agent": "GroceryHub/0.3 (https://grocery.dabrome.com)"},
    )
    try:
        with urlopen(request, timeout=4) as response:
            data = json.load(response)
        product = data.get("product") or {}
        label = product.get("product_name_fr") or product.get("product_name")
        if isinstance(label, str) and label.strip():
            return {"name":label.strip()[:250], "brand":product.get("brands", "")[:100] if isinstance(product.get("brands", ""),str) else "", "size":product.get("quantity", "")[:100] if isinstance(product.get("quantity", ""),str) else ""}
    except (OSError, ValueError, TypeError, AttributeError):
        log.info("Product lookup unavailable for %s; saving barcode", barcode)
    return None


def product_label(barcode: str) -> str:
    """An unknown product or unavailable catalog must still allow saving."""
    details = product_details(barcode)
    return details["name"] if details else f"EAN {barcode}"
