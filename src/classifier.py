import json
import os

# ドキュメント種別ごとのprimary_dateキー
PRIMARY_DATE_RULES = {
    "receipt":          "purchase_date",
    "official_receipt": "payment_date",
    "invoice":          "due_date",
    "delivery_note":    "delivery_date",
    "quotation":        "issue_date",
    "bank_statement":   "period_start",
    "card_statement":   "payment_date",
    "contract":         "effective_date",
    "timesheet":        "period_start",
    "payslip":          "payment_date",
    "employment_contract": "start_date",
    "resignation":      "retirement_date",
    "social_insurance": "effective_date",
    "health_check":     "examination_date",
    "registration":     "registration_date",
    # management
    "license":          "expiry_date",
    "permit":           "expiry_date",
    "qualification":    "expiry_date",
}

SYSTEM_PROMPT = """
あなたはドキュメント分類の専門家です。
提供されたファイル情報をもとに、ドキュメントの種別を判定してください。

必ず以下のJSON形式で返答してください：
{
  "category": "accounting" | "labor" | "legal" | "management" | "other",
  "subcategory": "<下記のサブカテゴリ>",
  "confidence": 0.0〜1.0,
  "reason": "<判定理由を1文で>"
}

サブカテゴリ一覧:
- accounting: receipt, official_receipt, invoice, delivery_note, quotation, bank_statement, card_statement, contract
- labor: timesheet, payslip, employment_contract, resignation, social_insurance, health_check
- legal: registration, contract
- management: license, permit, qualification
- other: unknown

managementカテゴリの判定基準:
- license: 薬局開設許可証・保険薬局指定通知など、事業所に紐づく許認可証
- permit: 麻薬取扱者免許・毒物劇物取扱責任者など、特定業務の取扱許可
- qualification: 薬剤師免許・登録販売者資格など、従業員個人の資格・免許証
"""


def classify(file_name: str, mime_type: str, folder_path: str, config: dict,
             local_path: str = None) -> tuple[dict, dict]:
    """戻り値: (分類結果, claudeメタ{"duration_ms", "raw_response", ...})"""
    from claude_cli import call_claude, call_claude_with_file
    threshold = config.get("classifier", {}).get("confidence_threshold", 0.8)
    model = config.get("classifier", {}).get("model", "claude-sonnet-4-6")

    user_message = f"""
ファイル名: {file_name}
MIMEタイプ: {mime_type}
格納フォルダパス: {folder_path}

画像の場合は、画像の内容を確認して分類してください。
"""
    # 画像/PDFファイルがある場合はVisionで分類
    if local_path and mime_type and (mime_type.startswith("image/") or mime_type == "application/pdf"):
        raw, meta = call_claude_with_file(SYSTEM_PROMPT, user_message, local_path, mime_type, model=model)
    else:
        raw, meta = call_claude(SYSTEM_PROMPT, user_message, model=model, max_tokens=512)
    # JSONブロックを抽出
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    result = json.loads(raw)
    result["low_confidence"] = result["confidence"] < threshold
    result["primary_date_key"] = PRIMARY_DATE_RULES.get(result.get("subcategory", ""), None)
    return result, meta
