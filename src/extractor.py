import base64
import json
import os
import time

SYSTEM_PROMPT = """
あなたは会計・労務ドキュメントのデータ抽出専門家です。
提供されたドキュメントから構造化データを抽出してください。

必ず以下のJSON形式で返答してください：
{
  "deal_type": "expense" | "income",
  "primary_date": "YYYY-MM-DD または null",
  "dates": {
    "<date_key>": "YYYY-MM-DD"
  },
  "amount": {
    "total": <税込合計または null>,
    "subtotal": <税抜小計または null>,
    "tax": <消費税額または null>
  },
  "counterpart": "<取引先・発行機関名または null>",
  "description": "<摘要または null>",
  "account_candidate": "<勘定科目候補または null>",
  "line_items": [
    {
      "description": "<品目名>",
      "amount": <税抜金額（数値）>,
      "account_candidate": "<勘定科目候補または null>"
    }
  ],
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "amount": <数値>,
      "description": "<摘要>",
      "account_candidate": "<勘定科目候補>"
    }
  ],
  "raw_text": "<OCRで読み取った生テキスト>"
}

【deal_type の判定基準】
- "income" : 自社に入金される書類
    例）自社発行の請求書、行政・保険機関からの支払決定通知書・入金通知、売上明細
- "expense": 自社が支払う書類
    例）仕入先・サービス業者からの請求書、レシート、領収書

【line_items と transactions の使い分け】
- line_items: 請求書・レシートの品目行（同一日付・同一取引の複数品目）。品目が1つでも必ず配列で返す。
- transactions: カード明細・銀行明細など、日付が異なる複数取引。該当しない場合は空配列 []。

datesキーの例:
- レシート: purchase_date
- 請求書: issue_date, due_date
- 支払決定通知: target_period（対象月）, payment_date（支払予定日）
- 給与明細: period_start, period_end, payment_date
- カード明細: transaction_date, closing_date, payment_date
"""

MANAGEMENT_SYSTEM_PROMPT = """
あなたは許認可証・資格証明書のデータ抽出専門家です。
提供されたドキュメントから構造化データを抽出してください。

必ず以下のJSON形式で返答してください：
{
  "primary_date": "YYYY-MM-DD または null（有効期限があればその日付）",
  "dates": {
    "issued_date": "YYYY-MM-DD または null（交付日・取得日）",
    "expiry_date": "YYYY-MM-DD または null（有効期限・満了日）",
    "renewal_deadline": "YYYY-MM-DD または null（更新手続き期限）"
  },
  "license_name": "<許認可名・資格名・免許名 または null>",
  "license_number": "<番号・登録番号 または null>",
  "person_name": "<氏名（個人の資格の場合） または null>",
  "authority": "<交付機関・管轄機関 または null>",
  "description": "<その他特記事項 または null>",
  "raw_text": "<OCRで読み取った生テキスト>"
}

読み取れない項目はnullとする。日付は必ずYYYY-MM-DD形式で返すこと。
"""


MANAGEMENT_SUBCATEGORIES = {"license", "permit", "qualification"}


def _extract_with_gemini(file_path: str, mime_type: str) -> tuple[dict, dict]:
    """Gemini 2.0 Flash で管理書類（免許証・資格証等）を抽出する"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = MANAGEMENT_SYSTEM_PROMPT + "\nこのドキュメントからデータを抽出してください。"

    t0 = time.monotonic()
    try:
        if mime_type == "application/pdf":
            import pathlib
            pdf_bytes = pathlib.Path(file_path).read_bytes()
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    prompt,
                ],
            )
        else:
            from PIL import Image
            img = Image.open(file_path)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, img],
            )
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw = response.text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    result = json.loads(raw)
    return result, {"duration_ms": duration_ms, "raw_response": raw, "model": "gemini-2.5-flash"}


def extract(file_path: str, mime_type: str, subcategory: str,
            primary_date_key: str | None, model: str = "claude-sonnet-4-6") -> tuple[dict, dict]:
    """戻り値: (抽出結果, メタ{"duration_ms", "raw_response", ...})"""

    is_management = subcategory in MANAGEMENT_SUBCATEGORIES

    # 管理書類 + GEMINI_API_KEY があれば Gemini で抽出
    if is_management and os.environ.get("GEMINI_API_KEY"):
        result, meta = _extract_with_gemini(file_path, mime_type)
        if not result.get("primary_date") and primary_date_key:
            result["primary_date"] = result.get("dates", {}).get(primary_date_key)
        return result, meta

    from claude_cli import call_claude, call_claude_with_file

    system_prompt = MANAGEMENT_SYSTEM_PROMPT if is_management else SYSTEM_PROMPT
    user_text = f"このドキュメント（種別: {subcategory}）からデータを抽出してください。"

    if mime_type.startswith("image/") or mime_type == "application/pdf":
        raw, meta = call_claude_with_file(system_prompt, user_text, file_path, mime_type, model)
    else:
        with open(file_path, "r", errors="replace") as f:
            text_content = f.read()
        raw, meta = call_claude(system_prompt, f"種別: {subcategory}\n\n{text_content}", model)

    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    raw = raw.strip()
    if not raw:
        empty = {"primary_date": None, "dates": {}, "amount": {"total": None},
                 "transactions": [], "description": None, "raw_text": None}
        return empty, meta

    result = json.loads(raw)

    if not result.get("primary_date") and primary_date_key:
        result["primary_date"] = result.get("dates", {}).get(primary_date_key)

    return result, meta
