# Picnic and Chronodrive

Grocery Hub synchronizes the latest desired quantity of each associated shopping-list
item to store carts. It does not place orders, pay, select a delivery slot, clear an
entire store cart, or remove unrelated store products.

Picnic login, SMS, search, cart reads and cart updates use the pinned `picnic-api`
Node client. The API and worker images include Node and its locked dependency; no
additional container, public route or Node installation on the host is needed.
Chronodrive remains a Python adapter. Both store integrations are unofficial.

## Configure both server containers

Put these variables in the environment of **both the API and worker**, with identical
values. The provided production Compose file loads both from the same `.env` file.

```dotenv
PICNIC_USERNAME=your-account-email
PICNIC_PASSWORD=your-account-password
PICNIC_COUNTRY_CODE=FR
PICNIC_DEVICE_ID=grocery-hub

CHRONODRIVE_EMAIL=your-account-email
CHRONODRIVE_PASSWORD=your-account-password
CHRONODRIVE_STORE_ID=your-store-id
CHRONODRIVE_SITE_MODE=DRIVE
```

`CHRONODRIVE_USERNAME` is accepted when `CHRONODRIVE_EMAIL` is absent. An empty EMAIL
does not override a nonempty USERNAME. Keep the Picnic country and device ID stable
after verification. Changing credentials, country, device or Chronodrive store resets
the cached session and product associations for that store.

For a custom stack with services `grocery-api` and `grocery-web`, retain:

```yaml
environment:
  - API_INTERNAL_URL=http://grocery-api:8000
```

The web and API must share a Docker network. The **worker also needs outbound HTTPS**
access to both stores. In the provided production Compose file it joins `frontend`
as well as the internal `backend` network. Update all three images together: API,
worker and web. The scanner does not need rebuilding for this change.

Keep the UI behind your existing OAuth middleware. If exposing the API hostname for
the scanner, restrict that router to `/api/v1/items/scan`; the dashboard's connector
actions and shopping-list routes go through the authenticated web hostname.

## First connection

1. Recreate the API and worker after setting their environment variables.
2. Open the dashboard. The worker attempts a connection on its first pass. Use
   **Reconnecter** to explicitly retry a refused or expired login.
3. For Picnic, if **Code SMS requis** appears, click **Recevoir un code SMS**, then
   enter it and click **Vérifier le code**. Polling never sends SMS automatically.
4. For Chronodrive, select your desired store and initialize a cart on its website
   first. Grocery Hub will not switch stores or choose an already ordered cart.

Connection status is based on successful authenticated requests, not just credentials
being present. An old worker heartbeat is shown as stale. Auth failures pause automatic
attempts; reconnect explicitly after correcting credentials or completing checks on the
store's site. Transient errors and rate limits back off for five minutes.

## Matching and synchronization

Existing list items are queued when the updated API starts. New scans, repeated scans,
and removals atomically update the per-store desired quantity. The worker polls every
`CONNECTOR_POLL_SECONDS` (default 30) and handles up to ten pending items per store/pass.

Only one exact EAN match is accepted automatically. Picnic EANs are resolved through
the public regional QR/GTIN service, then the resulting product ID is verified through
the account's catalog. The anonymous resolver follows only approved Picnic hosts and
does not receive account tokens. A resolved ID does not guarantee availability for
every account. The UI distinguishes an unrecognized EAN, an unavailable product sheet,
and a resolver error. Previously skipped EANs are retried once after this update.

When an EAN cannot be matched, **Choisir chez Picnic** prepares searches in this order:

1. The associated Chronodrive product title and pack size (e.g. `Nutella 400 g`).
2. Open Food Facts name/brand and pack size if the first search returns no products.
3. An editable user search when neither source finds a suitable product.

Pack size is separate from the number of units on the shopping list. No name-based
result is silently added: check the product and format and click **Associer**. The
choice is saved for later scans. Chronodrive resolves EANs through its own catalog.
Open Food Facts metadata is cached for a day; negative lookups are cached for 15 minutes.

You can add a product by name without an EAN; the worker automatically searches that
name in connected shops and opens the saved candidates. Card refresh repeats this
text search without consulting OFF or treating the internal item key as a barcode. The
plus/minus controls update the shared list quantity and queue the associated cart
updates. Named items use an internal key in the existing `ean` column and are never
sent to a store as barcodes.

## Price comparison

The worker stores a fresh normalized cart snapshot after each successful sync. Use
**Actualiser les prix** to read current prices without changing cart quantities.
**Ma sélection** sums the actual cart line amounts of associated products only when
all desired quantities are synced and priced. It includes the discounts assigned to
those lines; the store may calculate promotions using other cart contents.

**Panier du magasin** is the store's reported order/checkout total and can include
unrelated products, deposits, credits and fees. The comparison panel shows coverage,
other cart lines, reported fees/deposits and the last snapshot time. Missing totals
remain unknown; snapshots older than five minutes or from a disconnected/different
account are not presented as current prices. Full-selection differences require both
lists to be complete. If an item is unavailable or unmatched, fresh reported shop-cart
totals can still be compared as an explicitly partial basket difference; missing
products are not treated as free. Pending, stale or disconnected carts are not compared.
Compare the selected pack sizes before interpreting an
amount difference as savings on equivalent products.

