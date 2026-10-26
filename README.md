# Grocery Hub

Self-hosted grocery list and multi-store cart synchronization platform.

Grocery Hub keeps the shopping-list experience simple while synchronizing items with
Bring!, Picnic and Chronodrive, comparing prices, and keeping the technical state in
your own infrastructure.

## Deployment architecture

Production is intentionally split into two independent stacks:

```text
Raspberry Pi + USB scanner                 Docker server
┌──────────────────────────┐              ┌─────────────────────────────┐
│ grocery-scanner          │── HTTPS ───▶ │ grocery-api                 │
│ evdev                    │              │ grocery-worker              │
│ local SQLite spool       │              │ grocery-web                 │
└──────────────────────────┘              │ PostgreSQL                  │
                                          │ Redis                       │
                                          └─────────────────────────────┘
                                                   │
                                                   ├── Bring!
                                                   ├── Picnic
                                                   └── Chronodrive
```

The Raspberry Pi is only an input device. It does **not** contain store credentials,
PostgreSQL, Redis or shopping-list state. If the server/network is unavailable, scans
are queued locally in SQLite and retried later.

PostgreSQL on the Docker server is the source of truth. Store integrations are
asynchronous projections of that state.

## Repository layout

```text
apps/
  api/          FastAPI backend
  worker/       background synchronization worker
  web/          Next.js dashboard / future PWA
  scanner/      Raspberry Pi evdev client

deploy/
  server/       production Docker Compose for the Docker server
  scanner/      production Docker Compose for the Raspberry Pi

docs/
  architecture.md
  data-model.md
  deployment.md
.github/workflows/
  release.yml   multi-arch GHCR build/publish
```

## Images

Releases publish these images to GitHub Container Registry:

```text
ghcr.io/<owner>/grocery-api
ghcr.io/<owner>/grocery-worker
ghcr.io/<owner>/grocery-web
ghcr.io/<owner>/grocery-scanner
```

Images are built for both `linux/amd64` and `linux/arm64`.

## Production deployment

See **[docs/deployment.md](docs/deployment.md)** for the complete server + Raspberry Pi
procedure, including GHCR, device permissions, health checks, upgrades, rollback and
PostgreSQL backups.

Quick overview:

```bash
# Docker server
cd deploy/server
cp .env.example .env
# edit .env
docker compose pull
docker compose up -d

# Raspberry Pi
cd deploy/scanner
cp .env.example .env
# edit .env
docker compose pull
docker compose up -d
```

## Current status

EAN entry in the web dashboard and authenticated scanner submissions save to a shared
PostgreSQL shopping list. Repeated additions increase quantity; delivery retries are
idempotent per device/event ID. Items can be removed, and the list survives restarts.
Product names come from Open Food Facts when available; unknown barcodes are still saved.

Picnic and Chronodrive connectors support login, search, verified EAN matches, manual
product selection and background cart quantity synchronization. Picnic SMS verification
is available in the dashboard. A database-backed outbox keeps scans independent of
store outages. Connection cards show configuration, verification, connection and worker
errors, with per-item sync results. Bring integration and order detection remain planned.
These store APIs are unofficial; account-specific login and catalog behavior must be
verified against your accounts after deployment. See [connector setup](docs/connectors.md).

The browser uses same-origin API routes through Next.js. Docker Compose sets the
server-only `API_INTERNAL_URL` at runtime; a public API build-time URL is unnecessary.
See [deployment instructions](docs/deployment.md) to rebuild and deploy the changes.

Run API regression tests after installing `apps/api/requirements-dev.txt`:

```bash
cd apps/api
python -m unittest discover -s tests -v
```

Tests use a temporary SQLite database by default. Set `TEST_DATABASE_URL` to a dedicated
PostgreSQL test database to exercise production SQL (the tests reset its app tables).
