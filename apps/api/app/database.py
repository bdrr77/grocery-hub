"""Scan receipts and list updates commit in one transaction."""
from sqlalchemy import JSON, CheckConstraint, Column, Float, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
metadata = MetaData()
items = Table(
    "shopping_items", metadata,
    Column("ean", String(32), primary_key=True),
    Column("label", String(250), nullable=False),
    Column("quantity", Integer, nullable=False),
    CheckConstraint("quantity > 0", name="positive_quantity"),
)
receipts = Table(
    "scan_receipts", metadata,
    Column("device_id", String(128), primary_key=True),
    Column("event_id", String(128), primary_key=True),
    Column("barcode", String(32), nullable=False),
)

STORES = ("picnic", "chronodrive")
integrations = Table(
    "store_integrations", metadata,
    Column("store", String(32), primary_key=True),
    Column("status", String(32), nullable=False, default="pending"),
    Column("message", String(500), nullable=False, default="En attente du worker."),
    Column("checked_at", Float, nullable=False, default=0),
    Column("retry_at", Float, nullable=False, default=0),
    Column("session", JSON, nullable=False, default=dict),
    Column("fingerprint", String(64), nullable=False, default=""),
    Column("lease_owner", String(64), nullable=False, default=""),
    Column("lease_until", Float, nullable=False, default=0),
)
targets = Table(
    "store_sync_targets", metadata,
    Column("store", String(32), primary_key=True),
    Column("ean", String(32), primary_key=True),
    Column("quantity", Integer, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("label", String(250), nullable=False),
    Column("product_id", String(128), nullable=True),
    Column("product_name", String(250), nullable=True),
    Column("price_cents", Integer, nullable=True),
    Column("candidates", JSON, nullable=False, default=list),
    Column("status", String(32), nullable=False, default="queued"),
    Column("message", String(500), nullable=False, default="En attente de synchronisation."),
    Column("retry_at", Float, nullable=False, default=0),
)
cart_snapshots = Table(
    "store_cart_snapshots", metadata,
    Column("store", String(32), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("fetched_at", Float, nullable=False),
    Column("data", JSON, nullable=False),
)
ean_lookups = Table(
    "store_ean_lookups", metadata,
    Column("store", String(32), primary_key=True),
    Column("ean", String(32), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("message", String(500), nullable=False),
    Column("checked_at", Float, nullable=False),
)
product_facts = Table(
    "product_facts", metadata,
    Column("ean", String(32), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("fetched_at", Float, nullable=False),
)


def insert_for(table):
    return (sqlite_insert if engine.dialect.name == "sqlite" else pg_insert)(table)


def enqueue(connection, ean, quantity, label):
    for store in STORES:
        entry = insert_for(targets).values(store=store, ean=ean, quantity=quantity, label=label)
        connection.execute(entry.on_conflict_do_update(
            index_elements=[targets.c.store, targets.c.ean],
            set_={"quantity": quantity, "label": label, "revision": targets.c.revision + 1,
                  "status": "queued", "message": "En attente de synchronisation.", "retry_at": 0},
        ))


def add_barcode(barcode: str, event_id: str, device_id: str, label: str) -> None:
    insert = sqlite_insert if engine.dialect.name == "sqlite" else pg_insert
    with engine.begin() as connection:
        receipt = insert(receipts).values(device_id=device_id, event_id=event_id, barcode=barcode)
        created = connection.execute(
            receipt.on_conflict_do_nothing().returning(receipts.c.event_id)
        ).first()
        if created is None:
            original = connection.execute(select(receipts.c.barcode).where(
                receipts.c.device_id == device_id, receipts.c.event_id == event_id
            )).scalar_one()
            if original != barcode:
                raise ValueError("Event ID already used for a different barcode")
            return
        item = insert(items).values(ean=barcode, label=label, quantity=1)
        connection.execute(item.on_conflict_do_update(
            index_elements=[items.c.ean], set_={"quantity": items.c.quantity + 1}
        ))
        saved = connection.execute(select(items).where(items.c.ean == barcode)).mappings().one()
        enqueue(connection, barcode, saved["quantity"], saved["label"])


def list_items() -> list[dict]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            select(items).order_by(items.c.label, items.c.ean)
        ).mappings()]
