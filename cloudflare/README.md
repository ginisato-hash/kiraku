# 喜らく 速報BI（Cloudflare Workers + R2）

Beds24速報KPIを公開するためのディレクトリ。**Worker本体（表示）とBIデータ（R2）を分離**している。
**財務会計の確定値ではありません**（速報・宿泊運営KPI）。確定PL/BS/CFは銀行/OTA入金ベースで別管理。

詳細・手順は **[bi-web/README.md](bi-web/README.md)** を参照。

## 構成
```
cloudflare/
  bi-web/
    wrangler.toml         # kiraku-bi / [assets]ASSETS / [[r2_buckets]]BI_DATA=kiraku-bi-data
    src/worker.js         # /health /api/* /data/* → R2、その他 → ASSETS
    public/index.html     # 速報BI画面（ASSETS配信）
    public/app.js         # /data/* を取得（= Worker→R2）
    public/data/          # ローカル publish-bi 出力（Worker配信はR2を使う）
    package.json          # dev/deploy/r2:* scripts
  staff-ops/
    wrangler.toml         # kiraku-staff-ops / 別R2バケット(kiraku-staff-ops-data) / 別KVネームスペース
    src/worker.js         # Daily Ops / 清掃指示書 / 宿泊者名簿印刷（bi-webとは完全に別デプロイ・別データ境界）
    public/               # Daily Ops画面・印刷専用ページ・清掃スマホ画面（財務情報は一切含まない）
    ACCESS_SETUP.md        # Cloudflare Access（Zero Trust）手動設定手順（本リポジトリでは自動化できない）
  scripts/publish_bi.py   # ローカル公開ヘルパー（= yuge-finance publish-bi）
  wrangler.toml.example   # （旧Pages用。Workers版は bi-web/wrangler.toml が正）
```

`staff-ops/` は喜らく一般スタッフ・清掃担当者向け。**売上・ADR・RevPAR・宿泊料金・OTA手数料等の経営情報は
一切配信しない**（Beds24予約データのうち運用に必要な項目だけをallow-list方式で別R2バケットへ出力する新系統。
`bi-web`とはR2バケットもCloudflare Workerも完全に分離しており、コードにバグがあっても構造的に財務データへ
到達できない）。詳細は [staff-ops/README.md](staff-ops/README.md) を参照。

## 方針（重要）
- Worker本体は **HTML/JS/CSS配信 + R2読み出しだけ**。Beds24 API は呼ばない。
- BIデータは R2 bucket **`kiraku-bi-data`** の **`latest/`** に置く（キー名固定）。
- **Beds24 API token は Cloudflare に置かない。** Beds24取得は Mac の 15分 launchd のみ。
- **15分ごとに `wrangler deploy` しない。** Worker本体のdeployは変更時のみ。
- **Phase 1**: R2への投入は手動（`npm run r2:put:all`）。
- **Phase 2**（未実装）: Mac launchd から R2 へ自動アップロード（既存フローを壊さない別CLIで追加）。
- 会計処理（仕訳/PL/BS/CF/Excel）には触らない。

## ローカル確認（データ生成は従来どおり）
```bash
cd /Users/ginisato/YugeFinance/kiraku-finance-automation
./.venv/bin/yuge-finance refresh-beds24-bi --month current --months 2 --publish   # public/data/ 更新
cd cloudflare/bi-web && npm install && npx wrangler dev                            # Worker+R2をローカル起動
```

## 表示項目
最終更新時刻 / Beds24当月速報売上 / キャンセル除外速報売上 / キャンセル保持額 /
ADR / RevPAR / 稼働率 / 損益分岐達成率(速報) / 入金月OTA入金 /
revenue_data_status / revenue_comparison_status（同月比較対象外）/ validation / exception件数。

> Beds24宿泊月速報と銀行入金月実績は対象コホートが異なるため、**同月差分比較を主指標に出しません**。
