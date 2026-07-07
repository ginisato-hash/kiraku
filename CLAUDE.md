# CLAUDE.md — 喜らく単体 会計自動化

このリポジトリで作業する際の必須ルール。

## 対象
- **喜らく単体のみ**。Yuge全社 / 三浦屋 / ナリサワ / ZMI は対象外。命名・出力・設定すべて喜らく前提。

## 絶対禁止
- 既存Excelテンプレート（`templates/`）を上書きしない。出力は必ず `data/output/<month>/`。
- `06_PL` / `07_BS` / `08_CF` / `10_KPI` / `11_モデル連携` の数式セルを値で潰さない。
- OTA個別CSV/API取込を実装しない（売上はBeds24のみ）。
- 未分類取引を勝手に確定しない（仮勘定 + 例外レポート）。
- `approved` 以外の現金取引を確定仕訳にしない。
- rawデータ（`data/raw/`）を上書きしない。
- 同じCSV/APIデータを二重計上しない（`import_hash` / `booking_id` で排除）。
- 会計・税務の最終判断をシステムで断定しない。

## アーキテクチャ要点
- データフロー：取込（API/CSV）→ `normalize`（dataclass + import_hash）→ `db`（sqlite, UNIQUE制約）
  → `accounting`（仕訳→試算表→3表）→ `excel`（テンプレへ書込）+ `reports`。
- **04_仕訳帳が最終会計ソース**。05_試算表以降は `SUMIFS` で自動集計（科目名・`yyyy/mm` 月キー連動）。
  よって仕訳の借方/貸方科目は `config/accounts.yml` の22科目名と完全一致させること。詳細は補助科目で。
- 金額単位：仕訳帳・試算表は**円**。01_入力ページ系フォーキャストは千円（別系統）。
- 自動確定は `confidence == high` のみ。medium/low と未承認現金は例外レポートへ。

## 設定ファイル（`config/`）
`kiraku.yml`（全体・人件費・MC・税）/ `accounts.yml`（科目・category_map）/ `beds24.yml` /
`journal_rules.yml`（銀行）/ `cash_rules.yml`（現金）/ `receipt_categories.yml` / `workbook_map.yml`（Excelセル写像）。

## 変更時の確認
```bash
./.venv/bin/python -m pytest -q
./.venv/bin/yuge-finance close-month --month 2026-07
```
`validation_report.md` に重大エラーが残っていないこと、貸借一致・テンプレ未上書き・数式保持を確認。
