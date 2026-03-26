# DriveDesk アーキテクチャ設計書

**バージョン**: 0.1.0
**作成日**: 2026-03-26
**ステータス**: ドラフト

---

## 1. 全体構成

```
┌─────────────────────────────────────────────────────────┐
│                    Input Channels                        │
│         (LINE / Email / Manual Upload / etc.)            │
└─────────────────────────┬───────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   Google Drive                           │
│              (万能インボックス)                           │
│         指定フォルダ + 全サブフォルダ                      │
└─────────────────────────┬───────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                     DriveDesk                            │
│                                                          │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────────┐ │
│  │ Watcher  │→ │ Classifier │→ │      Extractor       │ │
│  │          │  │  (AI分類)  │  │  (OCR + AI抽出)      │ │
│  └──────────┘  └────────────┘  └──────────┬───────────┘ │
│                                            ↓             │
│  ┌──────────────────────────────────────────────────┐   │
│  │                   Processor                       │   │
│  │  [freee Plugin] [MF Plugin] [Custom Plugin...]   │   │
│  └──────────────────────────┬─────────────────────┘    │
│                              ↓                           │
│  ┌─────────────┐  ┌─────────────────────────────────┐   │
│  │   Logger    │  │          Notifier               │   │
│  │(Spreadsheet)│  │  [Telegram] [Email] [Slack...]  │   │
│  └─────────────┘  └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 2. コンポーネント詳細

### 2.1 Watcher（監視）
**責務**: Google Drive APIを通じてファイルの追加・更新を検知する

- **方式**: Google Drive API の Changes.list + startPageToken によるポーリング
  - Webhook（Push通知）も検討するが、初期はポーリングで実装
- **ポーリング間隔**: 設定可能（デフォルト: 60秒）
- **重複防止**: 処理済みファイルIDをDBに記録し、再処理を防ぐ
- **状態管理**: `pageToken` をローカルに保存し、前回チェック以降の差分のみ取得

### 2.2 Classifier（分類）
**責務**: ファイルの種別をAIで判定する

- **入力**: ファイルメタデータ（名前・MIMEタイプ・フォルダパス）+ ファイル内容のサマリー
- **出力**: カテゴリ + サブカテゴリ + 信頼度スコア（0.0〜1.0）
- **AI**: Claude API（claude-sonnet-4-6）
- **信頼度閾値**: 設定可能（デフォルト: 0.8）。以下は `requires_review` へ

```yaml
# 分類結果例
category: accounting
subcategory: receipt
confidence: 0.95
requires_review: false
```

### 2.3 Extractor（抽出）
**責務**: ファイルから構造化データを抽出する

- **画像ファイル**: Claude Vision APIでOCR + 構造化抽出
- **PDF**: テキスト抽出 → Claude APIで構造化
- **抽出項目（経理系）**: 日付・金額（税抜/税込）・取引先・摘要・勘定科目候補
- **抽出項目（労務系）**: 対象者・期間・種別
- **出力**: 構造化JSONデータ

### 2.4 Processor（処理）
**責務**: 抽出データを各ツールに登録する

プラグイン型インターフェースを持つ。各プラグインは共通インターフェースを実装する。

```
interface ProcessorPlugin {
  process(extractedData: ExtractedData): ProcessResult
  validate(extractedData: ExtractedData): ValidationResult
}
```

**初期実装プラグイン**:
- `FreeePlugin`: freee API v2 を使って取引・経費を登録

### 2.5 Logger（記録）
**責務**: 全処理をスプレッドシートに記録する

| 列 | 内容 |
|----|------|
| timestamp | 処理日時 |
| file_name | ファイル名 |
| file_id | Google Drive ファイルID |
| category | 分類結果 |
| confidence | 信頼度スコア |
| extracted_data | 抽出データ（JSON） |
| processor | 使用したプロセッサ |
| result | 処理結果（processed / failed / unprocessable） |
| low_confidence | 低信頼度フラグ（true/false） |
| error | エラー内容（失敗時） |
| external_ids | 外部ツール側のID・複数対応（JSON配列） |

### 2.6 Notifier（通知）
**責務**: 処理不能ファイルの蓄積・システムエラー発生時に通知する

プラグイン型。初期はスプレッドシートへの記録のみ。将来的にTelegram・Slack等を追加。

- **unprocessableバッチ通知**: 設定した頻度でまとめて通知（`hourly` / `daily` / `weekly` / `off`、デフォルト: `daily`）
- **システムエラー通知**: 処理失敗時は即時通知

---

## 3. データフロー

```
[新規ファイル検知]
      ↓
