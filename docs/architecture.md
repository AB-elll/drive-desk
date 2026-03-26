# DriveDesk アーキテクチャ設計書

**バージョン**: 0.2.0
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
│  └──────────┘  └────────────┘  └──────────┬───────────┘ │
│       ↕                                    ↓             │
│  ┌──────────────┐        ┌─────────────────────────┐    │
│  │Metadata Store│←───────│       Processor         │    │
│  │   (SQLite)   │        │ [FreeePlugin][JDLPlugin] │    │
│  └──────────────┘        └────────────┬────────────┘    │
│                                        ↓                 │
│              ┌─────────────┐  ┌────────────────────┐    │
│              │   Logger    │  │      Notifier      │    │
│              │(Spreadsheet)│  │ [Spreadsheet/TG..] │    │
│              └─────────────┘  └────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 2. コンポーネント詳細

### 2.1 Watcher（監視）
**責務**: Google Drive APIを通じてファイルの追加・更新を検知する

- **方式**: Google Drive API の `changes.list` + `startPageToken` によるポーリング
- **ポーリング間隔**: 設定可能（デフォルト: 60秒）
- **重複防止**: SQLiteの `file_id` で処理済みを判定し再処理を防ぐ
- **状態管理**: `pageToken` をSQLiteに保存し、前回チェック以降の差分のみ取得

### 2.2 Classifier（分類）
**責務**: ファイルの種別をAIで判定する

- **入力**: ファイルメタデータ（名前・MIMEタイプ・フォルダパス）+ ファイル内容プレビュー
- **出力**: カテゴリ + サブカテゴリ + 信頼度スコア（0.0〜1.0）
- **AI**: Claude API（claude-sonnet-4-6）
- **低信頼度の扱い**: `low_confidence: true` フラグを立てて処理は継続する（承認フローなし）
- **分類不能の扱い**: `unprocessable` としてログに記録しバッチ通知キューへ

```json
// 分類結果例
{
  "category": "accounting",
  "subcategory": "receipt",
  "confidence": 0.95,
  "low_confidence": false
}
```

### 2.3 Extractor（抽出）
**責務**: ファイルから構造化データを抽出する

- **画像ファイル**: Claude Vision APIでOCR + 構造化抽出
- **PDF**: テキスト抽出 → Claude APIで構造化
- **抽出項目（経理系）**: 日付群・金額（税抜/税込）・取引先・摘要・勘定科目候補
- **抽出項目（労務系）**: 対象者・期間・種別
- **出力**: 構造化JSONデータ（`primary_date` + `dates` を含む）

### 2.4 Metadata Store（状態管理）
**責務**: 全ファイルのメタデータをAIが常に参照できる形で管理する

**二層構造**

| レイヤー | 技術 | 役割 |
|---------|------|------|
| 主 | SQLite | AIが高速参照するメタデータストア |
| 副 | Drive Custom Properties | ファイル自体に処理状態を付与（移動・リネーム後も追跡継続） |

**SQLiteスキーマ**

```sql
CREATE TABLE files (
  file_id        TEXT PRIMARY KEY,
  file_name      TEXT NOT NULL,
  shared_at      DATETIME NOT NULL,
  primary_date   DATE,
  dates          JSON,
  category       TEXT,
  subcategory    TEXT,
  confidence     REAL,
  low_confidence INTEGER DEFAULT 0,
  status         TEXT DEFAULT 'pending',
  -- status: pending / processed / failed / unprocessable
  processor_refs JSON,
  -- {"freee": ["txn_001", "txn_002"], "jdl_csv": "export_202604.csv"}
  error_message  TEXT,
  updated_at     DATETIME
);

CREATE TABLE watcher_state (
  key   TEXT PRIMARY KEY,
  value TEXT
  -- key='page_token': Drive APIのpageToken保存
);

CREATE TABLE notifier_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id    TEXT,
  type       TEXT,  -- 'unprocessable' / 'system_error'
  created_at DATETIME,
  notified   INTEGER DEFAULT 0
);
```

