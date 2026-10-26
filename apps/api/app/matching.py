"""Prepare explicit, editable searches; name matches are never auto-added."""
import time

from sqlalchemy import select

from .connectors.base import ConnectorError
from .database import cart_snapshots, ean_lookups, engine, insert_for, items, product_facts, targets
from .products import product_details


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
