import base64
import json
import anthropic

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


def extract(file_path: str, mime_type: str, subcategory: str,
            primary_date_key: str | None, model: str = "claude-sonnet-4-6") -> dict:
    client = anthropic.Anthropic()

    # ファイルをbase64エンコード
    with open(file_path, "rb") as f:
        file_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # 画像・PDFはVision APIで処理
    if mime_type.startswith("image/"):
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": file_data,
                },
            },
            {
                "type": "text",
                "text": f"このドキュメント（種別: {subcategory}）からデータを抽出してください。",
            },
        ]
    elif mime_type == "application/pdf":
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": file_data,
                },
            },
            {
                "type": "text",
                "text": f"このドキュメント（種別: {subcategory}）からデータを抽出してください。",
            },
        ]
    else:
        # テキスト系はデコードしてテキストとして渡す
        text_content = base64.b64decode(file_data).decode("utf-8", errors="replace")
        content = [{"type": "text", "text": f"種別: {subcategory}\n\n{text_content}"}]

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    result = json.loads(raw)

    # primary_dateが未設定の場合、primary_date_keyから取得
    if not result.get("primary_date") and primary_date_key:
        result["primary_date"] = result.get("dates", {}).get(primary_date_key)

    return result