### 2.5 Processor（処理）
**責務**: 抽出データを設定された全プロセッサーへ並列で処理する

プラグイン型インターフェースを持つ。各プラグインは共通インターフェースを実装する。

```python
class ProcessorPlugin:
    type: str  # "api" or "csv"
    def process(self, extracted_data: dict) -> ProcessResult: ...
    def validate(self, extracted_data: dict) -> ValidationResult: ...
```

**プロセッサー一覧**

| タイプ | クラス | 方式 | タイミング |
|--------|--------|------|-----------|
| `freee` | `FreeePlugin` | REST API | リアルタイム |
| `jdl_csv` | `JDLCsvPlugin` | CSV生成→Drive出力 | スケジュール（cron） |
| （将来）`mf` | `MFPlugin` | REST API | リアルタイム |

**並列実行**: 1ファイルに対して設定された全プロセッサーが同時に走る。一方が失敗しても他方の処理は継続する。

**JDL CSVスケジューラー**: cronで月次実行。その月の全 `processed` レコードをJDL形式CSVに集約し、指定DriveフォルダへアップロードしてSQLiteの `processor_refs.jdl_csv` を更新する。

> ⚠️ **TODO**: JDL IBEXのCSVインポートフォーマット仕様を調査する

### 2.6 Logger（記録）
**責務**: 全処理を人間が読めるスプレッドシートに記録する

スプレッドシートはメタデータのソースではなく監査ログとして位置づける（正本はSQLite）。

| 列 | 内容 |
|----|------|
| timestamp | 処理日時 |
| file_name | ファイル名 |
| file_id | Google Drive ファイルID |
| shared_at | Drive共有日時 |
| primary_date | ドキュメント代表日付 |
| category | 分類結果 |
| confidence | 信頼度スコア |
| low_confidence | 低信頼度フラグ |
| result | 処理結果（processed / failed / unprocessable） |
| processor_refs | 登録先ID（JSON） |
| error | エラー内容（失敗時） |

### 2.7 Notifier（通知）
**責務**: 処理不能ファイルの蓄積・システムエラー発生時に通知する

プラグイン型。初期はスプレッドシートへの記録のみ。将来的にTelegram・Slack等を追加。

- **unprocessableバッチ通知**: `notifier_queue` を定期スキャンしてまとめて通知（デフォルト: daily）
- **システムエラー通知**: 処理失敗時は即時通知

---

## 3. データフロー

```
[新規ファイル検知 (Watcher)]
      ↓
[メタデータ取得] → ファイル名・MIMEタイプ・フォルダパス
      ↓
[SQLite: pendingレコード作成] → shared_at を記録
      ↓
[ファイルダウンロード] → 一時ファイルとしてローカルに保存
      ↓
[Classifier] → カテゴリ・信頼度を決定
      ↓
  分類・処理が可能？
  ├─ NO  → [SQLite: unprocessable更新]
  │         → [notifier_queue] バッチ通知キューに追加
  │         → [Logger] スプレッドシートに記録
  │         → 終了
  └─ YES → [Extractor] → 構造化データ抽出（primary_date・dates確定）
                ↓
         [Processor: 全プロセッサーを並列実行]
           ├─ FreeePlugin → freee APIへ登録（finalize or draft）
           │               ※ 1ファイル→複数取引は全件登録
           └─ JDLCsvPlugin → 月次バッファに追加（cron実行時にCSV生成）
                ↓
         全プロセッサー完了？
         ├─ YES → [SQLite: processed更新・processor_refs保存]
         │         → [Drive Custom Properties: 同期]
         │         → [Logger] スプレッドシートに記録
         └─ NO（一部失敗）→ [SQLite: failed更新]
                            → [Logger] 記録
                            → [Notifier] 即時通知
                            → リトライキューに追加
```

---

## 4. 設定ファイル構造

