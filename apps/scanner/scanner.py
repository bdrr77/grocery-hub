#!/usr/bin/env python3
import glob
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import requests
from evdev import InputDevice, categorize, ecodes

API_URL = os.getenv("SCANNER_API_URL", "http://api:8000").rstrip("/")
DEVICE_GLOB = os.getenv(
    "SCANNER_DEVICE_GLOB",
    "/dev/input/by-id/*SCAN_KEYBOARD*event-kbd",
)
DEVICE_ID = os.getenv("SCANNER_DEVICE_ID", "scanner")
DEVICE_TOKEN = os.getenv("SCANNER_DEVICE_TOKEN", "")
SPOOL_PATH = Path(os.getenv("SCANNER_SPOOL_PATH", "/data/scanner-spool.db"))
RETRY_SECONDS = max(1, int(os.getenv("SCANNER_RETRY_SECONDS", "10")))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("grocery-scanner")

KEYMAP = {f"KEY_{i}": str(i) for i in range(10)}
STOP_EVENT = threading.Event()


def connect_db() -> sqlite3.Connection:
    SPOOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(SPOOL_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_scans (
            event_id TEXT PRIMARY KEY,
            barcode TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )
    db.commit()
    return db


def enqueue(barcode: str) -> str:
    event_id = str(uuid.uuid4())
    with connect_db() as db:
        db.execute(
            "INSERT INTO pending_scans(event_id, barcode, created_at) VALUES (?, ?, ?)",
            (event_id, barcode, int(time.time())),
        )
        db.commit()
    logger.info("Queued barcode %s as event %s", barcode, event_id)
    return event_id


def submit(event_id: str, barcode: str) -> None:
    if not DEVICE_TOKEN:
        raise RuntimeError("SCANNER_DEVICE_TOKEN is not configured")

    response = requests.post(
        f"{API_URL}/api/v1/items/scan",
        json={"barcode": barcode, "event_id": event_id},
        headers={
            "Authorization": f"Bearer {DEVICE_TOKEN}",
            "X-Device-ID": DEVICE_ID,
        },
        timeout=8,
    )
    response.raise_for_status()


def flush_once() -> int:
    delivered = 0
    with connect_db() as db:
        rows = db.execute(
            "SELECT event_id, barcode FROM pending_scans ORDER BY created_at ASC LIMIT 50"
        ).fetchall()

        for event_id, barcode in rows:
            try:
                submit(event_id, barcode)
            except Exception as exc:
                db.execute(
                    "UPDATE pending_scans SET attempts = attempts + 1, last_error = ? WHERE event_id = ?",
                    (str(exc)[:500], event_id),
                )
                db.commit()
                logger.warning("Delivery failed for %s: %s", event_id, exc)
                break
            else:
                db.execute("DELETE FROM pending_scans WHERE event_id = ?", (event_id,))
                db.commit()
                delivered += 1
                logger.info("Delivered barcode %s (%s)", barcode, event_id)

    return delivered


def retry_loop() -> None:
    while not STOP_EVENT.is_set():
        try:
            flush_once()
        except Exception as exc:
            logger.error("Spool flush error: %s", exc)
        STOP_EVENT.wait(RETRY_SECONDS)


def find_scanner() -> InputDevice:
    while not STOP_EVENT.is_set():
        matches = glob.glob(DEVICE_GLOB)
        if matches:
            dev = InputDevice(matches[0])
            logger.info("Using scanner device: %s", dev.path)
            return dev
        logger.warning("Scanner not found, retrying in 5 seconds")
        STOP_EVENT.wait(5)
    raise RuntimeError("Scanner stopped")


def scanner_loop() -> None:
    while not STOP_EVENT.is_set():
        try:
            dev = find_scanner()
            buffer = ""

            for event in dev.read_loop():
                if STOP_EVENT.is_set():
                    return
                if event.type != ecodes.EV_KEY:
                    continue

                key = categorize(event)
                if key.keystate != key.key_down:
                    continue

                if key.keycode == "KEY_ENTER":
                    barcode = buffer.strip()
                    buffer = ""
                    if barcode:
                        enqueue(barcode)
                        # Wake-up is timer based; attempt immediately as well.
                        try:
                            flush_once()
                        except Exception as exc:
                            logger.warning("Immediate delivery failed: %s", exc)
                    continue

                char = KEYMAP.get(key.keycode)
                if char:
                    buffer += char

        except OSError as exc:
            logger.error("Scanner disconnected: %s", exc)
            STOP_EVENT.wait(2)


def main() -> None:
    logger.info("Starting scanner device_id=%s api=%s", DEVICE_ID, API_URL)
    connect_db().close()

    retry_thread = threading.Thread(target=retry_loop, name="spool-retry", daemon=True)
    retry_thread.start()

    try:
        scanner_loop()
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        STOP_EVENT.set()
        retry_thread.join(timeout=2)


if __name__ == "__main__":
    main()
