import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status
from sqlalchemy import delete, select

from .config import settings
from .database import add_barcode, engine, items, list_items, metadata
from .products import product_label
from .schemas import HealthResponse, ScanRequest, ScanResponse, ShoppingItemResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    metadata.create_all(engine)
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
        message="Saved to shopping list. Store synchronization is not implemented yet.",
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
        result = connection.execute(delete(items).where(items.c.ean == ean))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Item not found")


@app.get("/api/v1/integrations")
def integrations():
    # Credentials alone cannot establish a connection while adapters are stubs.
    return [{
        "id": store, "name": name, "status": "not_implemented", "connected": False,
        "message": "Connecteur non implémenté : connexion et synchronisation des paniers indisponibles.",
    } for store, name in [("picnic", "Picnic"), ("chronodrive", "Chronodrive")]]
