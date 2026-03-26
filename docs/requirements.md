# DriveDesk 要件定義書

**バージョン**: 0.5.0
**作成日**: 2026-03-26
**ステータス**: ドラフト

---

## 1. システム概要

### 1.1 プロダクト名
**DriveDesk**

### 1.2 コンセプト
Google Driveの指定フォルダを監視し、流入するあらゆるファイルをAIが理解・分類し、会計ソフトや各種管理ツールへ自動反映するパイプライン。

Driveは「万能インボックス」として機能する。LINE・メール・手動アップロードなど、あらゆるチャネルからの情報をDriveに集約し、DriveDesk がそれ以降の処理を担う。

```
[LINE / メール / 手動 / 他ツール]
           ↓
    【Google Drive】← 万能インボックス
           ↓
      【DriveDesk】← このシステムの責務範囲
           ↓
  [freee / スプレッドシート / Slack / 他]
```

### 1.3 OSS方針
DriveDesk は特定クライアントに依存しない汎用システムとして設計する。クライアントごとの設定は設定ファイルで管理し、プラグイン型アーキテクチャにより会計ソフト・通知手段を差し替え可能とする。

**最初のユースケース**: 合同会社Kara's（薬局経営）

---

## 2. ステークホルダー

| 役割 | 説明 |
|------|------|
| システム管理者 | DriveDesKのセットアップ・運用者（例: 経理担当コンサル） |
| 情報提供者 | Driveにファイルを共有する側（例: 顧客の代表者） |
| 情報受領者 | 処理結果を利用する側（例: 経理担当者） |

---

## 3. 機能要件

### 3.1 監視（Watcher）
- [ ] 指定したGoogle Driveフォルダ全体をリアルタイム監視
- [ ] フォルダ直下およびすべてのサブフォルダを対象とする
- [ ] 新規ファイルの追加を検知する
- [ ] 既存ファイルの更新・上書きを検知する
- [ ] 処理済みファイルを再処理しない（重複防止）

### 3.2 分類（Classifier）
- [ ] AIによるファイル種別の自動判定
- [ ] 判定カテゴリ（初期）:
  - 経理: レシート、請求書、領収書、銀行明細、納品書
  - 労務: 勤怠記録、給与関連、雇用契約書
  - 法務: 契約書、定款、登記書類
  - その他: 上記に該当しないファイル
- [ ] 信頼度スコアを付与し、低信頼度は「要確認」扱いにする
- [ ] 誤配置ファイル（サブフォルダに間違えて入ったもの）も対象とする

### 3.3 抽出（Extractor）
- [ ] 画像ファイル（レシート写真等）のOCR処理
- [ ] PDFのテキスト抽出
- [ ] AIによる構造化データ抽出（日付・金額・取引先・勘定科目候補など）

### 3.4 処理（Processor）
- [ ] freee APIへの自動登録（経理系ファイル）
- [ ] **登録モードはクライアント設定で制御**
  - `finalize`（デフォルト）: 確定登録。信頼度に関わらず自動で処理完了
  - `draft`: freeeの下書きとして登録。クライアントが確認フローを希望する場合に使用
- [ ] 1ファイルに複数取引が含まれる場合（カード明細等）は取引ごとに個別登録する
- [ ] 登録先の参照IDを全件 `processor_refs`（JSON配列）に保存する
- [ ] 完全に処理不能なファイル（読み取り不可・分類不能）はログに記録しバッチ通知する
- [ ] プラグイン方式により他の会計ソフト（マネーフォワード等）にも対応可能な設計

**処理フロー**

| 状況 | 動作 |
|------|------|
| 正常に分類・抽出できた | 設定に従いfreeeへ登録（finalize or draft） |
| 分類・抽出はできたが信頼度低 | 同上 + ログに低信頼フラグを記録 |
| 完全に処理不能 | ログに記録・バッチ通知（頻度は設定可能） |

> **設計方針**: DriveDesk 内に承認フローを持たない。信頼度に関わらず自動処理し、経理担当者の確認は freee 上の通常業務の中で完結させる。

### 3.5 メタデータ管理（Metadata Store）
DriveDesKはすべてのファイルについて以下の情報をAIが常に参照できる形で管理する。

**管理項目**

| フィールド | 説明 | 例 |
|-----------|------|----|
| `file_id` | Google Drive固有ID（不変・追跡の基準） | `1aBcDeFg...` |
| `file_name` | ファイル名 | `receipt_20260326.jpg` |
| `shared_at` | Driveに共有された日時 | `2026-03-26 14:30` |
| `primary_date` | そのドキュメントで最も重要な日付（ソート・フィルタ用） | `2026-03-20` |
| `dates` | 全日付をキーバリューで格納（JSON） | 下記参照 |
| `category` | 分類結果 | `accounting/receipt` |
| `confidence` | 分類信頼度スコア | `0.95` |
| `status` | 処理ステータス | `pending` / `processed` / `failed` / `unprocessable` |
| `low_confidence` | 低信頼度フラグ | `true` / `false` |
| `processor` | 登録先ツール | `freee` |
| `processor_refs` | 登録先での参照ID（複数対応・JSON配列） | `["freee_001","freee_002"]` |
| `updated_at` | 最終更新日時 | - |

**日付設計の方針**

ドキュメント種別によって意味のある日付が異なるため、`primary_date`（代表日付1つ）と `dates`（全日付のJSON）の二段構えで管理する。

```json
// 請求書の例
{ "issue_date": "2026-03-01", "due_date": "2026-03-31" }

// 給与明細の例
{ "period_start": "2026-03-01", "period_end": "2026-03-31", "payment_date": "2026-04-25" }

// クレジットカード明細の例
{ "transaction_date": "2026-03-15", "closing_date": "2026-03-25", "payment_date": "2026-04-27" }
```

