# Deployment guide

Grocery Hub uses two separate Docker Compose deployments:

- **Docker server:** API, worker, web UI, PostgreSQL and Redis.
- **Raspberry Pi:** scanner client only.

This is the recommended production architecture.

---

## 1. Prerequisites

### Docker server

Install Docker Engine with the Compose plugin and ensure the host can pull images from
`ghcr.io`.

Recommended resources for the initial deployment:

- 2 CPU cores
- 2 GB RAM or more
- persistent storage for PostgreSQL

PostgreSQL and Redis are deliberately **not exposed** on host ports by the production
Compose file.

### Raspberry Pi

Install Docker Engine + Compose plugin, connect the USB barcode scanner, then verify
that Linux sees it:

```bash
ls -l /dev/input/by-id/
```

You should see an `event-kbd` symlink for the scanner. Use its naming pattern for
`SCANNER_DEVICE_GLOB`.

Check the architecture if useful:

```bash
uname -m
```

The release workflow publishes both `linux/amd64` and `linux/arm64` images.

---

## 2. Publish images to GHCR

The GitHub Actions workflow `.github/workflows/release.yml` builds all four application
images and pushes them to GHCR.

Create and push a semantic version tag:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow publishes, for example:

```text
ghcr.io/<owner>/grocery-api:0.2.0
ghcr.io/<owner>/grocery-api:0.2
ghcr.io/<owner>/grocery-api:latest
```

and the equivalent tags for `worker`, `web`, and `scanner`.

If the packages are private, authenticate each deployment host before pulling:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

Use a token with permission to read packages. Public packages do not require a registry
login.

---

## 3. Deploy the Docker server

Copy the server deployment directory to the Docker host, or clone the repository there:

```bash
cd deploy/server
cp .env.example .env
```

Edit `.env`.

At minimum configure:

```dotenv
GHCR_OWNER=your-github-user-or-org
GROCERY_VERSION=0.2.0
POSTGRES_PASSWORD=<long-random-password>
DATABASE_URL=postgresql+psycopg://grocery:<same-password>@postgres:5432/grocery
NEXT_PUBLIC_API_URL=https://grocery-api.example.com
SCANNER_DEVICE_TOKENS=kitchen:<long-random-device-token>
```

> `POSTGRES_PASSWORD` and the password embedded in `DATABASE_URL` must match.

Start the stack:

```bash
docker compose pull
docker compose up -d
```

Inspect status:

```bash
docker compose ps
```

Follow logs:

```bash
docker compose logs -f api worker web
```

Check API health locally on the Docker server:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

The web UI is exposed on port `3000` and the API on port `8000`.

### Reverse proxy / TLS

For a home server already using Caddy, Traefik, Nginx Proxy Manager, etc., route your
chosen hostnames to:

```text
web -> server:3000
api -> server:8000
```

Prefer HTTPS for the API URL used by the Raspberry Pi. Do not expose PostgreSQL or
Redis.

The provided Compose file intentionally does not impose a reverse proxy because many
self-hosted environments already have one.

---

## 4. Test scanner authentication before deploying the Pi

From a machine that can reach the API:

```bash
curl -X POST 'https://grocery-api.example.com/api/v1/items/scan' \
  -H 'Authorization: Bearer YOUR_DEVICE_TOKEN' \
  -H 'X-Device-ID: kitchen' \
  -H 'Content-Type: application/json' \
  -d '{"barcode":"5449000214911","event_id":"manual-test-1"}'
```

A valid device should receive an accepted response. An unknown device or bad token
returns HTTP 401.

---

## 5. Deploy the Raspberry Pi scanner

On the Pi:

```bash
cd deploy/scanner
cp .env.example .env
```

Edit `.env` and set:

```dotenv
GHCR_OWNER=your-github-user-or-org
GROCERY_VERSION=0.2.0
SCANNER_API_URL=https://grocery-api.example.com
SCANNER_DEVICE_ID=kitchen
SCANNER_DEVICE_TOKEN=<same-token-as-server>
SCANNER_DEVICE_GLOB=/dev/input/by-id/*SCAN_KEYBOARD*event-kbd
```