```yaml
# drivedesk.config.yml（クライアント別）
client:
  name: "合同会社Kara's"
  id: "karas"

drive:
  folder_id: "${KARAS_DRIVE_FOLDER_ID}"
  watch_interval_seconds: 60
  include_subfolders: true

classifier:
  model: "claude-sonnet-4-6"
  confidence_threshold: 0.8   # これ以下でlow_confidenceフラグ

processors:
  - type: freee
    registration_mode: finalize   # finalize or draft
    company_id: "${FREEE_COMPANY_ID}"
    access_token: "${FREEE_ACCESS_TOKEN}"
  - type: jdl_csv
    output_folder_id: "${JDL_OUTPUT_FOLDER_ID}"
    schedule: monthly             # daily / weekly / monthly

logger:
  type: google_spreadsheet
  spreadsheet_id: "${LOG_SPREADSHEET_ID}"
  sheet_name: "DriveDesk Log"

notifier:
  unprocessable_batch: daily      # hourly / daily / weekly / off
  type: spreadsheet               # 初期: スプレッドシート記録のみ
  # type: telegram
  # token: "${TELEGRAM_BOT_TOKEN}"
  # chat_id: "${TELEGRAM_CHAT_ID}"
```

---

## 5. 技術スタック

| レイヤー | 技術 | 理由 |
|---------|------|------|
| ランタイム | **Python 3.11+** | 既存インフラ（OCF）と統一、AI/API SDKが充実 |
| AI | Claude API (claude-sonnet-4-6) | Vision対応・構造化出力が得意 |
| Drive連携 | Google Drive API v3 (`google-api-python-client`) | 公式SDK |
| 会計連携 | freee API v2 (`requests`) | REST API |
| ログ | Google Sheets API v4 | スプレッドシート記録 |
| 状態管理 | SQLite (`sqlite3` 標準ライブラリ) | 軽量・外部依存ゼロ |
| スケジューラー | cron（Mac mini） | JDL月次CSV・通知バッチ用 |
| 実行環境 | Mac mini（常駐プロセス / launchd） | 既存インフラ活用 |

---

## 6. ディレクトリ構造

```
drive-desk/
├── README.md
├── docs/
│   ├── requirements.md
│   └── architecture.md
├── src/
│   ├── main.py                   # エントリーポイント（常駐プロセス）
│   ├── watcher.py                # Drive監視
│   ├── classifier.py             # AI分類
│   ├── extractor.py              # データ抽出
│   ├── metadata_store.py         # SQLite管理
│   ├── processor/
│   │   ├── base.py               # ProcessorPlugin基底クラス
│   │   ├── freee.py              # freee APIプラグイン
│   │   └── jdl_csv.py            # JDL CSVプラグイン
│   ├── logger.py                 # スプレッドシート記録
│   ├── notifier/
│   │   ├── base.py               # Notifier基底クラス
│   │   └── spreadsheet.py        # スプレッドシート通知
│   └── scheduler.py              # JDL月次CSV・バッチ通知のcronエントリ
├── clients/
│   └── karas/
│       ├── drivedesk.config.yml  # Kara's固有設定
│       └── .env                  # クレデンシャル（gitignore）
├── config/
│   └── example.config.yml        # 設定テンプレート
├── requirements.txt
└── .gitignore
```

---

## 7. 設計上の重要な判断

### 7.1 Drive前段は責務外
LINE等からDriveへのファイル転送はDriveDeskの責務外とする。Drive以降のパイプラインに集中することで、システムをシンプルに保つ。

### 7.2 プラグイン型アーキテクチャ
会計ソフト・通知手段の追加が容易にできるよう、Processor・Notifier・Loggerをすべてプラグイン型とする。

### 7.3 承認フローを持たない
信頼度に関わらず自動処理することが原則。経理担当者の確認は各会計ソフト上の通常業務で完結させる。DriveDesk内に承認フローを作ると運用コストが増大し、クライアント数が増えた際にスケールしない。

### 7.4 SQLiteをメタデータの正本とする
スプレッドシートは人間が読む監査ログ。Drive Custom Propertiesはファイル追跡の保険。AIが参照する正本は常にSQLite。

### 7.5 Pythonを採用
既存インフラ（OCF・factcheck-engine）との統一性、および Google/Claude APIのSDK充実度からPythonを採用する。
