import logging, time
logging.basicConfig(level=logging.INFO)
log=logging.getLogger("grocery-worker")
log.info("Grocery worker started")
while True:
    time.sleep(30)