Then:

```bash
docker compose pull
docker compose up -d
```

Follow scanner logs:

```bash
docker compose logs -f scanner
```

Scan one barcode. A healthy flow should show the barcode being queued and then delivered
to the API.

---

## 6. Offline behaviour of the scanner

The Raspberry Pi stores every scan in a local SQLite queue before attempting delivery.
The queue is persisted in the `scanner_data` Docker volume.

Flow:

```text
barcode scanned
      |
      v
SQLite spool on Pi
      |
      +---- API unavailable ---> keep queued
      |
      +---- API available -----> POST /api/v1/items/scan
                                      |
                                      v
                               remove from spool
```

Each scan has an `event_id` idempotency key. The server persistence layer should enforce
that key when the database implementation is added, allowing safe retries without
creating duplicate shopping-list events.

You can therefore reboot the server, lose Wi-Fi temporarily, or restart the scanner
container without intentionally losing scans.

---

## 7. Adding more scanners

Generate a different token per device and add it to the server variable:

```dotenv
SCANNER_DEVICE_TOKENS=kitchen:tokenA,garage:tokenB
```

On each Raspberry Pi set the matching pair:

```dotenv
SCANNER_DEVICE_ID=garage
SCANNER_DEVICE_TOKEN=tokenB
```

Restart the API after changing the server configuration:

```bash
docker compose up -d api
```

---

## 8. Upgrades

Pin production to a released version rather than relying permanently on `latest`:

```dotenv
GROCERY_VERSION=0.3.0
```

Then upgrade the Docker server:

```bash
cd deploy/server
docker compose pull
docker compose up -d
```

Upgrade the Raspberry Pi independently:

```bash
cd deploy/scanner
docker compose pull
docker compose up -d
```

This split lets the central application evolve without requiring a scanner restart and
vice versa.

---

## 9. Rollback

Change `GROCERY_VERSION` back to the previous known-good release, for example:

```dotenv
GROCERY_VERSION=0.2.0
```

Then:

```bash
docker compose pull
docker compose up -d
```

Database migrations must eventually be designed to support the project's documented
rollback policy. Until migrations are implemented, do not assume an application image
rollback can reverse a future schema migration.

---

## 10. PostgreSQL backup

Create a logical backup from the Docker server:

```bash
cd deploy/server
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  > grocery-$(date +%Y%m%d-%H%M%S).dump
```

If your shell does not have those environment variables loaded, substitute the values
from `.env` explicitly.

Also back up the `.env` file securely; it contains secrets and must not be committed.

---

## 11. Security checklist

Before exposing the service outside your LAN:

- use HTTPS;
- use a unique random token per scanner;
- keep `.env` files out of Git;
- do not expose ports for PostgreSQL or Redis;
- prefer pinned release tags in production;
- keep GHCR packages public only if that is intentional;
- put the web dashboard behind your normal authentication/reverse-proxy policy until
  Grocery Hub has its own user authentication.

Store credentials for Bring/Picnic/Chronodrive should exist only on the Docker server,
never on the scanner Pi.

---

## 12. Troubleshooting

### Scanner is not found

On the Pi:

```bash
ls -l /dev/input/by-id/
docker compose exec scanner ls -l /dev/input/by-id/
```

Verify `SCANNER_DEVICE_GLOB` matches the actual scanner symlink.

### Scanner logs HTTP 401

Check that:

- `SCANNER_DEVICE_ID` exists in server `SCANNER_DEVICE_TOKENS`;
- its token matches exactly;
- the API container was recreated after changing `.env`.

```bash
cd deploy/server
docker compose up -d --force-recreate api
```

### Scans remain queued

Check connectivity from the Raspberry Pi:

```bash
curl -v https://grocery-api.example.com/health
```

Then inspect scanner logs:

```bash
docker compose logs -f scanner
```

Queued scans remain in the persistent scanner volume and will be retried.

### API is unhealthy

```bash
cd deploy/server
docker compose ps
docker compose logs --tail=200 api postgres redis
```
