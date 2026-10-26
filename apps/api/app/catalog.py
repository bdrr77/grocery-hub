"""A household search across stores and a price-free product directory."""
import asyncio
import time

from sqlalchemy import select, update

from .connectors.base import ConnectorError
from .database import engine, insert_for, items, item_details, product_facts, targets
from .integration_service import operate, public_status
from .pricing import pack_pricing
from .products import product_search


def group_results(sources):
    # Group only verified identical barcodes. Similar names can be different packs.
    groups = []
    for source, products in sources:
        for product in products:
            codes = {code.lstrip("0") for code in product.get("eans", []) if code.isdigit()}
            group = next((group for group in groups if codes and codes & set(group["codes"])), None)
            if group is None:
                group = {"name":product["name"], "size":product.get("size", ""), "codes":[], "offers":[]}
                groups.append(group)
            group["codes"] = sorted(set(group["codes"]) | codes)
            if not any(offer["source"] == source and offer["id"] == product["id"] for offer in group["offers"]):
                group["offers"].append({**product, **pack_pricing(product.get("size", ""), product.get("price_cents")), "source":source})
    return groups


async def search_catalog(ean, query):
    with engine.connect() as connection:
        if not connection.execute(select(items.c.ean).where(items.c.ean == ean)).first():
            raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
    query = query.strip()
    if len(query) < 2:
        raise ConnectorError("invalid_query", "Saisissez au moins deux caractères.")
    stores = public_status()
    names = {store["id"]:store["name"] for store in stores}
    async def search_store(store):
        if not store["connected"]:
            return store["id"], [], "Connectez ce magasin pour y rechercher des produits."
        job = next((job for job in store["items"] if job["ean"] == ean), None)
        if ean.isdigit() and job and job["status"] == "queued":
            return store["id"], [], "Recherche automatique en cours. Les informations apparaîtront sur la carte."
        try:
            return store["id"], await operate(store["id"], "search", ean=ean, query=query), None
        except ConnectorError:
            return store["id"], [], "Recherche momentanément indisponible. Réessayez."
    async def search_directory():
        try:
            products = await asyncio.to_thread(product_search, query)
            with engine.begin() as connection:
                for product in products:
                    data = {"name":product["name"], "size":product["size"], "brand":""}
                    entry = insert_for(product_facts).values(ean=product["id"], data=data, fetched_at=time.time())
                    connection.execute(entry.on_conflict_do_update(index_elements=[product_facts.c.ean],set_={"data":data,"fetched_at":time.time()}))
            return "openfoodfacts", products, None
        except (OSError, ValueError, TypeError, AttributeError):
            return "openfoodfacts", [], "Recherche momentanément indisponible. Réessayez."
    results = await asyncio.gather(*(search_store(store) for store in stores), search_directory())
    return {"results":group_results([(source,products) for source,products,_ in results]), "messages":[{"source":source,"name":names.get(source,"Open Food Facts"),"message":message} for source,_,message in results if message]}


def use_directory_identity(ean, code):
    with engine.begin() as connection:
        fact = connection.execute(select(product_facts).where(product_facts.c.ean == code)).mappings().first()
        if not fact or time.time() - fact["fetched_at"] > 86400 or not fact["data"].get("name"):
            raise ConnectorError("invalid_match", "Relancez la recherche pour choisir ce produit.")
        data = fact["data"]
        if not connection.execute(update(items).where(items.c.ean == ean).values(label=data["name"])).rowcount:
            raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
        connection.execute(update(targets).where(targets.c.ean == ean).values(label=data["name"]))
        entry = insert_for(item_details).values(ean=ean,size=data.get("size", ""),source="openfoodfacts",source_id=code)
        connection.execute(entry.on_conflict_do_update(index_elements=[item_details.c.ean],set_={"size":data.get("size", ""),"source":"openfoodfacts","source_id":code}))
