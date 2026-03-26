import logging
import os
from datetime import datetime

from classifier import classify
from extractor import extract
from logger import SheetLogger
from metadata_store import add_notifier_queue, upsert_file

logger = logging.getLogger(__name__)


def process_file(file_info: dict, config: dict, sheet_logger: SheetLogger):
    file_id   = file_info["file_id"]
    file_name = file_info["file_name"]
    mime_type = file_info["mime_type"]
    shared_at = file_info["shared_at"]
    local_path = file_info["local_path"]
    folder_path = file_info.get("folder_path", "/")

    logger.info(f"Processing: {file_name} ({mime_type})")

    # ── 1. 分類 ──────────────────────────────────────────────
    try:
        cls = classify(file_name, mime_type, folder_path, config)
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        upsert_file(file_id, status="unprocessable", error_message=str(e))
        add_notifier_queue(file_id, "unprocessable")
        sheet_logger.log(file_id, file_name, shared_at, None, None, None,
                         False, "unprocessable", None, str(e))
        _cleanup(local_path)
        return

    category    = f"{cls['category']}/{cls['subcategory']}"
    confidence  = cls["confidence"]
    low_conf    = cls["low_confidence"]
    primary_key = cls.get("primary_date_key")

    logger.info(f"Classified: {category} (confidence={confidence:.2f}, low={low_conf})")

    upsert_file(file_id,
                category=cls["category"],
                subcategory=cls["subcategory"],
                confidence=confidence,
                low_confidence=int(low_conf),
                status="pending")

    # ── 2. 抽出 ──────────────────────────────────────────────
    try:
        model = config.get("classifier", {}).get("model", "claude-sonnet-4-6")
        extracted = extract(local_path, mime_type, cls["subcategory"], primary_key, model)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        upsert_file(file_id, status="unprocessable", error_message=str(e))
        add_notifier_queue(file_id, "unprocessable")
        sheet_logger.log(file_id, file_name, shared_at, None, category, confidence,
                         low_conf, "unprocessable", None, str(e))
        _cleanup(local_path)
        return

    primary_date = extracted.get("primary_date")
    dates        = extracted.get("dates", {})
    transactions = extracted.get("transactions", [])
    logger.info(f"Extracted: primary_date={primary_date}, transactions={len(transactions)}")

    upsert_file(file_id,
                primary_date=primary_date,
                dates=dates,
                status="pending")

    # ── 3. プロセッサー（実装済みのみ実行）───────────────────
    processor_refs = {}
    failed_processors = []

    for proc_config in config.get("processors", []):
        proc_type = proc_config.get("type")
        try:
            plugin = _load_plugin(proc_type, proc_config)
            result = plugin.process(file_id, extracted)
            if result.success:
                processor_refs[proc_type] = result.refs
                logger.info(f"Processor [{proc_type}] OK: {result.refs}")
            else:
                raise Exception(result.error)
        except NotImplementedError:
            logger.info(f"Processor [{proc_type}] skipped (not implemented)")
        except Exception as e:
            logger.error(f"Processor [{proc_type}] failed: {e}")
            failed_processors.append(f"{proc_type}: {e}")

    # ── 4. 結果保存 ───────────────────────────────────────────
    if failed_processors:
        error_msg = " | ".join(failed_processors)
        upsert_file(file_id, status="failed",
                    processor_refs=processor_refs, error_message=error_msg)
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "failed", processor_refs, error_msg)
    else:
        upsert_file(file_id, status="processed", processor_refs=processor_refs)
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "processed", processor_refs, None)

    _cleanup(local_path)
    logger.info(f"Done: {file_name} -> {('failed' if failed_processors else 'processed')}")


def _load_plugin(proc_type: str, proc_config: dict):
    from processor.freee import FreeePlugin
    from processor.jdl_csv import JDLCsvPlugin
    mapping = {"freee": FreeePlugin, "jdl_csv": JDLCsvPlugin}
    cls = mapping.get(proc_type)
    if not cls:
        raise ValueError(f"Unknown processor: {proc_type}")
    return cls(proc_config)


def _cleanup(local_path: str):
    try:
        os.unlink(local_path)
    except Exception:
        pass
