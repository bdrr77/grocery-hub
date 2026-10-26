from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class ShoppingItemResponse(BaseModel):
    ean: str
    label: str
    quantity: int


class StoreSearchRequest(BaseModel):
    ean: str = Field(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    query: str = Field(min_length=1, max_length=200)


class StoreMatchRequest(BaseModel):
    ean: str = Field(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    product_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class VerificationRequest(BaseModel):
    code: str = Field(min_length=4, max_length=10, pattern=r"^[0-9]+$")


class NamedItemRequest(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    event_id: str = Field(min_length=8, max_length=128)


class QuantityRequest(BaseModel):
    quantity: int = Field(ge=1, le=999)


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
