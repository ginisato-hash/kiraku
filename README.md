# 喜らく単体 会計・財務モデル自動更新システム

株式会社Yuge が運営する宿泊施設「**喜らく**」**単体**向けの、ローカル会計データ自動化システムです。
Yuge全社 / 三浦屋 / ナリサワ / ZMI 等は対象外です。

## 売上の役割分担（重要）
- **Beds24 = 速報・管理会計**：KPI / ADR / RevPAR / 稼働率 / 予約済売上 / 損益分岐速報 / BI先行指標。
  **確定PL仕訳には使いません**（`config/kiraku.yml` `revenue.beds24_creates_journal: false`）。
- **確定PL/BS/CFの売上 = 銀行/OTA入金・会計士データ**（入金・売掛消込ベースが正）。
- **Beds24と銀行入金の差はエラーではなく reconciliation 項目**（warning扱い）。`revenue_reconciliation_report` と
  BI `revenue_data_status`（速報 / 一部入金反映 / 会計確定 / 要確認）で管理。
- 銀行CSV未投入の月は Beds24速報売上を「速報PL」として参照可。

- **売上（速報）**：Beds24 API を速報ソースとして取得（OTA個別取込はしない）
- **入出金**：銀行CSVドロップ運用
- **現金**：レシート画像 + ChatGPT等でCSV化したものを取込（承認制）
- **手動補正**：借入・固定資産・減価償却・会計士調整など
- **出力**：仕訳帳 → 試算表 → PL/BS/CF、および既存Excelテンプレへの書き込み

最終アウトプットは `data/output/<YYYY-MM>/updated_workbook.xlsx`。
**既存テンプレートは絶対に上書きしません。**

---

## 1. セットアップ

正式プロジェクトパスは **`/Users/ginisato/YugeFinance/kiraku-finance-automation`** です。
旧 `~/Documents/YugeFinance/...` は使いません（macOS TCCでlaunchdが`.venv`を読めないため移設済み）。

```bash
cd /Users/ginisato/YugeFinance/kiraku-finance-automation
python3 -m venv .venv
./.venv/bin/pip install -e .          # 開発時: pip install -e ".[dev]"
./.venv/bin/yuge-finance init
```

`init` でディレクトリ・`.env`・`data/ledger.sqlite` を初期化します。

### .env の作り方
```bash
cp .env.example .env
```
`.env` には Beds24 Long Life Token 等の機密情報を書きます。**`.env` は`.gitignore`済みでGit管理対象外です。**
Beds24 tokenは**ローカルのみ**で使用し、Cloudflare側（Worker/GitHub Actions）には一切置きません。

## 2. .env に必要な項目

`init` が `.env.example` から `.env` を作成します。最低限：

| 変数 | 用途 |
|---|---|
| `BEDS24_LONG_LIFE_TOKEN` | **正式採用・最優先**。Beds24 v2 の Long Life Token |
| `BEDS24_REFRESH_TOKEN` / `BEDS24_ACCESS_TOKEN` | フォールバック（Long Life Tokenがあれば不要） |
| `BEDS24_PROPERTY_IDS` | 喜らく=`330695`（カンマ区切り、空=全件） |
| `BEDS24_API_BASE` | 既定 `https://beds24.com/api/v2` |
| `MF_*` | Money Forward（将来対応・現在未使用） |

### Beds24 API 認証（Long Life Token 方式）
1. Beds24 管理画面で **Long Life Token** を発行し、`.env` の `BEDS24_LONG_LIFE_TOKEN` に設定
2. すべての v2 リクエストにヘッダ `token: <token>` を付与（`Authorization: Bearer` は使わない）
3. base URL = `https://beds24.com/api/v2`、予約は `GET /bookings`（`arrivalFrom/arrivalTo` で対象月）
4. 認証確認：
   ```bash
   ./.venv/bin/yuge-finance test-beds24-auth
   # success: True / property count: 1 / property id: 330695 / property name: 喜らく
   ```
   （token本体は一切表示しません）

`BEDS24_LONG_LIFE_TOKEN` が設定されていれば最優先で使用。未設定時は refresh/access token に
フォールバックし、いずれも無ければ `fetch-beds24` は明確なエラー、`close-month` ではスキップして継続します。

## 3. データの置き場所

