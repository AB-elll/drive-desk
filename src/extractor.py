import base64
import json
import os
import time

SYSTEM_PROMPT = """
あなたは会計・労務ドキュメントのデータ抽出専門家です。
提供されたドキュメントから構造化データを抽出してください。

必ず以下のJSON形式で返答してください：
{
  "primary_date": "YYYY-MM-DD または null",
  "dates": {
    "<date_key>": "YYYY-MM-DD"
  },
  "amount": {
    "total": <数値または null>,
    "subtotal": <税抜金額または null>,
    "tax": <消費税額または null>
  },
  "counterpart": "<取引先名または null>",
  "description": "<摘要または null>",
  "account_candidate": "<勘定科目候補または null>",
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

datesキーの例:
- レシート: purchase_date
- 請求書: issue_date, due_date
- 給与明細: period_start, period_end, payment_date
- カード明細: transaction_date, closing_date, payment_date

transactionsは複数取引が含まれる場合（カード明細・銀行明細等）に使用。
単一取引の場合は空配列 []。
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
                model="gemini-2.0-flash",
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    prompt,
                ],
            )
        else:
            from PIL import Image
            img = Image.open(file_path)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[prompt, img],
            )
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw = response.text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    result = json.loads(raw)
    return result, {"duration_ms": duration_ms, "raw_response": raw, "model": "gemini-2.0-flash"}


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
