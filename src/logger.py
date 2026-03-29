import json
import logging
import os
import traceback
from datetime import datetime

from googleapiclient.discovery import build

from google_auth import get_credentials

logger = logging.getLogger(__name__)


class SheetLogger:
    def __init__(self, config: dict):
        self.spreadsheet_id = os.environ["LOG_SPREADSHEET_ID"]
        self.sheet_name = config["logger"].get("sheet_name", "DriveDesk Log")
        self._sheets = build("sheets", "v4", credentials=get_credentials())
        self._debug = DebugLogger(self.spreadsheet_id, self._sheets)
        self._ensure_log_sheet()

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
                range=f"'{self.sheet_name}'!A1",
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
        except Exception as e:
            logger.error(f"Sheet logging failed: {e}")

    def _ensure_log_sheet(self):
        try:
            meta = self._sheets.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties.title",
            ).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if self.sheet_name not in titles:
                self._sheets.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": self.sheet_name}}}]},
                ).execute()
                headers = ["timestamp", "file_name", "file_id", "shared_at",
                           "primary_date", "category", "confidence",
                           "low_confidence", "result", "processor_refs", "error"]
                self._sheets.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{self.sheet_name}'!A1",
                    valueInputOption="RAW",
                    body={"values": [headers]},
                ).execute()
                logger.info(f"Created sheet: {self.sheet_name}")
        except Exception as e:
            logger.error(f"SheetLogger _ensure_log_sheet failed: {e}")

    @property
    def debug(self) -> "DebugLogger":
        return self._debug


class DebugLogger:
    """
    各パイプラインステップの詳細をSheetsに記録する。

    シート名: DriveDesk Debug
    列: timestamp | file_id | file_name | step | status | duration_ms | detail(JSON)
    """
    SHEET_NAME = "DriveDesk Debug"
    HEADER = ["timestamp", "file_id", "file_name", "step", "status", "duration_ms", "detail"]

    def __init__(self, spreadsheet_id: str, sheets_client):
        self.spreadsheet_id = spreadsheet_id
        self._sheets = sheets_client
        self._initialized = False

    def log(self, file_id: str, file_name: str, step: str,
            status: str, duration_ms: int, detail: dict):
        """
        step:   classify / extract / freee / jdl_csv / organizer / pipeline
        status: ok / error / skip / warn
        detail: 任意のJSONオブジェクト（入力・出力・エラーの全詳細）
        """
        self._ensure_sheet()
        row = [
            datetime.utcnow().isoformat(),
            file_id,
            file_name,
            step,
            status,
            duration_ms,
            json.dumps(detail, ensure_ascii=False, default=str),
        ]
        try:
            self._sheets.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{self.SHEET_NAME}!A1",
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
        except Exception as e:
            logger.error(f"DebugLogger write failed: {e}")

    def log_error(self, file_id: str, file_name: str, step: str,
                  exc: Exception, extra: dict | None = None):
        """例外をフルスタックトレース付きで記録する。"""
        detail = {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        if extra:
            detail.update(extra)
        self.log(file_id, file_name, step, "error", 0, detail)

    def _ensure_sheet(self):
        if self._initialized:
            return
        try:
            meta = self._sheets.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties.title",
            ).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if self.SHEET_NAME not in titles:
                self._sheets.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": self.SHEET_NAME}}}]},
                ).execute()
                # ヘッダー行を書き込む
                self._sheets.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{self.SHEET_NAME}!A1",
                    valueInputOption="RAW",
                    body={"values": [self.HEADER]},
                ).execute()
                logger.info(f"Created sheet: {self.SHEET_NAME}")
            self._initialized = True
        except Exception as e:
            logger.error(f"DebugLogger _ensure_sheet failed: {e}")
            self._initialized = True  # 失敗しても無限リトライしない