| 種別 | 置き場所 | 形式 |
|---|---|---|
| 開始残高・会計士YTD | `imports/opening_balance/` | 下記3ファイル（5月末確定） |
| 銀行CSV | `imports/bank/` | 各行=明細。日本語ヘッダ自動判別 |
| 現金レシート原本 | `imports/cash_receipts/images/` | jpg/png/pdf |
| 現金CSV（OCR/ChatGPT出力） | `imports/cash_receipts/csv/` | 下記フォーマット。初期は `needs_review` |
| 確認済み現金CSV | `imports/cash_receipts/reviewed/` | `approved` にして移動 |
| 手動補正CSV | `imports/manual_adjustments/` | 借方=貸方の仕訳 |

### 開始残高ファイル（`imports/opening_balance/`）
会計期間の起点は **2026-05-31**（会計士確定）。2026-06-01以降を自動ロールフォワードします。
- `opening_balance_2026-05-31.csv`：科目別 開始残高（試算表スナップショット）。**借方合計=貸方合計** 必須。
  ```csv
  as_of_date,account,subaccount,debit,credit,memo
  2026-05-31,現預金,,3000000,,会計士確定
  2026-05-31,借入金,長期借入,,26300000,銀行借入残高
  ```
- `accountant_pl_ytd_2026-05.csv` / `accountant_cf_ytd_2026-05.csv`：会計士確定YTD（`item,amount`）。BIの年度連続性に使用（複式仕訳には載せない）。

取込：`yuge-finance ingest-opening`（月指定不要。`close-month` でも自動実行）。
> BSは「開始残高 + 2026-06以降の累積」で算出（ロールフォワード）。PL/CFは当月（期間）ベース。

### 現金レシートCSVフォーマット
```csv
transaction_date,transaction_type,amount,tax_amount,tax_rate,category,vendor,description,payment_method,receipt_file,counterparty,review_status,memo
2026-07-03,現金支払,2480,225,10%,消耗品費,コメリ,掃除用品,現金,receipt_20260703_001.jpg,コメリ,approved,喜らく清掃用品
2026-07-04,現金入金,12000,1091,10%,宿泊売上,現地ゲスト,現金宿泊代,現金,receipt_20260704_001.jpg,現地ゲスト,approved,現地現金回収
```
- `transaction_type`：`現金支払` / `現金入金` / `現金移動` / `立替精算`
- `review_status`：`needs_review` / `reviewed` / `approved`
- **`approved` の取引のみ自動確定仕訳になります。** 他は例外レポート行き。

## 4. 月次の実行手順

個別実行：
```bash
./.venv/bin/yuge-finance ingest-opening                  # 開始残高(初回/更新時のみ)
./.venv/bin/yuge-finance fetch-beds24       --month 2026-07
./.venv/bin/yuge-finance ingest-bank        --month 2026-07
./.venv/bin/yuge-finance ingest-cash        --month 2026-07
./.venv/bin/yuge-finance ingest-adjustments --month 2026-07
./.venv/bin/yuge-finance build-ledger       --month 2026-07
./.venv/bin/yuge-finance export-excel       --month 2026-07
```

一括（推奨）：
```bash
./.venv/bin/yuge-finance close-month --month 2026-07
```
実行順：fetch-beds24 → ingest-bank → ingest-cash → ingest-adjustments → build-ledger → export-excel → 各レポート。

## 5. 出力（`data/output/<YYYY-MM>/`）

`updated_workbook.xlsx`, `beds24_bookings.csv`, `bank_transactions.csv`, `cash_transactions.csv`,
`manual_adjustments.csv`, `journal_entries.csv`, `trial_balance.csv`, `pl_summary.csv`,
`bs_summary.csv`, `cf_summary.csv`, `cash_balance_rollforward.csv`, `exception_report.csv`,
`receipt_review_report.csv`, `validation_report.md`, `monthly_close_report.md`, `processing_log.json`

さらに **BI用** `data/output/<月>/bi/`：
- `bi_snapshot.json`：PL（当月/6月以降累積）・BS・CF・損益分岐KPI・ロールフォワード・YTD（会計士+システム）。
- `bi_daily_timeseries.csv`：日次（宿泊売上/現金入出金/銀行入出金/予約件数/仕訳件数）。
- `bi_validation_status.json`：検証ステータス（BI監視用）。
- `bi_exception_summary.json`：例外サマリ（source/rule/confidence別）。

- **exception_report.csv**：medium/low confidence・未分類（仮勘定）・未承認現金の仕訳候補。手動確認用。
- **receipt_review_report.csv**：現金取引ごとの `review_status`・原本画像照合結果・必要アクション。

