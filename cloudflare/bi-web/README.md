# 喜らく 速報BI — Cloudflare Workers + R2（データ分離）

Beds24速報BIの公開構成。**Worker本体（表示）とBIデータ（R2）を分離**している。

- Worker本体は **HTML/JS/CSS の配信（Static Assets）** と **R2からのBIデータ読み出し** だけを行う。
- BIデータは R2 bucket **`kiraku-bi-data`** の **`latest/`** 以下に置く。
- **Beds24 API token は Cloudflare に置かない。** Beds24取得は Mac の 15分 launchd のみ。
- Worker は Beds24 API を呼ばない（表示専用）。
- **15分ごとに `wrangler deploy` はしない。** Worker本体のdeployは変更時のみ手動。
- 会計処理（仕訳 / PL/BS/CF / Excel）には触らない。

## 構成
```
cloudflare/bi-web/
  wrangler.toml          # name=kiraku-bi / [assets] ASSETS / [[r2_buckets]] BI_DATA=kiraku-bi-data
  package.json           # dev/deploy/r2:* scripts
  src/worker.js          # ルーティング（/health /api/* /data/* → R2、その他→ASSETS）
  public/                # Static Assets（ASSETS binding, directory=./public）
    index.html
    app.js               # /data/*.json,/data/*.csv を取得（= Worker→R2）
    data/                # ローカル publish-bi の出力先（Worker配信はR2を使うため未使用）
```

## Worker ルート
| パス | 返すもの |
|---|---|
| `GET /health` | JSON（R2を読まない） |
| `GET /api/manifest` | R2 `latest/manifest.json` |
| `GET /api/snapshot` | R2 `latest/bi_snapshot.json` |
| `GET /data/manifest.json` | R2 `latest/manifest.json` |
| `GET /data/bi_snapshot.json` | R2 `latest/bi_snapshot.json` |
| `GET /data/bi_daily_timeseries.csv` | R2 `latest/bi_daily_timeseries.csv` |
| `GET /data/bi_monthly_kpi.csv` | R2 `latest/bi_monthly_kpi.csv` |
| `GET /data/bi_validation_status.json` | R2 `latest/bi_validation_status.json` |
| `GET /data/bi_exception_summary.json` | R2 `latest/bi_exception_summary.json` |
| その他 | `env.ASSETS.fetch()`（index.html / app.js 等） |

R2にオブジェクトが無ければ 404 JSON、例外時は 500 JSON。Cache-Control は `no-store`。

## Phase 1 セットアップ（R2投入は手動）
```bash
cd /Users/ginisato/YugeFinance/kiraku-finance-automation

# 1) BIデータ最新化（ローカル）。既存フローを壊さない。
./.venv/bin/yuge-finance refresh-beds24-bi --month current --months 2 --publish

cd cloudflare/bi-web
npm install

# 2) R2 bucket 作成（要 Cloudflareログイン: npx wrangler login）
npx wrangler r2 bucket create kiraku-bi-data

# 3) R2 へ手動アップロード（latest/ 以下。キー名は固定）
npx wrangler r2 object put kiraku-bi-data/latest/manifest.json            --file public/data/manifest.json
npx wrangler r2 object put kiraku-bi-data/latest/bi_snapshot.json         --file public/data/bi_snapshot.json
npx wrangler r2 object put kiraku-bi-data/latest/bi_daily_timeseries.csv  --file public/data/bi_daily_timeseries.csv
npx wrangler r2 object put kiraku-bi-data/latest/bi_monthly_kpi.csv       --file public/data/bi_monthly_kpi.csv
npx wrangler r2 object put kiraku-bi-data/latest/bi_validation_status.json --file public/data/bi_validation_status.json
npx wrangler r2 object put kiraku-bi-data/latest/bi_exception_summary.json --file public/data/bi_exception_summary.json
# まとめて: npm run r2:put:all

# 4) Worker deploy（変更時のみ。15分ごとには実行しない）
npx wrangler deploy

# 5) 確認
curl https://<worker-url>/health
curl https://<worker-url>/api/manifest
curl https://<worker-url>/api/snapshot
curl https://<worker-url>/data/bi_snapshot.json
curl https://<worker-url>/
```

## Phase 2（今回は未実装）
- Mac launchd の 15分処理から **R2へ自動アップロード**（既存 `refresh-beds24-bi` / `publish-bi` / launchd を壊さない別CLIとして追加予定）。
- 15分ごとの `wrangler deploy` はしない（Worker本体は不変、データだけR2更新）。

## やらないこと
Beds24 token を Cloudflare に置かない / Worker から Beds24 API を呼ばない / 会計処理を触らない。
同月比較差額は主指標に出さない（`revenue_comparison_status = 同月比較対象外` を表示）。
