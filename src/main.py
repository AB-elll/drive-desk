import sys
import time
import logging
from pathlib import Path

from config import load_config
from metadata_store import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def run(client_id: str):
    logger.info(f"DriveDesk starting for client: {client_id}")
    config = load_config(client_id)
    init_db()

    # TODO: Google Drive API認証セットアップ後に有効化
    # from watcher import Watcher
    # watcher = Watcher(config)

    interval = config["drive"].get("watch_interval_seconds", 60)
    logger.info(f"Watch interval: {interval}s")
    logger.info("Watcher not yet available — waiting for Google API setup")

    while True:
        time.sleep(interval)


if __name__ == "__main__":
    client_id = sys.argv[1] if len(sys.argv) > 1 else "karas"
    run(client_id)