### 損益分岐点モデル v2（現体制運営前提。`config/fixed_variable_model.yml`）
旧経営体制（食事・売店・旧人件費・旧派遣料・旧役員報酬）は使わず、現体制固定費＋
現行人件費モデル（`config/labor_model.yml`）＋三浦屋モデル参照の変動費率で算出します。

- **キャッシュBEP**（`cash_operating_breakeven_revenue`）：BI主指標。減価償却費を除く。
- **会計BEP**（`accounting_operating_breakeven_revenue`）：減価償却費を含む会計上の営業黒字ライン。
- **返済込みBEP**（`finance_breakeven_revenue`）：支払利息・元本返済を含む安全ライン。
  返済予定表が未投入の間は元本・利息を0円として扱い（推定しない）、
  `debt_service_status = 予定表未投入` かつ「返済込みBEPは未完全」とBIに明示します。
- **MC費用**：固定15万円/月 ＋ GOPプラス時のみ成功報酬15%（`gop_before_success_fee` / `mc_success_fee` / `gop_after_mc`）。

### 達成率と予約ペースは別指標（重要）
- **達成率**（`cash_operating_breakeven_achievement_rate`）＝現在のBeds24速報売上 ÷ 月間キャッシュBEP。
  月初・月中は低く出るのが当然で、`breakeven_model_status`（達成/未達/大幅未達）はこの値のみで判定します。
- **予約ペース**（`booking_pace_status`: green/yellow/red/unknown）＝月内経過率に対して、
  最終的にBEPを達成できそうかを見る別軸の指標（`accounting/pace_model.py`）。
- **「大幅未達」かつ「予約ペース：要注意/グリーン」は矛盾ではありません。** 大幅未達は現在累計の達成度、
  予約ペースは月末着地見込みです。BIのトップカード下に両者を組み合わせた短いコメントを表示します。

## 6. Excel のどのシートに書くか

| シート | 書込 | 内容 |
|---|---|---|
| 02_銀行API取込 | ✅ | 銀行明細 |
| 03_OTA取込 | ✅ | Beds24予約（キャンセル除外） |
| 04_仕訳帳 | ✅ | 確定仕訳（最終会計ソース） |
| 05_試算表 | ✅(B3のみ) | 対象月セット（集計トリガ） |
| 09_借入返済 | ✅(空き行) | 借入ロールフォワード実績ブロック |
| 13_チェック | ✅(空き行) | 自動検証結果ブロック |
| 06_PL / 07_BS / 08_CF / 10_KPI / 11_モデル連携 | ⛔保護 | 数式セルは一切触らない |

> 仕訳帳に書いた実績は 05_試算表 が `SUMIFS` で自動集計します（科目名・月キーで連動）。
> **01_入力ページ への実績KPI上書きは、フォーキャスト前提を保護するため既定で無効**
> （`config/workbook_map.yml` の `input_page.enabled` で有効化可能）。
> 金額単位：仕訳帳・試算表は **円（実額）**。01_入力ページ等のフォーキャストは千円（別系統）。

## 7. 検証（validation_report.md）

予約ID/銀行/現金の重複、レシート原本照合、未承認現金の確定混入、仕訳・試算表の貸借一致、
BSバランス、CF整合、現金/借入ロールフォワード、未分類件数、対象月外混入、Excel出力存在・テンプレ未上書き
を自動チェックします。重大エラーが0なら総合判定 ✅。

## 8. サンプルデータ

`imports/` 配下に動作確認用サンプル（`sample_bank.csv` 等）を同梱しています。
実運用前に削除してください：
```bash
rm imports/bank/*.csv imports/cash_receipts/csv/*.csv imports/manual_adjustments/*.csv
```

## 9. テスト

```bash
./.venv/bin/python -m pytest -q
```

## 10. 速報BI（Beds24）: refresh-beds24-bi / publish-bi-r2

Beds24速報BIは**会計確定処理と完全に分離**しています。15分ごとの巡回では
仕訳生成・PL/BS/CF確定・Excel更新は一切行いません（Beds24取得・BIファイル再生成のみ）。

