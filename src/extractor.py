import base64
import json
import os

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
    """Gemini Flash を使った管理書類の抽出処理"""
    import time
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = """
あなたは日本の公的書類（免許証・許認可証・資格証明書）の読み取り専門家です。
画像に写っている文字を正確に読み取り、以下のJSON形式で返答してください。
読み取れない項目は必ず null にしてください。推測・補完は禁止です。

{
  "primary_date": "YYYY-MM-DD または null（有効期限があればその日付、なければ null）",
  "dates": {
    "issued_date": "YYYY-MM-DD または null",
    "expiry_date": "YYYY-MM-DD または null",
    "renewal_deadline": "YYYY-MM-DD または null"
  },
  "license_name": "書類に記載された正式名称 または null",
  "license_number": "番号・登録番号 または null",
  "person_name": "氏名（画像に明記されている場合のみ） または null",
  "authority": "交付機関・発行機関（画像に明記されている場合のみ） または null",
  "description": "その他読み取れた特記事項 または null",
  "raw_text": "画像から読み取れた全テキスト（改行は\\nで）"
}
"""

    t0 = time.monotonic()
    try:
        if mime_type == "application/pdf":
            uploaded = genai.upload_file(file_path, mime_type=mime_type)
            response = model.generate_content([prompt, uploaded])
            genai.delete_file(uploaded.name)
        else:
            from PIL import Image
            img = Image.open(file_path)
            response = model.generate_content([prompt, img])
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw = response.text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    result = json.loads(raw)
    return result, {"duration_ms": duration_ms, "raw_response": raw, "model": "gemini-1.5-flash"}


def extract(file_path: str, mime_type: str, subcategory: str,
            primary_date_key: str | None, model: str = "claude-sonnet-4-6") -> tuple[dict, dict]:
    """戻り値: (抽出結果, claudeメタ{"duration_ms", "raw_response", ...})"""
    from claude_cli import call_claude, call_claude_with_file

    is_management = subcategory in MANAGEMENT_SUBCATEGORIES

    # management カテゴリで GEMINI_API_KEY が設定されている場合は Gemini を使用
    if is_management and os.environ.get("GEMINI_API_KEY"):
        result, meta = _extract_with_gemini(file_path, mime_type)
    else:
        # 従来の Claude Vision 処理（フォールバック）
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

    # primary_dateが未設定の場合、primary_date_keyから取得
    if not result.get("primary_date") and primary_date_key:
        result["primary_date"] = result.get("dates", {}).get(primary_date_key)

    return result, meta
