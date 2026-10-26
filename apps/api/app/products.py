import json
import logging
import time
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def product_search(query: str) -> list[dict]:
    return _product_search(query.strip(), int(time.time() // 300))


@lru_cache(maxsize=64)
def _product_search(query, time_bucket):
    """Cache identical searches; OFF search is more limited than barcode reads."""
    if query.isdigit():
        details = product_details(query)
        return [{**details, "id":query, "eans":[query], "price_cents":None}] if details else []
    params = urlencode({"search_terms":query, "search_simple":1, "action":"process", "json":1, "page_size":10, "fields":"code,product_name,product_name_fr,brands,quantity"})
    request = Request("https://world.openfoodfacts.org/cgi/search.pl?" + params, headers={"User-Agent":"GroceryHub/0.3 (https://grocery.dabrome.com)"})
    with urlopen(request, timeout=8) as response:
        data = json.load(response)
    result = []
    for product in data.get("products", []):
        code = str(product.get("code", ""))
        name = product.get("product_name_fr") or product.get("product_name")
        if not code.isdigit() or not 8 <= len(code) <= 32 or not isinstance(name, str) or not name.strip():
            continue
        brand = product.get("brands") or ""
        if isinstance(brand, str) and brand.split(",")[0].strip().casefold() not in name.casefold():
            name = brand.split(",")[0].strip() + " " + name
        size = product.get("quantity")
        result.append({"id":code,"name":name.strip()[:250],"brand":brand[:100] if isinstance(brand,str) else "", "size":size[:100] if isinstance(size,str) else "", "eans":[code],"price_cents":None})
    return result


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
    if details:
        from .database import engine, insert_for, product_facts
        with engine.begin() as connection:
            entry = insert_for(product_facts).values(ean=barcode,data=details,fetched_at=time.time())
            connection.execute(entry.on_conflict_do_update(index_elements=[product_facts.c.ean],set_={"data":details,"fetched_at":time.time()}))
    return details["name"] if details else f"EAN {barcode}"
