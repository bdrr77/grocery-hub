import hmac

from fastapi import FastAPI, Header, HTTPException, status

from .config import settings
from .schemas import HealthResponse, ScanRequest, ScanResponse

app = FastAPI(
    title="Grocery Hub API",
    version="0.2.0",
    description="Self-hosted grocery list and multi-store synchronization API.",
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
async def scan_item(
    payload: ScanRequest,
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
) -> ScanResponse:
    device_id = authenticate_scanner(x_device_id, authorization)

    # V2 persistence step still to implement:
    # 1. atomically persist event_id + ShoppingItem mutation + outbox event
    # 2. enforce unique event_id for retry/idempotency safety
    # 3. return immediately
    # 4. worker synchronizes external stores asynchronously
    return ScanResponse(
        accepted=True,
        barcode=payload.barcode,
        event_id=payload.event_id,
        device_id=device_id,
        message="Barcode accepted; persistence/outbox implementation is the next step.",
    )