```bash
# BIデータ生成のみ（仕訳/Excelは触らない）
./.venv/bin/yuge-finance refresh-beds24-bi --month current --months 2

# Cloudflare公開ディレクトリ(cloudflare/bi-web/public/data/)へローカル反映
./.venv/bin/yuge-finance refresh-beds24-bi --month current --months 2 --publish

# R2(kiraku-bi-data)へ直接アップロード（Worker本体はdeployしない。手動検証用）
./.venv/bin/yuge-finance publish-bi-r2 --dry-run
./.venv/bin/yuge-finance publish-bi-r2
```

`--publish-r2` / `--no-publish-r2` を `refresh-beds24-bi` に渡すと同時にR2アップロードできますが、
**現時点ではlaunchdには自動で組み込んでいません**（手動検証優先）。将来的には
`refresh-beds24-bi --month current --months 2 --publish --publish-r2` に統一する想定です。

## 11. Cloudflare Workers + R2 公開

Worker本体（HTML/JS/CSS配信 + R2読み出し）と、BIデータ（R2）を分離しています。

- Worker: `cloudflare/bi-web/`（`wrangler.toml` / `src/worker.js` / `public/`）
- R2 bucket: **`kiraku-bi-data`**（binding名 `BI_DATA`）。BIデータは `latest/` 以下に固定。
- **Beds24 API tokenはCloudflareに置きません。** Workerは Beds24 API を呼びません（表示専用）。
- **15分ごとに `wrangler deploy` はしません。** Worker本体のdeployは変更時のみ
  （手動 `npm run deploy` または後述のGitHub Actions）。

```bash
cd cloudflare/bi-web
npm install
npx wrangler login                    # 初回のみ
npx wrangler r2 bucket create kiraku-bi-data   # 初回のみ（CloudflareダッシュボードでR2有効化が必要な場合あり）
npx wrangler deploy --dry-run
npx wrangler deploy                   # Worker本体を手動デプロイ
```

## 12. launchd（15分巡回）

`launchd/com.yuge.kiraku.beds24-bi-refresh.plist.template` を `~/Library/LaunchAgents/` に
配置し `launchctl bootstrap` します。**現時点では `refresh-beds24-bi --publish` まで**で、
R2アップロード（`--publish-r2`）は含みません（手動検証が済むまで変更しません）。

## 13. GitHub / CI

`.github/workflows/deploy-worker.yml` が `main` への push時に **Worker本体だけ** デプロイします
（`cloudflare/bi-web/**` の変更のみトリガ）。**BIデータはGitHub Actionsではデプロイしません**
（BIデータの配信はMac launchd → R2アップロード → Workerが都度R2から読む、という経路のみ）。

必要なGitHub Secrets：`CLOUDFLARE_API_TOKEN`（Workers編集権限のみの最小権限トークン）、
`CLOUDFLARE_ACCOUNT_ID`。

### Gitに含めないもの
`.gitignore` により以下は追跡されません：`.env`系、`data/`配下の生成物・DB、`logs/`、
`imports/`配下の実データ（銀行CSV・現金レシート・開始残高・返済予定表等）、
`*.pdf` / `*.xls` / `*.xlsx`（`templates/*.xlsx` のみ例外）。
会計士資料・銀行CSV・給与明細・Beds24実データ・個人情報は**絶対にpushしないでください**。
サンプルは `examples/` にダミー値で置いています。

## 14. 未対応・将来対応

- Beds24 の **泊日按分** 計上（現状はチェックイン月一括。`config/kiraku.yml` で切替準備済み）
- **Money Forward API**：`src/yuge_finance/api/moneyforward_client.py` は stub。
  次ステップ＝ OAuth2.0 → token保存 → 事業者/会計期間選択 → 仕訳/明細取得 → CSVドロップから差替。
- **銀行API直連携**：`bank_api_client.py` は stub（現状はCSVドロップ）。
- 数式の実値化：`recalculation.py` が LibreOffice 検出時に対応。通常は Excel で開けば自動再計算。
- launchdへの `--publish-r2` 組み込み（手動検証後）。
- 元本返済・支払利息：`imports/loan_repayment_schedule/` に返済予定表を投入すれば自動反映されます。
  **予定表が確定するまでは元本・利息を推定しません**（`debt_service_status = 予定表未投入`）。

## 注意事項
- 会計・税務の最終判断はシステムで断定しません。例外・要確認はレポートで人が確認します。
- 喜らく以外の物件は混在させないでください。
- **元本返済は返済予定表が確定してから `imports/loan_repayment_schedule/` に投入してください。** 未投入の間、
  返済込みBEPは「未完全」としてBIに明示され、元本・利息は0円のまま推定しません。