Two EANs cannot target the same store product. To change an existing association, remove
the item, wait for its removal to synchronize, then add it again and select a new match.

Retries read the current remote quantity, apply the difference and verify it. A store
timeout never rolls back the local list. Stock limits and partial writes remain visible
as per-item errors. **Synchroniser** requeues the current list, including unmatched or
failed items; it does not clear a cart or check out. It can reset quantities of associated
products that you manually changed in the store app. Unrelated products are left alone.

Successfully synced targets are not continually reapplied: a checkout or manual remote
change does not automatically recreate a completed cart. A new local change or an explicit
sync is required. Order detection and bidirectional cart import remain future work.

## Sessions, testing and protocol references

Unresolved cards reveal inline name search on request after automatic lookups finish.
Results sharing a verified barcode appear in one row with brand, coloured source
badges, store prices and pack sizes. Cards display up to ten rows. Only when more than
ten results exist, a button opens all results in a modal dialog inside the same page,
with the same selection controls, keyboard closing and focus return. Similar names never authorize
a cart association. Selecting a result replaces an unresolved item's EAN, name and
pack while retaining its shopping quantity, and queues store matching. Store results
without a barcode can still be selected explicitly: the original EAN is retained and
the shop's product ID is associated. Different shops can have different equivalents
without losing existing choices. A duplicate destination EAN cannot be overwritten.
Open Food Facts supplies no price.
Directory name searches are cached for five minutes. Failures in one source do not
suppress the other sources' results.

Card refresh retries the EAN in connected shops first, fetches fresh Open Food Facts
metadata without using its positive or negative cache, then automatically searches
unmatched connected shops with the returned brand, name and pack size. Candidates
open directly for explicit selection, without requiring the user to type a query.

The ordinary worker now performs this fallback after an unsuccessful barcode lookup,
not only after a card refresh. It uses cached positive OFF metadata when available,
otherwise fetches it, and saves name-search candidates without adding them to a cart.
The card reads these saved candidates and opens its list automatically when the worker
finishes. It stops if OFF has no metadata. Searches progressively drop brand/pack
constraints, try a generic description when provided and finally a product-name keyword
to expose equivalents. The full-results dialog reuses the current candidates.

Barcode lookup requests `product_type=all`, following OFF's redirects to Open Beauty
Facts, Open Pet Food Facts and Open Products Facts. A beauty-classified product must
not be treated as absent just because the food-only endpoint returns a 404.
See the [universal barcode API documentation](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/scanning-cosmetics-pet-food-and-other-products/).

The worker polls every 30 seconds by default, then makes the store requests. Queued
lookups and cart updates display a spinner beside the shop; errors and unresolved
products stop the spinner. The browser checks for updates every five seconds. Manual
search skips a shop while that item's automatic lookup is queued, and the sync/price
buttons are disabled while the shop has queued work to prevent duplicate requests.
List changes invalidate cart snapshots immediately. Comparison panels show a spinner
while pending changes, including zero-quantity removals, synchronize and await a new
snapshot. Old totals and other-item counts are hidden during that period. Confirmed
empty selections total zero; unrelated store cart contents are retained.

Session/refresh tokens are stored in the private `store_integrations.session` column in
PostgreSQL so API, worker and SMS verification share one session across restarts. They
are not returned by API responses or included in connector error messages. Protect
database access and backups as you protect the environment file; these session values
are not separately encrypted at rest.

Both integrations use unofficial web/mobile APIs and may require adjustment when stores
change their protocols. Tests use mocked HTTP responses and a temporary SQLite database;
they do not authenticate to real accounts. Set `TEST_DATABASE_URL` to a dedicated test
PostgreSQL database to exercise production persistence. Do not point tests at production.

Picnic search supports both RSC and JSON/PML (Fusion) pages. An upstream catalog or
parsing error is returned as HTTP 502 rather than a misleading HTTP 400, and includes
a controlled step name (page, action, bootstrap, client component or product sheet)
and the upstream HTTP status when available. Raw response bodies, URLs, credentials
and tokens are not included in errors. A catalog failure does not disconnect an
otherwise authenticated account. The logs include the same safe step/status fields.
Fusion parsing accepts the documented `layout.body` envelope and the bare `body`
layout. It traverses nested PML selling-unit tiles and excludes unavailable products;
the full envelope has a regression test through the pinned client's real HTTP path.
Logs also include an allowlisted `reason` (missing client configuration, missing action,
unsupported action response, RSC cycle or decoder/type error); no raw exception text is
logged. The bridge adds the configured app/device headers to the client's internal
bootstrap request so its search feature configuration uses the same identity as the
search page. This mismatch is covered by a regression test; a real account retry is
still required to establish whether it caused a particular production search failure.

Protocol sources used for implementation:

- [Picnic login and SMS verification](https://github.com/MRVDH/picnic-api/blob/main/src/domains/auth/service.ts)
- [Picnic cart protocol](https://github.com/MRVDH/picnic-api/blob/main/src/domains/cart/service.ts)
- [Maintained Picnic client](https://github.com/MRVDH/picnic-api)
- [Chronodrive OAuth and public frontend config](https://github.com/y-vex/chronodrive-mcp/blob/main/client.py)
- [Chronodrive product/cart contract](https://github.com/machintrucbidule/cOURSesDrive/blob/main/docs/api-chronodrive-contract.md)
