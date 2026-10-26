"""A household search across stores and a price-free product directory."""
import asyncio
import time

from sqlalchemy import select, update, delete

from .connectors.base import ConnectorError
from .database import engine, insert_for, items, item_details, product_facts, targets, enqueue, integrations
from .integration_service import operate, public_status
from .pricing import pack_pricing
from .products import product_search, product_details


def group_results(sources):
    # Group only verified identical barcodes. Similar names can be different packs.
    groups = []
    for source, products in sources:
        for product in products:
            codes = {code.lstrip("0") for code in product.get("eans", []) if code.isdigit()}
            matches = [group for group in groups if codes and codes & set(group["codes"])]
            group = matches[0] if matches else None
            for duplicate in matches[1:]:
                group["codes"] = sorted(set(group["codes"]) | set(duplicate["codes"]))
                group["offers"].extend(duplicate["offers"])
                groups.remove(duplicate)
            if group is None:
                group = {"name":product["name"], "size":product.get("size", ""), "codes":[], "offers":[]}
                groups.append(group)
            group["codes"] = sorted(set(group["codes"]) | codes)
            brands = list(dict.fromkeys(offer.get("brand", "") for offer in [*group["offers"],product] if offer.get("brand")))
            group["brand"] = " · ".join(brands)
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
                    data = {"name":product["name"], "size":product["size"], "brand":product.get("brand", "")}
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
        replace_identity(connection, ean, code, data, "openfoodfacts", code)


def replace_identity(connection, ean, code, data, source, source_id):
    item = connection.execute(select(items).where(items.c.ean == ean)).mappings().first()
    if not item:
        raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
    if code != ean:
        if connection.execute(select(items.c.ean).where(items.c.ean == code)).first():
            raise ConnectorError("invalid_match", "Ce produit est déjà dans votre liste.")
        mapped = connection.execute(select(targets.c.ean).where(targets.c.ean == ean, targets.c.product_id.is_not(None))).first()
        working = connection.execute(select(integrations.c.store).where(integrations.c.lease_until > time.time())).first()
        if mapped or working:
            raise ConnectorError("invalid_match", "Attendez la fin de la synchronisation avant de remplacer ce produit.")
        connection.execute(delete(targets).where(targets.c.ean == ean))
        connection.execute(delete(item_details).where(item_details.c.ean == ean))
        connection.execute(delete(items).where(items.c.ean == ean))
        connection.execute(items.insert().values(ean=code,label=data["name"],quantity=item["quantity"]))
    else:
        connection.execute(update(items).where(items.c.ean == ean).values(label=data["name"]))
    enqueue(connection, code, item["quantity"], data["name"])
    entry = insert_for(item_details).values(ean=code,size=data.get("size", ""),source=source,source_id=source_id)
    connection.execute(entry.on_conflict_do_update(index_elements=[item_details.c.ean],set_={"size":data.get("size", ""),"source":source,"source_id":source_id}))


def choose_store_product(ean, source, product_id):
    with engine.begin() as connection:
        target = connection.execute(select(targets).where(targets.c.ean == ean,targets.c.store == source)).mappings().first()
        product = next((p for p in (target["candidates"] if target else []) if p["id"] == product_id), None)
        if not product:
            raise ConnectorError("invalid_match", "Relancez la recherche pour choisir ce produit.")
        if target["product_id"] and target["product_id"] != product_id:
            raise ConnectorError("invalid_match", "Retirez d’abord le produit déjà associé à ce magasin.")
        if connection.execute(select(targets.c.ean).where(targets.c.store == source,targets.c.product_id == product_id,targets.c.ean != ean)).first():
            raise ConnectorError("invalid_match", "Ce produit magasin est déjà associé à un autre article.")
        codes = list(dict.fromkeys(code for code in product.get("eans", []) if code.isdigit()))
        code = ean if ean in codes else codes[0] if len(codes) == 1 else None
        if not code:
            raise ConnectorError("invalid_match", "Ce magasin ne fournit pas de code-barres vérifiable pour ce résultat.")
        replace_identity(connection, ean, code, product, source, product_id)
        connection.execute(update(targets).where(targets.c.ean == code,targets.c.store == source).values(product_id=product_id,product_name=product["name"],price_cents=product.get("price_cents"),candidates=[product]))


async def refresh_product(ean):
    with engine.connect() as connection:
        item = connection.execute(select(items).where(items.c.ean == ean)).mappings().first()
    if not item:
        raise ConnectorError("not_found", "Cet article n'est plus dans la liste.")
    stores = [store for store in public_status() if store["connected"]]
    with engine.begin() as connection:
        enqueue(connection,ean,item["quantity"],item["label"])
    messages = []
    async def resolve_barcode(store):
        try:
            await operate(store["id"],"resolve",ean=ean)
        except ConnectorError:
            messages.append({"source":store["id"],"name":store["name"],"message":"Actualisation du magasin impossible. Relancez la recherche."})
    # Exact store barcode resolution comes first, before the fresh OFF request.
    await asyncio.gather(*(resolve_barcode(store) for store in stores))
    details = await asyncio.to_thread(product_details, ean) if ean.isdigit() else None
    with engine.begin() as connection:
        if not connection.execute(select(items.c.ean).where(items.c.ean == ean)).first():
            raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
        entry = insert_for(product_facts).values(ean=ean,data=details or {},fetched_at=time.time())
        connection.execute(entry.on_conflict_do_update(index_elements=[product_facts.c.ean],set_={"data":details or {},"fetched_at":time.time()}))
        if details:
            connection.execute(update(items).where(items.c.ean == ean).values(label=details["name"]))
            connection.execute(update(targets).where(targets.c.ean == ean).values(label=details["name"]))
            entry = insert_for(item_details).values(ean=ean,size=details.get("size", ""),source="openfoodfacts",source_id=ean)
            connection.execute(entry.on_conflict_do_update(index_elements=[item_details.c.ean],set_={"size":details.get("size", ""),"source":"openfoodfacts","source_id":ean}))
    sources = [("openfoodfacts",[{"id":ean,**details,"eans":[ean],"price_cents":None}])] if details else []
    async def search_from_facts(store):
        if not details:
            return
        current = next(s for s in public_status() if s["id"] == store["id"])
        job = next((j for j in current["items"] if j["ean"] == ean),None)
        if not job or job["product_id"]:
            return
        name = details["name"]
        brand = details.get("brand", "").split(",")[0].strip()
        if brand and brand.casefold() not in name.casefold():
            name = brand + " " + name
        size = details.get("size", "")
        query = (name if not size or size.casefold() in name.casefold() else name + " " + size)[:200]
        try:
            products = await operate(store["id"],"search",ean=ean,query=query)
            sources.append((store["id"],products))
        except ConnectorError:
            messages.append({"source":store["id"],"name":store["name"],"message":"Recherche du magasin indisponible. Relancez la recherche."})
    await asyncio.gather(*(search_from_facts(store) for store in stores))
    return {"results":group_results(sources),"messages":messages}
