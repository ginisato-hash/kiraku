# 喜らく 速報BI — Cloudflare Workers + R2（データ分離）

Beds24速報BIの公開構成。**Worker本体（表示）とBIデータ（R2）を分離**している。

- Worker本体は **HTML/JS/CSS の配信（Static Assets）** と **R2からのBIデータ読み出し** を行い、
  `/health` と `/internal/*` 以外は **admin gate**（下記）の内側に置ける。
- BIデータは R2 bucket **`kiraku-bi-data`** の **`latest/`** 以下に置く。
- **Beds24 API token は Cloudflare に置かない。** Beds24取得は Mac の 15分 launchd のみ。
- Worker は Beds24 API を呼ばない（表示専用）。
- **15分ごとに `wrangler deploy` はしない。** Worker本体のdeployは変更時のみ手動。
- 会計処理（仕訳 / PL/BS/CF / Excel）には触らない。

## 構成
```
cloudflare/bi-web/
  wrangler.toml          # name=kiraku-bi / [assets] ASSETS(run_worker_first) / [[r2_buckets]] BI_DATA=kiraku-bi-data
  package.json           # dev/deploy/r2:* scripts
  src/worker.js          # ルーティング（/health /internal/* → 各自、/api/* /data/* → gate → R2、その他→gate → ASSETS）
  public/                # Static Assets（ASSETS binding, directory=./public）
    index.html
    app.js               # api/* と data/* をページ相対URLで取得（= Worker→R2）
    .assetsignore        # data/ を静的アセットから除外（BIデータを静的公開しない）
    data/                # ローカル publish-bi の出力先（Worker配信はR2を使うため未使用）
```

## Worker ルート
| パス | 認可 | 返すもの |
|---|---|---|
| `GET /health` | なし（常に公開） | JSON（R2を読まない） |
| `POST /internal/beds24/booking-webhook` | `X-Kiraku-Webhook-Token`（Beds24 webhook） | event_seq加算のみ |
| `POST /internal/bi-refresh/complete`, `/shadow-observation` | Bearer `BI_REFRESH_CALLBACK_SECRET` | GitHub Actionsからのcallback |
| `GET /internal/bi-refresh/status`, `POST /mode`, `/force` | Bearer `BI_REFRESH_OPS_SECRET` | 運用者用 |
| `GET /api/months` | **admin gate** | manifestの月リスト（R2 `latest/manifest.json`） |
| `GET /api/manifest` | **admin gate** | R2 `latest/manifest.json` |
| `GET /api/snapshot[?month=YYYY-MM]` | **admin gate** | R2 `latest/bi_snapshot.json`（月指定時は `latest/months/{YYYY-MM}/`） |
| `GET /data/{manifest.json,bi_snapshot.json,bi_validation_status.json,bi_exception_summary.json,bi_daily_timeseries.csv,bi_monthly_kpi.csv}` | **admin gate** | R2 `latest/` の同名オブジェクト |
| `GET /data/months/{YYYY-MM}/{bi_snapshot.json,...}` | **admin gate** | R2 `latest/months/{YYYY-MM}/` の同名オブジェクト |
| その他（`/`, `/app.js` 等） | **admin gate** | `env.ASSETS.fetch()`（index.html / app.js 等） |

R2にオブジェクトが無ければ 404 JSON、例外時は 500 JSON。Cache-Control は `no-store`。
`/internal/*` のうち上表に無い未知のパスは gate の対象（例外扱いしない）。

## Admin gate（二段階有効化）
`/health` と `/internal/*`（上表の各自の認可を持つもの）以外のすべて（`/api/*`、`/data/*`、
`/data/months/*`、静的UI）は、リクエストヘッダ `x-kiraku-admin-gate` が Worker Secret
`BI_ADMIN_GATE_SECRET` と一致した場合だけ通し、他は **理由を示さない同一の404** を返す
（HTTPメソッドを問わない。比較は定数時間: `src/timingSafeEqual.js`）。

- **有効化条件**: `BI_ADMIN_GATE_SECRET` が設定されている、または `BI_GATE_REQUIRED` が文字列 `"true"`（完全一致）。
  どちらも無い間は **従来どおり開いている**（このコードをmerge/deployしただけでは既存の閲覧は止まらない）。
