from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class ScanRequest(BaseModel):
    barcode: str = Field(min_length=8, max_length=32, pattern=r"^[0-9]+$")
    event_id: str = Field(min_length=8, max_length=128)


class ScanResponse(BaseModel):
    accepted: bool
    barcode: str
    event_id: str
    device_id: str
    message: str


class ShoppingItemCreate(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    ean: str | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    kind: str = Field(default="generic", pattern="^(generic|exact)$")
