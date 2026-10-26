"""Database-backed connector sessions, leases and coalescing transactional outbox."""
import hashlib
import json
import time
import uuid

from sqlalchemy import select, update

from .config import settings
from .connectors.base import ConnectorError
from .connectors.chronodrive import ChronodriveAdapter
from .connectors.picnic import PicnicAdapter
from .database import STORES, cart_snapshots, ean_lookups, engine, insert_for, integrations, item_details, items, targets
from .pricing import summarize, product_card

NAMES = {"picnic": "Picnic", "chronodrive": "Chronodrive"}


def adopt_product_name(connection, ean, product, store):
    """Replace an unresolved barcode title while preserving user-provided names."""
    changed = connection.execute(update(items).where(items.c.ean == ean, items.c.label == f"EAN {ean}").values(label=product["name"]))
    if changed.rowcount:
        connection.execute(update(targets).where(targets.c.ean == ean).values(label=product["name"]))
        entry = insert_for(item_details).values(ean=ean,size=product.get("size", ""),source=store,source_id=product["id"])
        connection.execute(entry.on_conflict_do_nothing())


def configuration(store):
    if store == "picnic":
        values = [settings.picnic_username, settings.picnic_password, settings.picnic_country_code, settings.picnic_device_id, settings.picnic_api_version, settings.picnic_agent]
        configured = bool(all(values[:2]))
        message = "Renseignez PICNIC_USERNAME et PICNIC_PASSWORD sur le serveur (PICNIC_COUNTRY_CODE=FR)."
    else:
        values = [settings.chronodrive_email or settings.chronodrive_username, settings.chronodrive_password, settings.chronodrive_store_id, settings.chronodrive_site_mode]
        configured = bool(all(values))
        message = "Renseignez CHRONODRIVE_EMAIL (ou USERNAME), CHRONODRIVE_PASSWORD et CHRONODRIVE_STORE_ID sur le serveur."
    return configured, hashlib.sha256(json.dumps(values).encode()).hexdigest(), message


def initialize():
    with engine.begin() as connection:
        for store in STORES:
            connection.execute(insert_for(integrations).values(store=store).on_conflict_do_nothing())
            for item in connection.execute(select(items)).mappings():
                connection.execute(insert_for(targets).values(store=store, **dict(item)).on_conflict_do_nothing())
                # Older releases deliberately skipped Picnic EANs. Try those once after upgrade.
                if store == "picnic" and item["ean"].isdigit():
                    attempted = connection.execute(select(ean_lookups.c.ean).where(ean_lookups.c.store == store, ean_lookups.c.ean == item["ean"])).first()
                    if not attempted:
                        connection.execute(update(targets).where(targets.c.store == store, targets.c.ean == item["ean"], targets.c.status == "unmatched").values(status="queued", retry_at=0))


def public_status():
    now = time.time()
    with engine.connect() as connection:
        states = {row["store"]: dict(row) for row in connection.execute(select(integrations)).mappings()}
        jobs = [dict(row) for row in connection.execute(select(targets)).mappings()]
        snapshots = {row["store"]: dict(row) for row in connection.execute(select(cart_snapshots)).mappings()}
        lookups = {(row["store"],row["ean"]):{key:row[key] for key in ("status","message","checked_at")} for row in connection.execute(select(ean_lookups)).mappings()}
    result = []
    for store in STORES:
        configured, fingerprint, setup_message = configuration(store)
        row = states.get(store, {})
        state, message = row.get("status", "pending"), row.get("message", "En attente du worker.")
        checked = row.get("checked_at", 0)
        if not configured:
            state, message = "not_configured", setup_message
        elif row.get("fingerprint") != fingerprint:
            state, message = "pending", "Configuration modifiée. En attente de connexion."
        elif state == "connected" and now - checked > max(120, settings.connector_poll_seconds * 4):
            state, message = "stale", "Dernière connexion trop ancienne. Vérifiez que le worker est démarré."
        result.append({
            "id": store, "name": NAMES[store], "status": state, "connected": state == "connected",
            "message": message, "configured": configured, "checked_at": checked or None,
            "items": [{**{key: job[key] for key in ("ean", "quantity", "status", "message", "product_id", "product_name", "price_cents")}, **product_card(job, snapshots.get(store) if snapshots.get(store, {}).get("fingerprint") == fingerprint else None), "ean_lookup":lookups.get((store,job["ean"]))} for job in jobs if job["store"] == store and job["quantity"] > 0],
            "pricing":summarize(snapshots.get(store), [job for job in jobs if job["store"] == store], fingerprint, state == "connected", now),
        })
    return result