[メタデータ取得] → ファイル名・サイズ・MIMEタイプ・フォルダパス
      ↓
[SQLiteにpendingレコード作成] → shared_at を記録
      ↓
[ファイルダウンロード] → 一時ファイルとしてローカルに保存
      ↓
[Classifier] → カテゴリ・信頼度を決定
      ↓
  分類・抽出が可能？
  ├─ NO  → [unprocessable] → SQLite更新 → Logger記録
  │         → バッチキューに追加（日次等でまとめてNotifier）
  └─ YES → [Extractor] → 構造化データ抽出（primary_date・dates含む）
                ↓
           [Processor] → 設定に従いfreeeへ登録（finalize or draft）
           ※ 1ファイル→複数取引の場合は全件登録
                ↓
           成功？
           ├─ YES → [SQLite] processed更新・processor_refs保存
           │         → [Drive Custom Properties] 同期
           │         → [Logger] スプレッドシートに記録
           └─ NO  → [SQLite] failed更新 → Logger記録 → 即時Notifier → リトライキュー
```

---

## 4. 設定ファイル構造

```yaml
# drivedesk.config.yml
client:
  name: "合同会社Kara's"
  id: "karas"

drive:
  folder_id: "GOOGLE_DRIVE_FOLDER_ID"
  watch_interval_seconds: 60
  include_subfolders: true

classifier:
  confidence_threshold: 0.8
  model: "claude-sonnet-4-6"

processors:
  - type: freee
    enabled: true
    config:
      company_id: "${FREEE_COMPANY_ID}"
      access_token: "${FREEE_ACCESS_TOKEN}"

logger:
  type: google_spreadsheet
  spreadsheet_id: "${LOG_SPREADSHEET_ID}"
  sheet_name: "DriveDesk Log"

processor:
  freee:
    registration_mode: finalize   # finalize（デフォルト）or draft

notifier:
  unprocessable_batch: daily      # hourly / daily（デフォルト）/ weekly / off
  type: spreadsheet               # 初期: スプレッドシート記録のみ
  # type: telegram
  # token: "${TELEGRAM_BOT_TOKEN}"
  # chat_id: "${TELEGRAM_CHAT_ID}"
```

---

## 5. 技術スタック候補

| レイヤー | 技術 | 理由 |
|---------|------|------|
| ランタイム | Node.js または Python | Drive API・freee APIのSDKが充実 |
| AI | Claude API (claude-sonnet-4-6) | Vision対応、構造化出力が得意 |
| Drive連携 | Google Drive API v3 | 公式SDK |
| 会計連携 | freee API v2 | 公式SDK |
| ログ | Google Sheets API v4 | スプレッドシート記録 |
| 状態管理 | SQLite（ローカル）| 軽量・依存少 |
| 実行環境 | Mac mini（常駐プロセス）| 既存インフラ活用 |

---

## 6. ディレクトリ構造（想定）

```
drive-desk/
├── README.md
├── docs/
│   ├── requirements.md
│   └── architecture.md
├── src/
│   ├── watcher/
│   ├── classifier/
│   ├── extractor/
│   ├── processor/
│   │   ├── base.js
│   │   └── plugins/
│   │       └── freee.js
│   ├── logger/
│   └── notifier/
├── config/
│   └── example.config.yml
├── clients/
│   └── karas/
│       └── drivedesk.config.yml  ← Kara's固有設定
└── package.json
```

---

## 7. 設計上の重要な判断

### 7.1 Drive前段は責務外
LINE等からDriveへのファイル転送はDriveDeskの責務外とする。Drive以降のパイプラインに集中することで、システムをシンプルに保つ。

### 7.2 プラグイン型アーキテクチャ
freee以外の会計ソフトへの対応や、Telegram通知の追加が容易にできるよう、Processor・Notifier・Loggerはすべてプラグイン型とする。

### 7.3 要確認キュー
AIの判断に自信がない場合は自動処理しない。人間のレビューを経てから処理する安全設計を採用する。
