import asyncio
import logging

from app.config import settings
from app.database import STORES, engine, metadata
from app.integration_service import initialize, operate

logging.basicConfig(level=logging.INFO)
# Upstream OAuth request URLs can contain provisional authentication tickets.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("grocery-worker")


async def process(store):
    try:
        await operate(store, background=True)
    except Exception as error:
        # Exception text may contain connection strings or upstream response data.
        log.warning("%s sync interrupted (%s); retrying next cycle", store, type(error).__name__)


async def main():
    while True:
        try:
            metadata.create_all(engine)
            initialize()
            break
        except Exception as error:
            log.warning("Waiting for database (%s)", type(error).__name__)
            await asyncio.sleep(5)
    log.info("Grocery connector worker started")
    while True:
        await asyncio.gather(*(process(store) for store in STORES))
        await asyncio.sleep(max(5, settings.connector_poll_seconds))


if __name__ == "__main__":
    asyncio.run(main())
