# Architecture

## Production topology

Grocery Hub is deliberately split between the physical scanner host and the central
Docker server.

```text
                         HOME / LAN

 Raspberry Pi                               Docker server
 ┌───────────────────┐                     ┌────────────────────────┐
 │ USB barcode       │                     │ grocery-web            │
 │ scanner           │                     │ grocery-api            │
 │       │           │                     │ grocery-worker         │
 │       v           │     HTTPS/API       │                        │
 │ grocery-scanner   │────────────────────▶│ PostgreSQL             │
 │       │           │                     │ Redis                  │
 │       v           │                     └───────────┬────────────┘
 │ SQLite spool      │                                 │
 └───────────────────┘                           async adapters
                                                    │ │ │
                                           Bring! ──┘ │ └── Chronodrive
                                                     └──── Picnic
```

## Responsibilities

### Raspberry Pi

The Pi is a replaceable edge device. It is responsible only for:

- reading `evdev` events from the physical scanner;
- assembling the barcode;
- writing the scan to a durable local SQLite spool;
- delivering queued scans to the central API;
- authenticating with a per-device token.

The Pi must **not** contain:

- PostgreSQL/Redis;
- Bring/Picnic/Chronodrive credentials;
- product matching logic;
- authoritative shopping-list state.

### Docker server

The server owns all business state and integrations:

- web dashboard;
- REST API;
- PostgreSQL source of truth;
- Redis queue/cache;
- asynchronous worker;
- product resolution and matching;
- store cart synchronization;
- price observations and comparisons;
- order detection;
- notifications and confirmed global cart flushes.

## Reliability principles

1. PostgreSQL is authoritative.
2. A scan is durably queued on the Pi before network delivery.
3. API writes and outbox events will be committed atomically.
4. External store synchronization is eventually consistent.
5. Each adapter retries independently.
6. A store outage never rolls back the local shopping list.
7. A store cart unexpectedly becoming empty is a signal, not proof of checkout.
8. Global flush requires confirmation by default.
9. Checkout/payment remains outside Grocery Hub.

## Scanner request

A scanner sends:

```http
POST /api/v1/items/scan
Authorization: Bearer <device-token>
X-Device-ID: kitchen
Content-Type: application/json
```

```json
{
  "barcode": "5449000214911",
  "event_id": "5cf84594-..."
}
```

`event_id` is an idempotency key generated on the Pi. Once persistence is implemented,
the API must store/enforce uniqueness of that ID so network retries cannot duplicate a
scan event.

## Domain events

Initial vocabulary:

- `BARCODE_SCANNED`
- `ITEM_ADDED`
- `ITEM_REMOVED`
- `ITEM_QUANTITY_CHANGED`
- `ITEM_CHECKED`
- `STORE_MATCH_UPDATED`
- `STORE_SYNC_REQUESTED`
- `STORE_SYNC_SUCCEEDED`
- `STORE_SYNC_FAILED`
- `PRICE_OBSERVED`
- `CART_BECAME_EMPTY`
- `ORDER_DETECTED`
- `GLOBAL_FLUSH_CONFIRMED`
- `LIST_ARCHIVED`

## Store adapter interface

```python
class StoreAdapter:
    async def health(self): ...
    async def search_by_ean(self, ean: str): ...
    async def search(self, query: str): ...
    async def get_cart(self): ...
    async def add_item(self, external_product_id: str, quantity: int = 1): ...
    async def remove_item(self, external_product_id: str): ...
    async def set_quantity(self, external_product_id: str, quantity: int): ...
    async def clear_cart(self): ...
```

Order-aware stores may additionally implement:

```python
class OrderAwareStoreAdapter(StoreAdapter):
    async def get_orders(self): ...
    async def detect_new_order(self): ...
```

## Matching policy

Priority:

1. exact EAN/GTIN;
2. brand + product name + format/weight;
3. brand + product name;
4. product name only;
5. manual selection.

Suggested confidence thresholds:

- `1.00`: exact EAN;
- `>= 0.90`: auto-match;
- `0.75 - 0.89`: suggest and require confirmation;
- `< 0.75`: unmatched.
