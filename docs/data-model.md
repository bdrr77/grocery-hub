# Data model

Implemented tables (created on API startup):

- `shopping_items`: EAN primary key, product label, positive quantity.
- `scan_receipts`: device ID + event ID primary key, barcode. Saved atomically with
  the item mutation, allowing safe delivery retries. Web additions use device `web`.

- `store_integrations`: connection state, heartbeat, backoff, credential fingerprint,
  leased operation lock and private session JSON. Session tokens remain server-side.
- `store_sync_targets`: store + EAN primary key, desired quantity, revision, confirmed
  product association, candidate products, observed price and per-item sync result.

The app supports one shared household list. Item changes and sync targets commit in
one transaction. Targets coalesce changes into the latest desired quantity; removal
retains a zero-quantity target until the associated store line has been removed.
Workers read the remote quantity before applying a delta, then verify the result.
Revision checks prevent completion of an older operation from erasing a newer change.

Planned tables:
- `shopping_lists`
- `store_products`
- `item_store_matches`
- `price_observations`
- `cart_snapshots`
- `orders`
- `outbox_events`

Money is stored as integer minor units (cents).

The current outbox is `store_sync_targets`, polled directly from PostgreSQL. Redis is
not required for cart processing. An append-only domain event history is future work.
