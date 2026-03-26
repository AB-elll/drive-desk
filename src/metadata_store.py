"""
metadata_store — ファイル処理状態をGoogle Sheetsで管理（SQLite廃止）

使用シート（LOG_SPREADSHEET_ID 内に自動作成）:
  _DriveDesk_Files   : 処理済みファイル記録
  _DriveDesk_State   : key-value ステート（page_token, freee_token_json 等）
  _DriveDesk_Queue   : 通知キュー
"""
import json
import logging
import os
from datetime import datetime

from googleapiclient.discovery import build

from google_auth import get_credentials

logger = logging.getLogger(__name__)

_FILES_SHEET  = "_DriveDesk_Files"
_STATE_SHEET  = "_DriveDesk_State"
_QUEUE_SHEET  = "_DriveDesk_Queue"

_FILES_HEADERS = [
    "file_id", "file_name", "shared_at", "primary_date", "dates",
    "category", "subcategory", "confidence", "low_confidence",
    "status", "processor_refs", "error_message", "updated_at",
]
_STATE_HEADERS = ["key", "value"]
_QUEUE_HEADERS = ["id", "file_id", "type", "created_at", "notified"]

_sheets_client = None
_spreadsheet_id = ""
_processed_cache = set()


def _client():
    global _sheets_client, _spreadsheet_id
    if _sheets_client is None:
        _sheets_client = build("sheets", "v4", credentials=get_credentials())
        _spreadsheet_id = os.environ["LOG_SPREADSHEET_ID"]
    return _sheets_client


def _sid():
    _client()
    return _spreadsheet_id


def init_db():
    svc = _client()
    sid = _sid()
    meta = svc.spreadsheets().get(
        spreadsheetId=sid, fields="sheets.properties.title"
    ).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    for sheet_name, headers in [
        (_FILES_SHEET, _FILES_HEADERS),
        (_STATE_SHEET, _STATE_HEADERS),
        (_QUEUE_SHEET, _QUEUE_HEADERS),
    ]:
        if sheet_name not in existing:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            ).execute()
            svc.spreadsheets().values().update(
                spreadsheetId=sid, range=f"{sheet_name}!A1",
                valueInputOption="RAW", body={"values": [headers]},
            ).execute()
            logger.info(f"Created sheet: {sheet_name}")
    _rebuild_cache()


def _rebuild_cache():
    try:
        rows = _read_all(_FILES_SHEET, _FILES_HEADERS)
        for row in rows:
            if row.get("status") in ("processed", "unprocessable", "failed"):
                _processed_cache.add(row["file_id"])
        logger.info(f"Cache restored: {len(_processed_cache)} processed files")
    except Exception as e:
        logger.warning(f"Cache rebuild failed: {e}")


def _read_all(sheet_name, headers):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{sheet_name}!A1:Z",
    ).execute()
    rows = resp.get("values", [])
    if len(rows) <= 1:
        return []
    return [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in rows[1:]]


def _find_row_index(sheet_name, value):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{sheet_name}!A1:A",
    ).execute()
    for i, row in enumerate(resp.get("values", []), start=1):
        if row and row[0] == value:
            return i
    return None


def _append_row(sheet_name, values):
    _client().spreadsheets().values().append(
        spreadsheetId=_sid(), range=f"{sheet_name}!A1",
        valueInputOption="RAW", body={"values": [values]},
    ).execute()


def _update_row(sheet_name, row_idx, headers, data):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(),
        range=f"{sheet_name}!A{row_idx}:{chr(64 + len(headers))}{row_idx}",
    ).execute()
    current = (resp.get("values") or [[]])[0]
    current += [""] * (len(headers) - len(current))
    for k, v in data.items():
        if k in headers:
            current[headers.index(k)] = v
    _client().spreadsheets().values().update(
        spreadsheetId=_sid(), range=f"{sheet_name}!A{row_idx}",
        valueInputOption="RAW", body={"values": [current]},
    ).execute()


def upsert_file(file_id, **kwargs):
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    for k, v in list(kwargs.items()):
        if isinstance(v, (dict, list)):
            kwargs[k] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            kwargs[k] = ""
    row_idx = _find_row_index(_FILES_SHEET, file_id)
    if row_idx and row_idx > 1:
        _update_row(_FILES_SHEET, row_idx, _FILES_HEADERS, kwargs)
    else:
        kwargs["file_id"] = file_id
        row = [kwargs.get(h, "") for h in _FILES_HEADERS]
        _append_row(_FILES_SHEET, row)
    if kwargs.get("status") in ("processed", "unprocessable", "failed"):
        _processed_cache.add(file_id)


def get_file(file_id):
    rows = _read_all(_FILES_SHEET, _FILES_HEADERS)
    for row in rows:
        if row.get("file_id") == file_id:
            for field in ("dates", "processor_refs"):
                if row.get(field):
                    try:
                        row[field] = json.loads(row[field])
                    except Exception:
                        pass
            return row
    return None


def is_processed(file_id):
    return file_id in _processed_cache


def get_watcher_state(key):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{_STATE_SHEET}!A1:B",
    ).execute()
    for row in resp.get("values", []):
        if len(row) >= 2 and row[0] == key:
            return row[1]
    return None


def set_watcher_state(key, value):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{_STATE_SHEET}!A1:A",
    ).execute()
    for i, row in enumerate(resp.get("values", []), start=1):
        if row and row[0] == key:
            _client().spreadsheets().values().update(
                spreadsheetId=_sid(), range=f"{_STATE_SHEET}!B{i}",
                valueInputOption="RAW", body={"values": [[value]]},
            ).execute()
            return
    _append_row(_STATE_SHEET, [key, value])


def backup_freee_token(token_json):
    try:
        set_watcher_state("freee_token_json", token_json)
        logger.debug("freee token backed up to Sheets")
    except Exception as e:
        logger.warning(f"freee token backup failed: {e}")


def restore_freee_token():
    try:
        return get_watcher_state("freee_token_json")
    except Exception as e:
        logger.warning(f"freee token restore failed: {e}")
        return None


def add_notifier_queue(file_id, type):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{_QUEUE_SHEET}!A1:A",
    ).execute()
    next_id = max(0, len(resp.get("values", [])) - 1) + 1
    _append_row(_QUEUE_SHEET, [next_id, file_id, type, datetime.utcnow().isoformat(), "0"])


def get_pending_notifications(type):
    rows = _read_all(_QUEUE_SHEET, _QUEUE_HEADERS)
    return [r for r in rows if r.get("type") == type and r.get("notified") == "0"]


def mark_notified(ids):
    resp = _client().spreadsheets().values().get(
        spreadsheetId=_sid(), range=f"{_QUEUE_SHEET}!A1:A",
    ).execute()
    str_ids = [str(x) for x in ids]
    for i, row in enumerate(resp.get("values", []), start=1):
        if row and row[0] in str_ids:
            _client().spreadsheets().values().update(
                spreadsheetId=_sid(), range=f"{_QUEUE_SHEET}!E{i}",
                valueInputOption="RAW", body={"values": [["1"]]},
            ).execute()
