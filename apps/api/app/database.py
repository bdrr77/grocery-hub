"""Scan receipts and list updates commit in one transaction."""
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table, create_engine, select
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


def list_items() -> list[dict]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            select(items).order_by(items.c.label, items.c.ean)
        ).mappings()]
