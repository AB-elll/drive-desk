"""
JDL IBEX 仕訳CSVプロセッサー

JDL IBEX会計（3桁勘定科目コード）形式のCSVを生成し、
指定のGoogle Driveフォルダに月次ファイルとしてアップロードする。

フォーマット仕様:
- 文字コード: Shift-JIS
- ヘッダー: 全角項目名
- 列構成: 日付,借方科目名称,貸方科目名称,金額,摘要,借方課区,貸方課区
- 日付形式: YYYY/MM/DD
"""
import csv
import io
import logging
import os
from datetime import datetime
from pathlib import Path

from .base import ProcessorPlugin, ProcessResult

logger = logging.getLogger(__name__)

# DriveDesk勘定科目候補 → JDL科目名マッピング
JDL_ACCOUNT_MAP = {
    "消耗品費":   "消耗品費",
    "食料品費":   "福利厚生費",
    "交通費":     "旅費交通費",
    "通信費":     "通信費",
    "水道光熱費": "水道光熱費",
    "福利厚生費": "福利厚生費",
    "外注費":     "外注費",
    "広告宣伝費": "広告宣伝費",
    "接待交際費": "交際費",
    "雑費":       "雑費",
}

DEFAULT_DEBIT_ACCOUNT  = "消耗品費"
DEFAULT_CREDIT_ACCOUNT = "未払金"
DEFAULT_TAX_CODE       = "課税仕入10%"


class JDLCsvPlugin(ProcessorPlugin):
    processor_type = "jdl_csv"

    def __init__(self, config: dict):
        super().__init__(config)
        self.output_folder_id = os.environ.get(
            "JDL_OUTPUT_FOLDER_ID", config.get("output_folder_id", "")
        )
        self.schedule = config.get("schedule", "monthly")  # monthly / immediate

    def process(self, file_id: str, extracted_data: dict) -> ProcessResult:
        try:
            rows = self._build_rows(extracted_data)
            if not rows:
                return ProcessResult(
                    success=False,
                    processor_type=self.processor_type,
                    refs=[],
                    error="No transactions to export",
                )

            if not self.output_folder_id:
                path = self._save_local(rows, extracted_data)
                logger.info(f"JDL CSV saved locally: {path}")
                return ProcessResult(
                    success=True,
                    processor_type=self.processor_type,
                    refs=[path],
                )

            drive_file_id = self._upload_to_drive(rows, extracted_data)
            logger.info(f"JDL CSV uploaded to Drive: {drive_file_id}")
            return ProcessResult(
                success=True,
                processor_type=self.processor_type,
                refs=[drive_file_id],
            )

        except Exception as e:
            logger.error(f"JDLCsvPlugin error: {e}")
            return ProcessResult(
                success=False,
                processor_type=self.processor_type,
                refs=[],
                error=str(e),
            )

    # ── CSV行構築 ────────────────────────────────────────────────

    def _build_rows(self, extracted: dict) -> list[dict]:
        transactions = extracted.get("transactions", [])
        if transactions:
            return [self._build_row(
                t["date"], t["amount"],
                t.get("description", ""), t.get("account_candidate"),
            ) for t in transactions]

        amount = (extracted.get("amount") or {}).get("total")
        if not amount:
            return []

        date_str = extracted.get("primary_date") or datetime.today().strftime("%Y-%m-%d")
        return [self._build_row(
            date_str, amount,
            extracted.get("description", "") or "",
            extracted.get("account_candidate"),
        )]

    def _build_row(self, date_str: str, amount: float,
                   description: str, account_candidate: str | None) -> dict:
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d")
            jdl_date = d.strftime("%Y/%m/%d")
        except ValueError:
            jdl_date = datetime.today().strftime("%Y/%m/%d")

        debit = JDL_ACCOUNT_MAP.get(account_candidate or "", DEFAULT_DEBIT_ACCOUNT)

        return {
            "日付":         jdl_date,
            "借方科目名称": debit,
            "貸方科目名称": DEFAULT_CREDIT_ACCOUNT,
            "金額":         int(amount),
            "摘要":         (description or "")[:50],
            "借方課区":     DEFAULT_TAX_CODE,
            "貸方課区":     "",
        }

    # ── CSV生成 ──────────────────────────────────────────────────

    _FIELDNAMES = ["日付", "借方科目名称", "貸方科目名称", "金額", "摘要", "借方課区", "貸方課区"]

    def _to_csv_bytes(self, rows: list[dict]) -> bytes:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self._FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().encode("shift-jis", errors="replace")

    def _filename(self, extracted: dict) -> str:
        date_str = extracted.get("primary_date") or datetime.today().strftime("%Y-%m-%d")
        ym = date_str[:7].replace("-", "")
        return f"jdl_journal_{ym}.csv"

    # ── ローカル保存（output_folder_id未設定時） ─────────────────

    def _save_local(self, rows: list[dict], extracted: dict) -> str:
        output_dir = Path.home() / ".config" / "drivedesk" / "jdl_export"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / self._filename(extracted)

        if self.schedule == "monthly" and path.exists():
            existing_text = path.read_bytes().decode("shift-jis", errors="replace")
            existing_rows = list(csv.DictReader(io.StringIO(existing_text)))
            rows = existing_rows + rows

        path.write_bytes(self._to_csv_bytes(rows))
        return str(path)

    # ── Google Drive アップロード ─────────────────────────────────

    def _upload_to_drive(self, rows: list[dict], extracted: dict) -> str:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from google_auth import get_credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds = get_credentials()
        drive = build("drive", "v3", credentials=creds)
        filename = self._filename(extracted)

        existing_id = None
        if self.schedule == "monthly":
            results = drive.files().list(
                q=(f"name='{filename}' and '{self.output_folder_id}' in parents"
                   " and trashed=false"),
                fields="files(id)",
            ).execute()
            files = results.get("files", [])
            if files:
                existing_id = files[0]["id"]

        if existing_id:
            raw = drive.files().get_media(fileId=existing_id).execute()
            existing_rows = list(csv.DictReader(
                io.StringIO(raw.decode("shift-jis", errors="replace"))
            ))
            rows = existing_rows + rows

        media = MediaIoBaseUpload(io.BytesIO(self._to_csv_bytes(rows)), mimetype="text/csv")

        if existing_id:
            result = drive.files().update(
                fileId=existing_id, media_body=media
            ).execute()
        else:
            result = drive.files().create(
                body={"name": filename, "parents": [self.output_folder_id]},
                media_body=media, fields="id",
            ).execute()

        return result["id"]