**ドキュメント種別ごとの日付定義**

*経理*

| ドキュメント | `primary_date` | `dates` に含まれる日付 |
|-------------|---------------|----------------------|
| レシート | `purchase_date` | purchase_date |
| 領収書 | `payment_date` | payment_date |
| 請求書 | `due_date` | issue_date, due_date |
| 納品書 | `delivery_date` | delivery_date |
| 見積書 | `issue_date` | issue_date, valid_until |
| 銀行明細 | `period_start` | transaction_date, period_start, period_end |
| カード明細 | `payment_date` | transaction_date, closing_date, payment_date, period_start, period_end |
| 契約書（経理） | `effective_date` | contract_date, effective_date, expiry_date |

*労務*

| ドキュメント | `primary_date` | `dates` に含まれる日付 |
|-------------|---------------|----------------------|
| 勤怠記録 | `period_start` | period_start, period_end |
| 給与明細 | `payment_date` | period_start, period_end, payment_date |
| 雇用契約書 | `start_date` | contract_date, start_date, end_date |
| 退職関連書類 | `retirement_date` | submission_date, last_working_date, retirement_date |
| 社保・年金書類 | `effective_date` | application_date, effective_date |
| 健康診断書 | `examination_date` | examination_date, issue_date |

*法務*

| ドキュメント | `primary_date` | `dates` に含まれる日付 |
|-------------|---------------|----------------------|
| 登記書類 | `registration_date` | registration_date, issue_date |
| 各種契約書 | `effective_date` | contract_date, effective_date, expiry_date |

**ストレージ構成（二層）**

- [ ] **SQLite（主）**: AIが高速参照するメタデータストア。`file_id` をキーに全情報を管理
- [ ] **Google Driveカスタムプロパティ（副）**: 処理ステータス・登録先IDをファイル自体に付与。ファイルが移動・リネームされても追跡が切れない
- [ ] 両レイヤーは処理完了時に同期する

### 3.6 記録（Logger）
- [ ] 全処理をGoogleスプレッドシートに記録（人間用監査ログ）
  - 記録項目: ファイル名、shared_at、document_date、分類結果、処理結果、登録先ID
- [ ] スプレッドシートはメタデータのソースではなく人間が読む監査ログとして位置づける

### 3.7 通知（Notifier）
- [ ] 処理不能ファイルが蓄積した場合にバッチ通知（頻度はクライアント設定で制御、デフォルト: 日次）
  - 設定値: `hourly` / `daily`（デフォルト）/ `weekly` / `off`
- [ ] 処理失敗（システムエラー）時に通知
- [ ] 通知先はプラグイン方式（Telegram / メール / Slack / スプレッドシート記録など）
- [ ] **Kara'sユースケース初期**: スプレッドシート記録（通知手段は未定）

### 3.8 整理（Organizer）
- [ ] 処理済みファイルを適切なサブフォルダへ自動移動（オプション）
- [ ] 移動ルールは設定ファイルで定義可能

---

## 4. 非機能要件

| 項目 | 要件 |
|------|------|
| 汎用性 | 設定ファイルで任意クライアント・任意フォルダに対応 |
| 拡張性 | プラグイン型アーキテクチャ（Processor / Notifier / Logger を差し替え可能） |
| 信頼性 | 処理失敗時のリトライ・エラーログ |
| セキュリティ | クレデンシャルは環境変数または .env 管理（リポジトリに含めない） |
| 可観測性 | 全処理をSQLite・スプレッドシートで追跡可能。AIは常に全ファイルの状態を把握できる |
| 追跡継続性 | ファイルの移動・リネーム後もfile_idベースで追跡が切れない |
| OSS | MITライセンス、ドキュメント英語・日本語併記 |

---

## 5. ユースケース詳細: 合同会社Kara's

### 5.1 前提
- 監視フォルダ: `合同会社Kara's(共有用)/`（Google Drive）
- 情報提供者: Kara's代表者
- 情報受領者: 経理担当コンサル（翔太さん）
- 会計ソフト: freee（導入予定・承認済み）
- 通知手段: Googleスプレッドシート（候補）

### 5.2 典型的なフロー
1. 代表者がレシート写真・請求書PDFをDriveにアップロード
2. DriveDesKが検知 → SQLiteに `shared_at` とともにレコード作成
3. AIで「レシート（経理）」と分類 → 信頼度スコアを記録
4. OCRで金額・日付・店舗名を抽出 → `document_date` を確定
5. freee APIへ自動登録（信頼度高の場合）
6. SQLite・Driveプロパティを `processed` に更新、freee取引IDを `processor_ref` に保存
7. スプレッドシートに監査ログを追記

### 5.3 要確認ケース
- 画像が不鮮明でOCR精度が低い
- 分類が判断できない（信頼度スコアが閾値以下）
- freee登録時にエラーが発生

---

## 6. 制約・前提条件

- Google Drive APIへのアクセス権が必要
- freee APIのアクセストークンが必要（Kara'sユースケース）
- 処理はサーバー（Mac mini等）で常駐実行を想定
- Drive前段のチャネル（LINE等）はDriveDesk の責務範囲外（将来の拡張）

---

## 7. 将来の拡張（スコープ外・検討事項）

- LINE Bot → Drive自動転送連携
- メール添付 → Drive自動転送連携
- マネーフォワード・弥生会計への対応
- Webダッシュボード（処理状況の可視化）
- 複数クライアントの並行管理
