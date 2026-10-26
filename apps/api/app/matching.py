"""Prepare explicit, editable searches; name matches are never auto-added."""
import time
import asyncio
import re

from sqlalchemy import select, update

from .connectors.base import ConnectorError
from .database import cart_snapshots, ean_lookups, engine, insert_for, items, item_details, product_facts, targets
from .products import product_details


def equivalent_queries(details):
    name = details["name"]
    brand = details.get("brand", "").split(",")[0].strip()
    branded = brand + " " + name if brand and brand.casefold() not in name.casefold() else name
    size = details.get("size", "")
    queries = []
    for text, pack in [(branded,size),(details.get("description", ""),size),(name,size),(details.get("description", ""),""),(name,"")]:
        if not text.strip():
            continue
        query = (text if not pack or pack.casefold() in text.casefold() else text + " " + pack)[:200]
        if query.casefold() not in [q.casefold() for q in queries]:
            queries.append(query)
    unbranded = re.sub(r"(?<!\w)"+re.escape(brand)+r"(?!\w)", " ", name, flags=re.I) if brand else name
    words = re.findall(r"[^\W\d_]+",unbranded,flags=re.UNICODE)
    for query in [" ".join(unbranded.split())," ".join(words),max(words,key=len) if words else ""]:
        if len(query) >= 3 and query.casefold() not in [q.casefold() for q in queries]:
            queries.append(query[:200])
    return queries


async def search_equivalents(adapter, details):
    for query in equivalent_queries(details):
        products = await adapter.search(query)
        if products:
            return products
    return []


async def automatic_equivalents(ean, adapter):
    with engine.connect() as connection:
        cached = connection.execute(select(product_facts).where(product_facts.c.ean == ean)).mappings().first()
    details = cached["data"] if cached else None
    if not cached or time.time()-cached["fetched_at"] > (86400 if details else 900):
        details = await asyncio.to_thread(product_details,ean)
        with engine.begin() as connection:
            entry=insert_for(product_facts).values(ean=ean,data=details or {},fetched_at=time.time())
            connection.execute(entry.on_conflict_do_update(index_elements=[product_facts.c.ean],set_={"data":details or {},"fetched_at":time.time()}))
    if not details:
        return []
    with engine.begin() as connection:
        if not connection.execute(select(items.c.ean).where(items.c.ean == ean)).first():
            return []
        source = connection.execute(select(item_details.c.source).where(item_details.c.ean == ean)).scalar_one_or_none()
        if source not in {"picnic","chronodrive"}:
            connection.execute(update(items).where(items.c.ean == ean).values(label=details["name"]))
            entry=insert_for(item_details).values(ean=ean,size=details.get("size", ""),source="openfoodfacts",source_id=ean)
            connection.execute(entry.on_conflict_do_update(index_elements=[item_details.c.ean],set_={"size":details.get("size", ""),"source":"openfoodfacts","source_id":ean}))
    return [product.public() for product in await search_equivalents(adapter,details)]


def search_suggestions(ean, fetch_off=True):
    with engine.connect() as connection:
        item = connection.execute(select(items).where(items.c.ean == ean)).mappings().first()
        if not item:
            raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
        chrono = connection.execute(select(targets).where(targets.c.store == "chronodrive", targets.c.ean == ean, targets.c.quantity > 0)).mappings().first()
        snapshot = connection.execute(select(cart_snapshots).where(cart_snapshots.c.store == "chronodrive")).mappings().first()
        cached = connection.execute(select(product_facts).where(product_facts.c.ean == ean)).mappings().first()
        lookup = connection.execute(select(ean_lookups).where(ean_lookups.c.store == "picnic", ean_lookups.c.ean == ean)).mappings().first()
    suggestions = []
    def add(source, name, size=""):
        name = name.strip()
        if not name:
            return
        query = name if not size or size.casefold() in name.casefold() else f"{name} {size}"
        suggestions.append({"source":source,"name":name,"size":size,"query":query[:200]})
    if chrono and chrono["product_id"] and chrono["product_name"]:
        lines = snapshot["data"].get("lines", []) if snapshot else []
        line = next((line for line in lines if line["id"] == chrono["product_id"]), {})
        candidates = chrono["candidates"] or []
        candidate = next((candidate for candidate in candidates if candidate["id"] == chrono["product_id"]), {})
        add("chronodrive", line.get("name") or chrono["product_name"], line.get("size") or candidate.get("size", ""))
    details = cached["data"] if cached else {}
    if ean.isdigit() and fetch_off and (not cached or time.time()-cached["fetched_at"] > (86400 if details else 900)):
        details = product_details(ean) or {}
        with engine.begin() as connection:
            entry = insert_for(product_facts).values(ean=ean,data=details,fetched_at=time.time())
            connection.execute(entry.on_conflict_do_update(index_elements=[product_facts.c.ean],set_={"data":details,"fetched_at":time.time()}))
    if details:
        name = details["name"]
        brand = details.get("brand", "").split(",",1)[0].strip()
        if brand and brand.casefold() not in name.casefold():
            name = brand + " " + name
        add("openfoodfacts", name, details.get("size", ""))
    manual = "" if item["label"].startswith("EAN ") else item["label"][:200]
    suggestions.append({"source":"user","name":item["label"],"size":"","query":manual})
    return {"ean":ean,"suggestions":suggestions,"ean_lookup":{key:lookup[key] for key in ("status","message","checked_at")} if lookup else None}