- **fail-closed**: `BI_GATE_REQUIRED="true"` で秘密が無い場合、gate対象のルートは全て404。
- **静的資産の迂回防止**: `wrangler.toml` の `[assets] run_worker_first = true` により、静的ファイルも
  先にWorkerを通り、gate通過後に `env.ASSETS.fetch` で配信される（無いと `/` や `/app.js` がgateを素通りする）。
  `public/.assetsignore` が `public/data/`（ローカル `publish-bi` の出力）を静的アセットから除外する。
- **呼び出し側の接点**: 管理ページ側からservice bindingでこのWorkerを呼ぶ際に、ヘッダ
  `x-kiraku-admin-gate: <BI_ADMIN_GATE_SECRET と同じ値>` を付ける。値はコード・設定・テスト・ログに書かない。

### 有効化手順
1. このバージョンをdeployする（秘密もフラグも無いので挙動は従来どおり）。
2. 呼び出し側（管理ページ）と同じ値を Worker Secret に設定する。**設定した時点でgateが有効になる**:
   `cd cloudflare/bi-web && npx wrangler secret put BI_ADMIN_GATE_SECRET`（値は対話入力。コマンド履歴・CIログに残さない）。
3. 確認: ヘッダ付きで `/`・`/api/snapshot`・`/data/bi_snapshot.json` が200、**ヘッダ無しの直アクセスは
   `/`・`/app.js`・`/api/*`・`/data/*` すべて404**、`/health` は200のまま、`/internal/*`
   （webhook・callback・ops）は従来どおり各自の認可で動く。
4. 問題が無ければ `wrangler.toml` の `[vars]` に `BI_GATE_REQUIRED = "true"` を足して再deploy
   （以後は秘密を失っても/消しても開き直さず、gate対象は全て404のまま）。

### ロールバック
- 手順2の段階（`BI_GATE_REQUIRED` 未設定）: `npx wrangler secret delete BI_ADMIN_GATE_SECRET` だけで従来どおり開く。
- 手順4の後: `BI_GATE_REQUIRED` を外して再deployし、その上で秘密を削除する
  （フラグを残したまま秘密を消すと全て404になる）。

## 前置パス配下での配信
UI（`public/`）はページURL相対のパス（`api/...` `data/...` `./app.js` `./styles.css`）だけを使うので、
管理ページ側が `/admin/bi/` のような前置パス配下で配信しても、ルート直下（`/`）でも同じコードで動く。
**ページURLは末尾スラッシュ付きで開くこと**（`/admin/bi` ではなく `/admin/bi/`。呼び出し側で
リダイレクトするか、前置パスを外してWorkerへ渡す）。回帰テスト: `test/uiRelativePaths.test.mjs`。

## 内部コンシューマはR2を直接読む
gateを有効にすると、credential無しの公開GETは404になる。そのため次は公開URLではなく
`wrangler r2 object get --remote`（既存の `CLOUDFLARE_API_TOKEN` / `wrangler login`）でR2を読む:
`publish-bi-r2 --preserve-bank-fields-from-r2` の銀行項目引き継ぎ、shadow観測のbaseline、
`refresh-bi-r2.yml` の検証・summary、`scripts/refresh_beds24_bi_and_publish_r2.sh` の確認。
読めなかった場合は `bank_fields_source=not_available`（銀行項目が引き継げなかった印）とstderr警告で見える。
`deploy-worker.yml` は、deploy前に BIバケットの r2.dev 公開が無効であることを読み取り検証する
（有効、または確認不能ならdeployを止める。有効なら `wrangler r2 bucket dev-url disable kiraku-bi-data`）。

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

# 5) 確認（gate有効時は x-kiraku-admin-gate ヘッダが必要。無ヘッダは404）
curl https://<worker-url>/health
curl -H "x-kiraku-admin-gate: $GATE_VALUE" https://<worker-url>/api/manifest
curl -H "x-kiraku-admin-gate: $GATE_VALUE" https://<worker-url>/api/snapshot
curl -H "x-kiraku-admin-gate: $GATE_VALUE" https://<worker-url>/data/bi_snapshot.json
curl -H "x-kiraku-admin-gate: $GATE_VALUE" https://<worker-url>/
```

## R2への自動publish
R2への更新は GitHub Actions（`refresh-bi-r2.yml`、起動は本Workerの Cron Trigger）が
`yuge-finance publish-bi-r2` で行う。15分ごとの `wrangler deploy` はしない（Worker本体は不変、データだけR2更新）。

## やらないこと
Beds24 token を Cloudflare に置かない / Worker から Beds24 API を呼ばない / 会計処理を触らない。
同月比較差額は主指標に出さない（`revenue_comparison_status = 同月比較対象外` を表示）。
