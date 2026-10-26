# Connector evaluation — 2026-10-26

## Decision for the intended product

The goal is one simple interface to search/scan products, select the right pack in each
store, adjust quantities, manage store carts and compare totals. Language consistency
is secondary to supporting the current store protocol and showing truthful totals.

Recommended implementation: keep the existing Python API, persistence and worker;
use the maintained Node `picnic-api` client for Picnic's complete integration, and keep
Chronodrive behind a small Python adapter. Do not maintain separate handwritten Picnic
login/cart code alongside a package-based search indefinitely. A Node helper inside
the images is sufficient; an extra publicly exposed service is unnecessary.

The Python Picnic package is a credible alternative, not an absent or abandoned option.
However, it must pass a live French-account search test before choosing it instead of
the Node client. Its JSON/PML search could work when the server honors its older agent
version; this evaluation does not establish that it fails for every French account.

No production account was used, no remote cart was changed and no release was published
as part of this evaluation. All authenticated response fixtures are synthetic.

Follow-up implementation: Picnic's public French QR resolver was subsequently tested
anonymously with EAN `3017620422003` and returned product ID `s1035674`. The app now
uses that resolver and verifies the resulting product against the authenticated catalog.
This supersedes the earlier decision to skip EAN matching. It still does not establish
availability for every account. JSON/PML search fallback and controlled upstream-step
diagnostics were also added alongside RSC search support.

## Packages examined

| Candidate | Verified strengths | Limitations for this app |
| --- | --- | --- |
| `python-picnic-api2==2.0.1` | French region URLs; SMS verification and token rotation; JSON/PML search; EAN QR resolution; typed cart/add/remove results; raw pricing payload retained | Requires Python >=3.13; search unconditionally decodes JSON and does not support RSC; quantity overrides and some fees remain in raw payload; HTTP status and timeout guards need an application wrapper |
| `picnic-api==5.3.0` (Node) | RSC search and dynamic server-action discovery; normalized pack names/prices; cart mutations; decorator-aware quantities; checkout total, deposits, fees and discounts | Adds Node to API/worker images; no verified EAN resolver was established in the evaluated interface; normalizer turns a missing checkout total into zero, so completeness checks are mandatory |
| `y-vex/chronodrive-mcp` Python implementation | Reference for OAuth/PKCE, public application config, store-specific searches, cart reads and mutations | An MCP application rather than a proven drop-in client dependency; import-time environment/config/session side effects; whole-cart cleanup methods are not needed here; its simplified responses require independent validation and normalization |

The older MikeBrink Picnic client is not the Python candidate recommended here: its
legacy `/search` route is the route that failed in the deployed implementation.

## What was actually exercised

The Python wheel was downloaded from PyPI and installed in an isolated CPython 3.13.17
environment. The normal development virtual environment runs Python 3.12, which is why
an initial install of 2.0.1 was refused; the Docker images already use Python 3.13.

Seven offline Python-client checks passed:

1. French page-based search and product price/pack extraction.
2. Login requiring SMS verification, with rotation of the shared session token.
3. Reading native totals and preserving raw quantity overrides and fees.
4. Add/remove endpoint payloads and typed returned carts.
5. French EAN QR redirect resolution to an article ID.
6. An RSC response raises a JSON decoding error: this is a confirmed capability gap.
7. An HTTP 503 JSON body can become a cart with missing fields instead of raising:
   application code must validate status and response completeness.

Four offline checks using the installed Node package passed:

1. RSC search: load page, discover server action through bootstrap/chunks, invoke it
   with the page version, normalize prices and exclude unavailable products. CDN
   requests do not carry the account token.
2. Empty search and malformed-response handling in the adapter helper.
3. Cart quantity overrides, checkout total, deposit and fee normalization.
4. A missing upstream checkout total becomes zero: the app must distinguish an
   explicitly reported empty cart from an unknown price.

Chronodrive's actual public homepage was fetched without credentials. Its auth setting
is `API_AUTH_CLIENT_ID`, not the previously assumed standalone `CLIENT_ID`. The updated
local parser successfully extracted all four needed fields from that real HTML.
This validates discovery only, not authenticated login or cart writes. Existing
connector fixtures also cover PKCE, signed quantity deltas, stock rejection and avoiding
already ordered carts.

## Changes needed to deliver an easy cart/pricing UI

1. **One product list with quantity controls.** Provide plus/minus or an editable
   quantity; barcode scanning should not be the only way to change quantity. Allow
   search by name as well as EAN.
2. **Reusable product associations.** Show name, brand and pack size for each store.
   Save explicit selections, expose unavailable/missing matches, and do not silently
   substitute a different pack. Add per-kg/per-litre comparison where meaningful.
3. **Current cart snapshots.** Normalize store-specific fields into product lines,
   quantities, discounts, deposits, fees, native total, currency, completeness and
   fetch time. Preserve unknown values as unknown, not zero. The current Python
   adapters return quantity dictionaries and discard native cart totals; per-item
   search prices alone are not sufficient for accurate cart comparison.
4. **Two clearly distinguished totals.** The comparable-list estimate covers the
   same matched items and quantities, with missing counts shown. The actual store
   total comes from the store cart and may include unrelated products, promotions,
   deposits, credits and delivery fees. Do not declare a cheaper basket when coverage
   differs or prices are stale. Different packs require explicit user agreement.
5. **Visible progress and freshness.** Distinguish connected, syncing, partial,
   unavailable and stale. Show the last successful price refresh and an explicit
   refresh/sync action. A failed catalog request must not pretend authentication failed.

## Live acceptance criteria before a release claim

Use accounts configured on the server; never paste passwords or session tokens into
chat. For both stores verify connection, session reuse, search, a user-selected product,
add/increment/decrement/remove and read-back quantities. For Picnic also verify SMS and
search format for the French account. For Chronodrive verify the selected store and
active cart. Finally, compare the app's reported cart total to the store's own UI,
including an example with multiple units and a promotion/deposit/fee if available.

These live checks remain outstanding. Offline package checks establish capabilities
and failure modes, not successful access to the user's accounts.

## Sources and reproduction

- [Python package and documented API](https://pypi.org/project/python-picnic-api2/2.0.1/)
- [Python search implementation](https://github.com/codesalatdev/python-picnic-api/blob/main/src/python_picnic_api2/client.py)
- [Python cart model](https://github.com/codesalatdev/python-picnic-api/blob/main/src/python_picnic_api2/models/cart.py)
- [Node package](https://www.npmjs.com/package/picnic-api)
- [Node source](https://github.com/MRVDH/picnic-api)
- [Chronodrive implementation](https://github.com/y-vex/chronodrive-mcp)
- [Chronodrive product/cart protocol](https://github.com/machintrucbidule/cOURSesDrive/blob/main/docs/api-chronodrive-contract.md)

Evaluation scripts live in `evaluations/`; see its README for commands. The RSC helper
test uses the locally installed, pinned Node package from `apps/api/picnic-search`.
