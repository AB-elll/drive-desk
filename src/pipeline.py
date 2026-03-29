import logging
import os
import time

from classifier import classify
from extractor import extract
from logger import SheetLogger
from metadata_store import add_notifier_queue, upsert_file

logger = logging.getLogger(__name__)


def process_file(file_info: dict, config: dict, sheet_logger: SheetLogger,
                 organizer=None):
    file_id   = file_info["file_id"]
    file_name = file_info["file_name"]
    mime_type = file_info["mime_type"]
    shared_at = file_info["shared_at"]
    local_path = file_info["local_path"]
    folder_path = file_info.get("folder_path", "/")
    dbg = sheet_logger.debug

    logger.info(f"Processing: {file_name} ({mime_type})")

    # ── 1. 分類 ──────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        cls, cls_meta = classify(file_name, mime_type, folder_path, config, local_path=local_path)
        dbg.log(file_id, file_name, "classify", "ok",
                int((time.monotonic() - t0) * 1000), {
                    "result": cls,
                    "claude_duration_ms": cls_meta.get("duration_ms"),
                    "raw_response": cls_meta.get("raw_response"),
                    "prompt_preview": cls_meta.get("prompt_preview"),
                })
    except Exception as e:
        dbg.log_error(file_id, file_name, "classify", e,
                      {"file_name": file_name, "mime_type": mime_type})
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
                file_name=file_name,
                shared_at=shared_at,
                category=cls["category"],
                subcategory=cls["subcategory"],
                confidence=confidence,
                low_confidence=int(low_conf),
                status="pending")

    # ── 2. 抽出 ──────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        model = config.get("classifier", {}).get("model", "claude-sonnet-4-6")
        extracted, ext_meta = extract(local_path, mime_type, cls["subcategory"], primary_key, model)
        dbg.log(file_id, file_name, "extract", "ok",
                int((time.monotonic() - t0) * 1000), {
                    "subcategory": cls["subcategory"],
                    "result": {k: v for k, v in extracted.items() if k != "raw_text"},
                    "raw_text_len": len(extracted.get("raw_text") or ""),
                    "claude_duration_ms": ext_meta.get("duration_ms"),
                    "raw_response": ext_meta.get("raw_response"),
                })
    except Exception as e:
        dbg.log_error(file_id, file_name, "extract", e,
                      {"subcategory": cls["subcategory"]})
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

    # management/legal は免許名・氏名・交付機関など日付以外のフィールドも保存
    _SKIP = {"primary_date", "dates", "transactions", "raw_text", "amount"}
    extracted_fields = (
        {k: v for k, v in extracted.items() if k not in _SKIP and v}
        if cls["category"] in ("management", "legal") else {}
    )

    upsert_file(file_id,
                primary_date=primary_date,
                dates=dates,
                extracted_fields=extracted_fields or None,
                status="pending")

    # ── 3. プロセッサー ───────────────────────────────────────
    # other/unknown は会計処理不要
    if cls["category"] == "other":
        upsert_file(file_id, status="unprocessable",
                    error_message="category=other/unknown: skipped")
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "unprocessable", {}, "other/unknown: skipped")
        dbg.log(file_id, file_name, "pipeline", "skip", 0,
                {"reason": "category=other/unknown"})
        if organizer:
            organizer.move(file_id, "other")
        _cleanup(local_path)
        logger.info(f"Skipped (other/unknown): {file_name}")
        return

    # management/legal は会計処理不要 → 抽出のみで processed
    if cls["category"] in ("management", "legal"):
        upsert_file(file_id, status="processed", processor_refs={})
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "processed", {}, None)
        dbg.log(file_id, file_name, "pipeline", "ok", 0,
                {"reason": f"{cls['category']}: extraction only, no accounting processor"})
        if organizer:
            organizer.move(file_id, cls["category"])
        _cleanup(local_path)
        logger.info(f"Done (management, no processor): {file_name}")
        return

    processor_refs = {}
    failed_processors = []

    for proc_config in config.get("processors", []):
        proc_type = proc_config.get("type")
        t0 = time.monotonic()
        try:
            plugin = _load_plugin(proc_type, proc_config)
            result = plugin.process(file_id, extracted,
                                    local_path=local_path, mime_type=mime_type)
            duration_ms = int((time.monotonic() - t0) * 1000)
            if result.success:
                processor_refs[proc_type] = result.refs
                dbg.log(file_id, file_name, proc_type, "ok", duration_ms, {
                    "refs": result.refs,
                    "transactions_count": len(transactions),
                })
                logger.info(f"Processor [{proc_type}] OK: {result.refs}")
            else:
                raise Exception(result.error)
        except NotImplementedError:
            dbg.log(file_id, file_name, proc_type, "skip",
                    int((time.monotonic() - t0) * 1000), {"reason": "not implemented"})
            logger.info(f"Processor [{proc_type}] skipped (not implemented)")
        except Exception as e:
            dbg.log_error(file_id, file_name, proc_type, e, {
                "extracted_summary": {k: v for k, v in extracted.items()
                                      if k not in ("raw_text", "transactions")},
            })
            logger.error(f"Processor [{proc_type}] failed: {e}")
            failed_processors.append(f"{proc_type}: {e}")

    # ── 4. 結果保存 ───────────────────────────────────────────
    if failed_processors:
        error_msg = " | ".join(failed_processors)
        upsert_file(file_id, status="failed",
                    processor_refs=processor_refs, error_message=error_msg)
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "failed", processor_refs, error_msg)
        dbg.log(file_id, file_name, "pipeline", "error", 0, {
            "failed_processors": failed_processors,
            "succeeded_processors": list(processor_refs.keys()),
        })
    else:
        upsert_file(file_id, status="processed", processor_refs=processor_refs)
        sheet_logger.log(file_id, file_name, shared_at, primary_date, category,
                         confidence, low_conf, "processed", processor_refs, None)
        dbg.log(file_id, file_name, "pipeline", "ok", 0, {
            "processors": list(processor_refs.keys()),
            "refs": processor_refs,
        })

    if organizer:
        t0 = time.monotonic()
        try:
            organizer.move(file_id, cls["category"])
            dbg.log(file_id, file_name, "organizer", "ok",
                    int((time.monotonic() - t0) * 1000), {"category": cls["category"]})
        except Exception as e:
            dbg.log_error(file_id, file_name, "organizer", e)

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
