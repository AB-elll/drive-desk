"""
DriveDesk E2E テスト
テスト用レシートテキストを作成し、classify→extract→freee登録まで通す
"""
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

# .env 読み込み
from dotenv import load_dotenv
load_dotenv("clients/karas/.env")

sys.path.insert(0, "src")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    from config import load_config
    from metadata_store import init_db
    from pipeline import process_file
    from logger import SheetLogger

    config = load_config("karas")
    init_db()
    sheet_logger = SheetLogger(config)

    # ── テスト用レシートテキストファイルを作成 ──────────────────────
    receipt_text = """
領収書

店名: ヤマザキデイリーストア
住所: 東京都渋谷区xxx
日付: 2026年3月26日
レシートNo: 00123456

商品:
  コピー用紙 A4 500枚  980円
  ボールペン(10本)      550円
  クリアファイル        220円

小計: 1,750円
消費税(10%): 175円
合計: 1,925円

お支払い: 現金 2,000円
お釣り: 75円

ありがとうございました。
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     prefix="test_receipt_", delete=False,
                                     encoding="utf-8") as f:
        f.write(receipt_text)
        local_path = f.name

    logger.info(f"テストファイル作成: {local_path}")

    # ── ダミーのfile_infoを構築 ────────────────────────────────────
    file_info = {
        "file_id": f"test_e2e_{int(time.time())}",
        "file_name": "テスト領収書_20260326.txt",
        "mime_type": "text/plain",
        "shared_at": "2026-03-26T12:00:00",
        "local_path": local_path,
        "folder_path": "/",
    }

    logger.info("=" * 60)
    logger.info("E2E テスト開始")
    logger.info("=" * 60)

    try:
        process_file(file_info, config, sheet_logger)
        logger.info("E2E テスト完了")

        # ── SQLite結果確認 ────────────────────────────────────────
        import sqlite3
        conn = sqlite3.connect("drivedesk.db")
        row = conn.execute(
            "SELECT file_id, category, subcategory, confidence, status, "
            "primary_date, processor_refs, error_message "
            "FROM files WHERE file_id = ?",
            (file_info["file_id"],)
        ).fetchone()
        conn.close()

        if row:
            print("\n" + "=" * 60)
            print("【処理結果】")
            print(f"  file_id      : {row[0]}")
            print(f"  category     : {row[1]}/{row[2]}")
            print(f"  confidence   : {row[3]:.2f}" if row[3] else "  confidence   : N/A")
            print(f"  status       : {row[4]}")
            print(f"  primary_date : {row[5]}")
            refs = json.loads(row[6]) if row[6] else {}
            print(f"  processor_refs: {json.dumps(refs, ensure_ascii=False)}")
            if row[7]:
                print(f"  error        : {row[7]}")
            print("=" * 60 + "\n")

            if refs.get("freee"):
                print(f"✅ freee取引登録成功! deal_id(s): {refs['freee']}")
                print("   freeeダッシュボードで確認してください。")
            elif row[4] == "processed":
                print("✅ 処理完了（freee対象外の書類として処理）")
            else:
                print(f"❌ 処理失敗: {row[7]}")
        else:
            print("❌ DBにレコードが見つかりません")

    except Exception as e:
        logger.error(f"テスト例外: {e}", exc_info=True)
        raise
    finally:
        # クリーンアップ（process_fileが削除済みのはずだが念のため）
        if Path(local_path).exists():
            os.unlink(local_path)


if __name__ == "__main__":
    main()