def acquire(store):
    owner = uuid.uuid4().hex
    with engine.begin() as connection:
        row = connection.execute(update(integrations).where(
            integrations.c.store == store, integrations.c.lease_until < time.time()
        ).values(lease_owner=owner, lease_until=time.time() + 300).returning(integrations)).mappings().first()
    if row is None:
        raise ConnectorError("busy", "Une opération est déjà en cours pour ce magasin. Réessayez dans un instant.")
    return owner, dict(row)


def save_state(store, owner, **values):
    with engine.begin() as connection:
        connection.execute(update(integrations).where(integrations.c.store == store, integrations.c.lease_owner == owner).values(**values))


def adapter_for(store, session):
    return (PicnicAdapter if store == "picnic" else ChronodriveAdapter)(settings, session)


async def operate(store, action="sync", *, ean=None, query=None, product_id=None, code=None, background=False):
    if store not in STORES:
        raise ConnectorError("not_found", "Magasin inconnu.")
    owner, state = acquire(store)
    adapter = None
    try:
        configured, fingerprint, setup_message = configuration(store)
        if not configured:
            raise ConnectorError("not_configured", setup_message)
        changed = state["fingerprint"] != fingerprint
        if background and not changed and state["retry_at"] > time.time():
            return
        if action == "connect" or changed:
            state["session"] = {}
            # A different account/store must never inherit old product mappings.
            if changed:
                with engine.begin() as connection:
                    connection.execute(update(targets).where(targets.c.store == store).values(product_id=None, product_name=None, price_cents=None, candidates=[], status="queued", retry_at=0))
        adapter = adapter_for(store, state["session"])
        if action == "verify-code":
            await adapter.verify_code(code)
        elif action == "send-code":
            await adapter.send_code()
            save_state(store, owner, status="otp_required", message="Code SMS demandé. Saisissez le code reçu pour terminer la connexion.", checked_at=time.time(), retry_at=time.time() + 86400, fingerprint=fingerprint, session=adapter.session)
            return
        else:
            await adapter.health()
        result = None
        if action == "search":
            products = await adapter.search(query)
            candidates = [product.public() for product in products]
            with engine.begin() as connection:
                connection.execute(update(targets).where(targets.c.store == store, targets.c.ean == ean, targets.c.quantity > 0).values(candidates=candidates))
            result = candidates
        elif action == "match":
            with engine.begin() as connection:
                job = connection.execute(select(targets).where(targets.c.store == store, targets.c.ean == ean, targets.c.quantity > 0)).mappings().first()
                if not job:
                    raise ConnectorError("not_found", "Cet article n’est plus dans la liste.")
                product = next((candidate for candidate in job["candidates"] if candidate["id"] == product_id), None)
                if not product:
                    raise ConnectorError("invalid_match", "Choisissez un produit issu de la recherche de ce magasin.")
                if job["product_id"] and job["product_id"] != product_id:
                    raise ConnectorError("invalid_match", "Pour changer de produit, retirez l’article, attendez sa synchronisation puis ajoutez-le de nouveau.")
                conflict = connection.execute(select(targets.c.ean).where(targets.c.store == store, targets.c.product_id == product_id, targets.c.ean != ean)).first()
                if conflict:
                    raise ConnectorError("invalid_match", "Ce produit magasin est déjà associé à un autre EAN.")
                connection.execute(update(targets).where(targets.c.store == store, targets.c.ean == ean).values(product_id=product_id, product_name=product["name"], price_cents=product["price_cents"], status="queued", retry_at=0, revision=targets.c.revision + 1))
                adopt_product_name(connection, ean, product, store)
        elif action == "sync":
            await sync_targets(store, adapter, owner)
        elif action == "resolve":
            await sync_targets(store, adapter, owner, ean=ean)
        if action in {"sync", "connect", "verify-code", "refresh", "resolve"}:
            snapshot = await adapter.get_cart_details()
            with engine.begin() as connection:
                entry = insert_for(cart_snapshots).values(store=store, fingerprint=fingerprint, fetched_at=time.time(), data=snapshot)
                connection.execute(entry.on_conflict_do_update(index_elements=[cart_snapshots.c.store], set_={"fingerprint":fingerprint,"fetched_at":time.time(),"data":snapshot}))
        save_state(store, owner, status="connected", message="Compte connecté. Synchronisation des articles associés active.", checked_at=time.time(), retry_at=0, fingerprint=fingerprint, session=adapter.session)
        return result
    except ConnectorError as error:
        # Messages are controlled strings, never upstream bodies, URLs or tokens.
        _, fingerprint, _ = configuration(store)
        if action in {"verify-code", "send-code"} and adapter and adapter.session.get("otp_required") and adapter.session.get("token"):
            message = "Code non validé. Vérifiez le code reçu et réessayez." if action == "verify-code" else "Impossible de demander un code. Réessayez plus tard."
            save_state(store, owner, status="otp_required", message=message, checked_at=time.time(), retry_at=time.time() + 86400, fingerprint=fingerprint, session=adapter.session)
        elif error.status in {"invalid_match", "not_found", "search_error"}:
            save_state(store, owner, status="connected", message="Compte connecté.", checked_at=time.time(), retry_at=0, fingerprint=fingerprint, session=adapter.session if adapter else {})
        else:
            save_state(store, owner, status=error.status, message=error.message, checked_at=time.time(), retry_at=time.time() + (86400 if error.status in {"otp_required", "auth_error", "access_denied"} else 300), fingerprint=fingerprint, session=adapter.session if adapter else {})
        if not background:
            raise
    except Exception as error:
        save_state(store, owner, status="protocol_error", message="Erreur du connecteur. Réessayez ou vérifiez les journaux du worker.", checked_at=time.time(), retry_at=time.time() + 300, session=adapter.session if adapter else {})
        if not background:
            raise ConnectorError("protocol_error", "Réponse du magasin non reconnue. Réessayez plus tard.") from error
    finally:
        if adapter:
            await adapter.close()
        save_state(store, owner, lease_owner="", lease_until=0)


