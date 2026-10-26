import hmac
import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status
from sqlalchemy import delete, select, update

from .config import settings
from .database import STORES, add_barcode, engine, enqueue, items, item_details, list_items, metadata, targets
from .connectors.base import ConnectorError
from .integration_service import initialize, operate, public_status
from .products import product_label
from .matching import search_suggestions
from .catalog import search_catalog, use_directory_identity, refresh_product, choose_store_product
from .schemas import ProductSearchRequest, ProductIdentityRequest, ProductSelectionRequest
from .schemas import HealthResponse, ScanRequest, ScanResponse, ShoppingItemResponse, StoreSearchRequest, StoreMatchRequest, VerificationRequest, NamedItemRequest, QuantityRequest

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    metadata.create_all(engine)
    initialize()
    yield
    engine.dispose()

app = FastAPI(
    title="Grocery Hub API",
    version="0.2.0",
    description="Self-hosted grocery list and multi-store synchronization API.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


def authenticate_scanner(device_id: str | None, authorization: str | None) -> str:
    if not device_id or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Scanner authentication required")

    supplied_token = authorization.removeprefix("Bearer ").strip()
    expected_token = settings.scanner_tokens().get(device_id)

    if not expected_token or not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scanner credentials")

    return device_id


@app.post("/api/v1/items/scan", response_model=ScanResponse)
def scan_item(
    payload: ScanRequest,
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
) -> ScanResponse:
    device_id = authenticate_scanner(x_device_id, authorization)

    save_scan(payload, device_id)
    return ScanResponse(
        accepted=True,
        barcode=payload.barcode,
        event_id=payload.event_id,
        device_id=device_id,
        message="Saved to shopping list. Store synchronization queued.",
    )


def save_scan(payload: ScanRequest, device_id: str) -> None:
    with engine.connect() as connection:
        label = connection.execute(select(items.c.label).where(items.c.ean == payload.barcode)).scalar_one_or_none()
    try:
        add_barcode(payload.barcode, payload.event_id, device_id, label or product_label(payload.barcode))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/v1/items", response_model=list[ShoppingItemResponse])
def get_items():
    return list_items()


@app.post("/api/v1/items", response_model=list[ShoppingItemResponse])
def create_item(payload: ScanRequest):
    save_scan(payload, "web")
    return list_items()


@app.delete("/api/v1/items/{ean}", status_code=204)
def remove_item(ean: str):
    with engine.begin() as connection:
        label = connection.execute(select(items.c.label).where(items.c.ean == ean)).scalar_one_or_none()
        result = connection.execute(delete(items).where(items.c.ean == ean))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Item not found")
        connection.execute(delete(item_details).where(item_details.c.ean == ean))
        enqueue(connection, ean, 0, label)


@app.post("/api/v1/items/named", response_model=list[ShoppingItemResponse])
def create_named_item(payload: NamedItemRequest):
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="Le nom du produit est vide.")
    key = "n" + hashlib.sha256(payload.event_id.encode()).hexdigest()[:31]
    add_barcode(key, payload.event_id, "web-named", label)
    return list_items()


@app.patch("/api/v1/items/{ean}", response_model=list[ShoppingItemResponse])
def set_item_quantity(ean: str, payload: QuantityRequest):
    with engine.begin() as connection:
        row = connection.execute(update(items).where(items.c.ean == ean).values(quantity=payload.quantity).returning(items)).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        enqueue(connection, ean, row["quantity"], row["label"])
    return list_items()


@app.get("/api/v1/integrations")
def integrations():
    return public_status()


async def store_action(store, action, **kwargs):
    try:
        return await operate(store, action, **kwargs)
    except ConnectorError as error:
        code = 409 if error.status in {"busy", "invalid_match"} else 404 if error.status == "not_found" else 502 if error.status in {"search_error", "ean_lookup_error"} else 400
        raise HTTPException(status_code=code, detail=error.message) from error


@app.get("/api/v1/items/{ean}/search-suggestions")
def item_search_suggestions(ean: str):
    try:
        return search_suggestions(ean)
    except ConnectorError as error:
        raise HTTPException(status_code=404, detail=error.message) from error


@app.post("/api/v1/items/{ean}/search")
async def search_item_catalog(ean: str, payload: ProductSearchRequest):
    try:
        return await search_catalog(ean, payload.query)
    except ConnectorError as error:
        raise HTTPException(status_code=404 if error.status == "not_found" else 422, detail=error.message) from error


@app.post("/api/v1/items/{ean}/identity", response_model=list[ShoppingItemResponse])
def set_item_identity(ean: str, payload: ProductIdentityRequest):
    try:
        use_directory_identity(ean, payload.code)
    except ConnectorError as error:
        raise HTTPException(status_code=404 if error.status == "not_found" else 409, detail=error.message) from error
    return list_items()


@app.post("/api/v1/items/{ean}/refresh")
async def refresh_item(ean: str):
    try:
        return await refresh_product(ean)
    except ConnectorError as error:
        raise HTTPException(status_code=404 if error.status == "not_found" else 409, detail=error.message) from error


@app.post("/api/v1/items/{ean}/select", response_model=list[ShoppingItemResponse])
def select_item(ean: str, payload: ProductSelectionRequest):
    try:
        choose_store_product(ean,payload.source,payload.product_id)
    except ConnectorError as error:
        raise HTTPException(status_code=404 if error.status == "not_found" else 409, detail=error.message) from error
    return list_items()


@app.post("/api/v1/integrations/{store}/connect")
async def connect_store(store: str):
    await store_action(store, "connect")
    return public_status()


@app.post("/api/v1/integrations/{store}/sync", status_code=202)
def request_sync(store: str):
    if store not in STORES:
        raise HTTPException(status_code=404, detail="Magasin inconnu.")
    with engine.begin() as connection:
        connection.execute(update(targets).where(targets.c.store == store).values(status="queued", retry_at=0, revision=targets.c.revision + 1))
    return {"message": "Synchronisation demandée. Le worker mettra à jour les articles associés."}


@app.post("/api/v1/integrations/{store}/search")
async def search_store(store: str, payload: StoreSearchRequest):
    return await store_action(store, "search", ean=payload.ean, query=payload.query)


@app.post("/api/v1/integrations/{store}/refresh")
async def refresh_prices(store: str):
    await store_action(store, "refresh")
    return public_status()


@app.post("/api/v1/integrations/{store}/match", status_code=202)
async def match_store(store: str, payload: StoreMatchRequest):
    await store_action(store, "match", ean=payload.ean, product_id=payload.product_id)
    return {"message": "Produit associé. Synchronisation en attente."}


@app.post("/api/v1/integrations/picnic/send-code")
async def send_picnic_code():
    # A code is sent only after an explicit UI action, never by the polling worker.
    await store_action("picnic", "send-code")
    return public_status()


@app.post("/api/v1/integrations/picnic/verify-code")
async def verify_picnic_code(payload: VerificationRequest):
    await store_action("picnic", "verify-code", code=payload.code)
    return public_status()
