# Data model

Implemented tables (created on API startup):

- `shopping_items`: EAN primary key, product label, positive quantity.
- `scan_receipts`: device ID + event ID primary key, barcode. Saved atomically with
  the item mutation, allowing safe delivery retries. Web additions use device `web`.

The initial app supports one shared household list. Store synchronization is not yet
implemented; it does not currently write outbox events or remote carts.

Planned tables:
- `shopping_lists`
- `store_products`
- `item_store_matches`
- `price_observations`
- `cart_snapshots`
- `orders`
- `outbox_events`

Money is stored as integer minor units (cents).

The planned transactional outbox will record a list mutation and its sync event in the same PostgreSQL transaction so the worker can retry when Redis or a store API is unavailable.
