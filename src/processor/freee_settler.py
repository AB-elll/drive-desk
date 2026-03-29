"""
freee 消し込み（売掛金・買掛金の決済消込）

bank_statement 処理後に呼び出す。
抽出した入出金と未決済の income/expense deals を金額で突合し、
deal description を更新してメタデータに記録する。

現プラン制約:
  - payments API (/deals/{id}/payments) は未対応 → description 更新で代替
  - 将来プランアップグレード時は _settle_via_api() で完全消し込み可能
"""
import logging
import time
from datetime import datetime, timedelta

import requests

FREEE_API_BASE = "https://api.freee.co.jp/api/1"
logger = logging.getLogger(__name__)

# 金額突合の許容誤差（円）
AMOUNT_TOLERANCE = 1
# 日付突合の許容幅（日）
DATE_TOLERANCE_DAYS = 45


class FreeeSettler:
    def __init__(self, company_id: int):
        self.company_id = company_id

    def settle_from_bank_statement(self, token: str, extracted_data: dict) -> list[dict]:
        """
        銀行明細の入金データから未決済 income deals を消し込む。
        戻り値: 消し込み結果のリスト [{"deal_id": int, "amount": int, "status": "settled"|"matched"}]
        """
        transactions = extracted_data.get("transactions", [])
        # 入金（正の金額）のみ対象
        credits = [t for t in transactions if float(t.get("amount", 0)) > 0]
        if not credits:
            logger.info("FreeeSettler: no credit transactions found")
            return []

        unsettled = self._get_unsettled_deals(token, "income")
        if not unsettled:
            logger.info("FreeeSettler: no unsettled income deals")
            return []

        results = []
        for credit in credits:
            match = self._find_match(credit, unsettled)
            if match:
                result = self._settle(token, match, credit)
                results.append(result)
                # 同じ deal に二重消し込みしないよう除外
                unsettled = [d for d in unsettled if d["id"] != match["id"]]

        logger.info(f"FreeeSettler: {len(results)} deals matched from {len(credits)} credits")
        return results

    # ── 突合ロジック ─────────────────────────────────────────

    def _find_match(self, credit: dict, deals: list[dict]) -> dict | None:
        """金額完全一致（誤差 AMOUNT_TOLERANCE 円以内）で最初に見つかった deal を返す"""
        credit_amount = abs(float(credit.get("amount", 0)))
        credit_date = self._parse_date(credit.get("date"))

        for deal in deals:
            # 金額チェック
            if abs(deal["due_amount"] - credit_amount) > AMOUNT_TOLERANCE:
                continue

            # 日付チェック（入金日 >= 請求日 かつ 許容幅内）
            deal_date = self._parse_date(deal.get("issue_date"))
            if credit_date and deal_date:
                delta = (credit_date - deal_date).days
                if delta < 0 or delta > DATE_TOLERANCE_DAYS:
                    continue

            return deal

        return None

    # ── 消し込み実行 ─────────────────────────────────────────

    def _settle(self, token: str, deal: dict, credit: dict) -> dict:
        """
        消し込みを記録する。
        現プランでは payments API 不可のため deal description を更新。
        将来 payments API が使える場合は _settle_via_api() に切り替え。
        """
        deal_id = deal["id"]
        credit_date = credit.get("date", datetime.today().strftime("%Y-%m-%d"))
        credit_desc = credit.get("description", "")
        amount = int(abs(float(credit.get("amount", deal["amount"]))))

        # deal の description に消し込み情報を追記
        new_desc = self._build_settlement_note(deal, credit_date, credit_desc)
        updated = self._update_deal_description(token, deal_id, new_desc)

        status = "settled_note" if updated else "match_only"
        logger.info(
            f"FreeeSettler: deal {deal_id} ({amount}円) matched with credit "
            f"{credit_date} {credit_desc!r} → {status}"
        )
        return {
            "deal_id": deal_id,
            "amount": amount,
            "credit_date": credit_date,
            "status": status,
        }

    def _settle_via_api(self, token: str, deal_id: int,
                        amount: int, date: str, wallet_account_id: int) -> bool:
        """
        プランアップグレード後に使用する本格消し込み。
        POST /api/1/deals/{id}/payments で due_amount を 0 にする。
        """
        resp = requests.post(
            f"{FREEE_API_BASE}/deals/{deal_id}/payments",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "company_id": self.company_id,
                "amount": amount,
                "date": date,
                "from_wallet_account_id": wallet_account_id,
            },
        )
        if resp.ok:
            return True
        logger.warning(f"FreeeSettler: payments API {resp.status_code}: {resp.text[:200]}")
        return False

    # ── freee API ────────────────────────────────────────────

    def _get_unsettled_deals(self, token: str, deal_type: str) -> list[dict]:
        resp = requests.get(
            f"{FREEE_API_BASE}/deals",
            params={
                "company_id": self.company_id,
                "type": deal_type,
                "status": "unsettled",
                "limit": 100,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if not resp.ok:
            logger.error(f"FreeeSettler: get deals failed {resp.status_code}")
            return []
        return resp.json().get("deals", [])

    def _update_deal_description(self, token: str, deal_id: int, description: str) -> bool:
        """deal の ref_number フィールドに消し込みメモを記録する"""
        resp = requests.put(
            f"{FREEE_API_BASE}/deals/{deal_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "company_id": self.company_id,
                "ref_number": description[:255],
            },
        )
        if resp.ok:
            return True
        logger.warning(
            f"FreeeSettler: update deal {deal_id} failed "
            f"{resp.status_code}: {resp.text[:200]}"
        )
        return False

    # ── ユーティリティ ────────────────────────────────────────

    def _build_settlement_note(self, deal: dict, credit_date: str, credit_desc: str) -> str:
        return f"消込:{credit_date}"

    def _parse_date(self, date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            return None
