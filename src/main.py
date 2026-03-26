import logging
import sys
import time

from config import load_config
from logger import SheetLogger
from metadata_store import init_db
from organizer import Organizer
from pipeline import process_file
from watcher import Watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def run(client_id: str):
    logger.info(f"DriveDesk starting — client: {client_id}")
    config = load_config(client_id)
    init_db()

    watcher      = Watcher(config)
    sheet_logger = SheetLogger(config)
    interval     = config["drive"].get("watch_interval_seconds", 60)

    organizer = None
    if config.get("organizer", {}).get("enabled", False):
        organizer = Organizer(config["drive"]["folder_id"])
        logger.info("Organizer enabled")

    logger.info(f"Watching folder: {config['drive']['folder_id']} (interval: {interval}s)")

    while True:
        try:
            new_files = watcher.poll()
            if new_files:
                logger.info(f"Detected {len(new_files)} new file(s)")
                for file_info in new_files:
                    file_info["folder_path"] = watcher.get_folder_path(file_info["file_id"])
                    process_file(file_info, config, sheet_logger, organizer)
            else:
                logger.debug("No new files")
        except Exception as e:
            logger.error(f"Poll error: {e}", exc_info=True)

        time.sleep(interval)


if __name__ == "__main__":
    client_id = sys.argv[1] if len(sys.argv) > 1 else "karas"
    run(client_id)