async def sync_targets(store, adapter, owner, ean=None):
    with engine.connect() as connection:
        jobs = [dict(row) for row in connection.execute(select(targets).where(
            targets.c.store == store, targets.c.status != "synced", targets.c.status != "unmatched", targets.c.retry_at <= time.time(), *([targets.c.ean == ean] if ean else [])
        ).order_by(targets.c.ean).limit(10)).mappings()]
    for job in jobs:
        save_state(store, owner, lease_until=time.time() + 300)
        condition = (targets.c.store == store, targets.c.ean == job["ean"], targets.c.revision == job["revision"])
        try:
            if not job["product_id"] and job["quantity"] > 0:
                if not job["ean"].isdigit():
                    raise ConnectorError("unmatched", "Choisissez le produit et le format souhaités pour cet article.")
                try:
                    matches = await adapter.search_by_ean(job["ean"])
                except ConnectorError as error:
                    record_lookup(store, job["ean"], "unavailable", error.message)
                    raise
                lookup_status = "matched" if len(matches)==1 else "ambiguous" if matches else "not_found"
                lookup_message = "Produit confirmé par le service EAN du magasin." if len(matches)==1 else "Plusieurs produits correspondent à cet EAN. Choisissez le produit et le format." if matches else getattr(adapter,"ean_lookup_message","EAN non résolu dans ce magasin. Une recherche par nom est proposée.")
                record_lookup(store, job["ean"], lookup_status, lookup_message)
                if len(matches) != 1:
                    raise ConnectorError("unmatched", "Aucun produit unique confirmé par EAN. Recherchez et choisissez un produit.")
                product = matches[0]
                with engine.begin() as connection:
                    conflict = connection.execute(select(targets.c.ean).where(targets.c.store == store, targets.c.product_id == product.id, targets.c.ean != job["ean"])).first()
                    if conflict:
                        raise ConnectorError("unmatched", "Ce produit magasin est déjà associé à un autre EAN.")
                    saved = connection.execute(update(targets).where(*condition).values(product_id=product.id, product_name=product.name, price_cents=product.price_cents, candidates=[product.public()]))
                    if not saved.rowcount:
                        continue
                    adopt_product_name(connection, job["ean"], product.public(), store)
                job["product_id"] = product.id
            if job["product_id"]:
                # Absolute target after a fresh cart read makes retries after timeouts safe.
                await adapter.set_quantity(job["product_id"], job["quantity"])
            with engine.begin() as connection:
                values = {"status": "synced", "message": "Panier synchronisé.", "retry_at": 0}
                if job["quantity"] == 0:
                    values.update(product_id=None, product_name=None, price_cents=None, candidates=[])
                connection.execute(update(targets).where(*condition).values(**values))
        except ConnectorError as error:
            if error.status in {"auth_error", "otp_required", "access_denied", "rate_limited", "unavailable"}:
                raise
            with engine.begin() as connection:
                connection.execute(update(targets).where(*condition).values(status=error.status, message=error.message, retry_at=time.time() + 300))


def record_lookup(store, ean, status, message):
    with engine.begin() as connection:
        entry = insert_for(ean_lookups).values(store=store,ean=ean,status=status,message=message[:500],checked_at=time.time())
        connection.execute(entry.on_conflict_do_update(index_elements=[ean_lookups.c.store,ean_lookups.c.ean],set_={"status":status,"message":message[:500],"checked_at":time.time()}))
