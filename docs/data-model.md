# Data model

Core tables:
- `shopping_lists`
- `shopping_items` (`exact` or `generic`)
- `store_products`
- `item_store_matches`
- `price_observations`
- `cart_snapshots`
- `orders`
- `outbox_events`

Money is stored as integer minor units (cents).

The transactional outbox records a list mutation and its sync event in the same PostgreSQL transaction. The worker can retry later if Redis or a store API is unavailable.
