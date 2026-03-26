import json
import logging
import os
from datetime import datetime

from googleapiclient.discovery import build

from google_auth import get_credentials

logger = logging.getLogger(__name__)


class SheetLogger:
    def __init__(self, config: dict):
        self.spreadsheet_id = os.environ["LOG_SPREADSHEET_ID"]
        self.sheet_name = config["logger"].get("sheet_name", "DriveDesk Log")
        self._sheets = build("sheets", "v4", credentials=get_credentials())

    def log(self, file_id: str, file_name: str, shared_at: str,
            primary_date: str | None, category: str | None,
            confidence: float | None, low_confidence: bool,
            result: str, processor_refs: dict | None, error: str | None):
        row = [
            datetime.utcnow().isoformat(),
            file_name,
            file_id,
            shared_at,
            primary_date or "",
            category or "",
            confidence if confidence is not None else "",
            "TRUE" if low_confidence else "FALSE",
            result,
            json.dumps(processor_refs, ensure_ascii=False) if processor_refs else "",
            error or "",
        ]
        try:
            self._sheets.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
        except Exception as e:
            logger.error(f"Sheet logging failed: {e}")
