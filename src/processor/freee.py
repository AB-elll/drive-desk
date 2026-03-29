import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests

from .base import ProcessorPlugin, ProcessResult

logger = logging.getLogger(__name__)

TOKEN_PATH = Path.home() / ".config" / "drivedesk" / "freee_token.json"
FREEE_API_BASE = "https://api.freee.co.jp/api/1"
FREEE_AUTH_URL = "https://accounts.secure.freee.co.jp/public_api/token"

# 勘定科目候補のキーワードマッピング（AI抽出結果から近似マッチ）
ACCOUNT_KEYWORDS = {
    "消耗品費": ["消耗品", "文具", "コピー用紙", "トナー"],
    "食料品費": ["食品", "飲料", "コーヒー", "弁当", "サンドイッチ"],
    "交通費": ["電車", "バス", "タクシー", "交通"],
    "通信費": ["電話", "インターネット", "NTT", "携帯"],
    "水道光熱費": ["電気", "ガス", "水道"],
    "福利厚生費": ["薬", "医療", "健康"],
    "外注費": ["外注", "委託", "業務委託"],
    "広告宣伝費": ["広告", "宣伝", "チラシ"],
    "接待交際費": ["接待", "会食", "ギフト"],
}


class FreeePlugin(ProcessorPlugin):
    processor_type = "freee"

    def __init__(self, config: dict):
        super().__init__(config)
        self.company_id = int(os.environ.get("FREEE_COMPANY_ID", config.get("company_id", 0)))
        self.registration_mode = config.get("registration_mode", "finalize")
        self._token_cache: dict | None = None
        self._account_items_cache: list | None = None

    # ── 公開インターフェース ───────────────────────────────────

    def process(self, file_id: str, extracted_data: dict) -> ProcessResult:
        try:
            token = self._get_token()
            transactions = self._build_transactions(extracted_data)
            if not transactions:
                return ProcessResult(
                    success=False,
                    processor_type=self.processor_type,
                    refs=[],
                    error="No transactions to register",
                )

            refs = []
            for txn in transactions:
                deal_id = self._create_deal(token, txn)
                refs.append(str(deal_id))
                logger.info(f"freee deal created: {deal_id}")
                time.sleep(0.2)  # レート制限対策

            return ProcessResult(success=True, processor_type=self.processor_type, refs=refs)

        except Exception as e:
            logger.error(f"FreeePlugin error: {e}")
            return ProcessResult(success=False, processor_type=self.processor_type,
                                 refs=[], error=str(e))

    # ── トークン管理 ─────────────────────────────────────────

    def _get_token(self) -> str:
        if self._token_cache:
            return self._token_cache["access_token"]

        token_path = Path(os.environ.get("FREEE_TOKEN_PATH", str(TOKEN_PATH)))

        # ローカルファイルから読み込み
        if token_path.exists():
            data = json.loads(token_path.read_text())
            if data.get("expires_at", 0) > time.time() + 60:
                self._token_cache = data
                return data["access_token"]
            return self._refresh_token(data["refresh_token"], token_path)

        # ローカルなし → Sheets バックアップから復元
        from metadata_store import restore_freee_token
        backed_up = restore_freee_token()
        if backed_up:
            logger.info("freee token restored from Sheets backup")
            data = json.loads(backed_up)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(json.dumps(data))
            if data.get("expires_at", 0) > time.time() + 60:
                self._token_cache = data
                return data["access_token"]
            return self._refresh_token(data["refresh_token"], token_path)

        # 環境変数から直接取得（初回セットアップ用）
        access_token = os.environ.get("FREEE_ACCESS_TOKEN")
        if access_token:
            return access_token

        raise RuntimeError(
            "freee token not found. Set FREEE_ACCESS_TOKEN or run freee OAuth setup."
        )

    def _refresh_token(self, refresh_token: str, token_path: Path) -> str:
        client_id = os.environ["FREEE_CLIENT_ID"]
        client_secret = os.environ["FREEE_CLIENT_SECRET"]

        resp = requests.post(FREEE_AUTH_URL, data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        })
        resp.raise_for_status()
        data = resp.json()
        data["expires_at"] = time.time() + data.get("expires_in", 21600) - 300

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps(data))
        self._token_cache = data

        # Sheets にバックアップ
        from metadata_store import backup_freee_token
        backup_freee_token(json.dumps(data))

        return data["access_token"]

    # ── 取引データ構築 ────────────────────────────────────────

    def _build_transactions(self, extracted: dict) -> list[dict]:
        deal_type = self._deal_type(extracted)

        # カード明細・銀行明細: 日付が異なる複数取引 → 別々の deal
        transactions = extracted.get("transactions", [])
        if transactions:
            return [self._build_deal(t["date"], t["amount"], t.get("description", ""),
                                     t.get("account_candidate"), deal_type) for t in transactions]

        # 請求書・レシートの品目行: 同一 deal に複数 details
        line_items = extracted.get("line_items", [])
        if line_items:
            return [self._build_deal_with_line_items(extracted, line_items)]

        # フォールバック: 合計1行
        amount = (extracted.get("amount") or {}).get("total")
        date = extracted.get("primary_date") or datetime.today().strftime("%Y-%m-%d")
        description = extracted.get("description", "")
        account_candidate = extracted.get("account_candidate")

        if not amount:
            return []
        return [self._build_deal(date, amount, description, account_candidate, deal_type)]

    def _deal_type(self, extracted: dict) -> str:
        """抽出データから freee の取引種別を決定する"""
        return "income" if extracted.get("deal_type") == "income" else "expense"

    def _build_deal_with_line_items(self, extracted: dict, line_items: list) -> dict:
        """請求書の品目を1つの deal・複数 details として構築する"""
        date = extracted.get("primary_date") or datetime.today().strftime("%Y-%m-%d")
        deal_type = self._deal_type(extracted)
        details = []
        for item in line_items:
            details.append({
                "account_item_id": self._resolve_account_item(item.get("account_candidate")),
                "tax_code": 1,
                "amount": int(item["amount"]),
                "description": item.get("description", ""),
            })
        return {
            "company_id": self.company_id,
            "issue_date": date,
            "type": deal_type,
            "details": details,
        }

    def _build_deal(self, issue_date: str, amount: float, description: str,
                    account_candidate: str | None, deal_type: str = "expense") -> dict:
        account_item_id = self._resolve_account_item(account_candidate)
        return {
            "company_id": self.company_id,
            "issue_date": issue_date,
            "type": deal_type,
            "details": [{
                "account_item_id": account_item_id,
                "tax_code": 1,
                "amount": int(amount),
                "description": description,
            }],
        }

    def _resolve_account_item(self, candidate: str | None) -> int:
        items = self._get_account_items()
        if not candidate:
            return self._default_account_id(items)

        # 完全一致
        for item in items:
            if item["name"] == candidate:
                return item["id"]

        # キーワード部分一致
        for account_name, keywords in ACCOUNT_KEYWORDS.items():
            if any(kw in (candidate or "") for kw in keywords):
                for item in items:
                    if item["name"] == account_name:
                        return item["id"]

        return self._default_account_id(items)

    def _default_account_id(self, items: list) -> int:
        for item in items:
            if item["name"] == "消耗品費":
                return item["id"]
        return items[0]["id"] if items else 0

    # ── freee API呼び出し ─────────────────────────────────────

    def _get_account_items(self) -> list:
        if self._account_items_cache is not None:
            return self._account_items_cache
        token = self._get_token()
        resp = requests.get(f"{FREEE_API_BASE}/account_items",
                            params={"company_id": self.company_id},
                            headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        self._account_items_cache = resp.json().get("account_items", [])
        return self._account_items_cache

    def _create_deal(self, token: str, deal: dict) -> int:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if self.registration_mode == "draft":
            deal["receipt_ids"] = []  # 下書き扱いのマーカー

        resp = requests.post(f"{FREEE_API_BASE}/deals",
                             json=deal, headers=headers)

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            resp = requests.post(f"{FREEE_API_BASE}/deals",
                                 json=deal, headers=headers)

        # request/response を詳細ログに残す（エラー原因追跡用）
        logger.debug(
            "freee POST /deals status=%s request=%s response=%s",
            resp.status_code,
            json.dumps(deal, ensure_ascii=False),
            resp.text[:500],
        )

        if not resp.ok:
            raise RuntimeError(
                f"freee API error {resp.status_code}: {resp.text[:300]}"
            )

        return resp.json()["deal"]["id"]
