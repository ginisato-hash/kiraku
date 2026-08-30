# 喜らく スタッフ Daily Ops / 清掃指示 — Cloudflare Workers（財務データ非表示）

一般スタッフ・清掃担当者向け。**売上・ADR・RevPAR・宿泊料金・OTA手数料等の経営情報は一切配信しない。**
`cloudflare/bi-web`（経営BI）とは Worker・R2バケット・KVネームスペースすべて完全に別。

- Worker本体は **HTML/JS/CSS の配信（Static Assets）** と **R2/KVからの読み書き** だけを行う。
- 運用データは R2 bucket **`kiraku-staff-ops-data`** の **`latest/staff_ops_snapshot.json`** に置く。
  生成元は既存Kiraku BIパイプラインが取得・キャッシュ済みのBeds24 raw JSON（Beds24 APIを
  二重に呼ばない）。`yuge-finance export-daily-ops` が生成し、`refresh-bi-r2.yml` の中で
  15分毎にR2へpublishする（既存cadenceを共有。新規cronは追加していない）。
- **Beds24 API token はこのWorkerに置かない。** Worker自身はBeds24 APIを一切呼ばない。
- 清掃指示のroom_number/notes上書きだけは KV **`CLEANING_OVERRIDES`** に保存する
  （date+room_typeキー。Beds24 raw dataそのものは変更しない）。
- **認証: Cloudflare Access(Zero Trust)は不採用。** スタッフ共通パスワード
  (Worker Secret `STAFF_OPS_PASSWORD`) + 署名済みsession cookie(HMAC-SHA256,
  Worker Secret `STAFF_OPS_SESSION_SECRET`)による、このWorker自身の認証ミドルウェアで
  全route(`/health`, `/login`, `/login.js`, `/styles.css`, `/api/auth/*`を除く)を
  デフォルト拒否(default-deny)保護する。詳細・Secret設定手順は
  **[AUTH_SETUP.md](AUTH_SETUP.md)** を参照。

## 構成
```
cloudflare/staff-ops/
  wrangler.toml           # kiraku-staff-ops / [assets]ASSETS / [[r2_buckets]]OPS_DATA / [[kv_namespaces]]CLEANING_OVERRIDES / [vars]AUTH_VERSION
  package.json            # dev/deploy/test scripts
  src/worker.js           # ルーティング + 認証ミドルウェア
  src/auth.js             # session署名/検証・パスワード比較・rate limit（純粋関数）
  src/cleaningOverrides.js
  public/
    index.html, dailyOps.js, dailyOpsViewModel.js, styles.css   # Daily Ops画面
    login.html, login.js   # ログイン画面（未認証時のみ到達可能）
    jst.js                 # JST "今日" の唯一の定義（全ページ共通）
    financialGuard.js      # 財務キー非混入の実行時アサーション
    printUtils.js           # printField()（None等の非表示化）/ waitForPrintReady()
    guestRegisterTemplate.js       # 宿泊者名簿（完成版・写真待ち不要）
    cleaningSheetTemplate.js       # 清掃指示書 — ⚠ 仮レイアウト（原本写真待ち）
    ops/print/guest-register.html + print-guest-register.js   # /ops/print/guest-register
    ops/print/cleaning.html + print-cleaning.js                # /ops/print/cleaning
    cleaning/today.html + today.js                             # /cleaning/today（スマホ）
  test/                    # node:assert手動テスト（bi-webと同じ流儀）。npm test で全実行。
```

## Worker ルート
| パス | 認証 | 説明 |
|---|---|---|
| `GET /health` | 不要 | JSON（R2/KV読まない） |
| `GET /login`, `GET /login.js`, `GET /styles.css` | 不要 | ログイン画面表示に必要な静的アセットのみ |
| `POST /api/auth/login` | 不要 | `{password}` → 成功時session cookie発行 |
| `POST /api/auth/logout` | 不要 | session cookie失効 |
| `GET /` | **必須** | Daily Ops画面（未認証は`/login`へ302） |
| `GET /api/daily-ops?date=YYYY-MM-DD` | **必須** | その日の arrivals/departures/stayovers/cleaning（未認証は401 JSON） |
| `GET /api/cleaning?date=YYYY-MM-DD` | **必須** | その日の清掃行（R2ベース + KV上書きマージ済み） |
| `POST /api/cleaning/override` | **必須 + Origin一致** | room_number/notesの当日限定上書き |
| `GET /ops/print/guest-register?date=` | **必須** | 宿泊者名簿 印刷専用ページ（1予約=A4 1ページ、window.print自動発火） |
| `GET /ops/print/cleaning?date=` | **必須** | 清掃指示書 印刷専用ページ（**現在は仮レイアウト**） |
| `GET /cleaning/today?date=` | **必須** | 清掃担当者向けスマホ画面（既定=JST今日） |

上記以外の未列挙パス（`/api/*`含む）もすべて同じ認証ミドルウェアでデフォルト拒否される
（「HTMLだけ認証してAPI URLを直接叩けば取得可能」を防ぐため、判定はルーティングの
どのハンドラよりも先に行う）。

## 認証セットアップ（必須・Cloudflareダッシュボードでの2つのSecret設定が残っている）
**[AUTH_SETUP.md](AUTH_SETUP.md) を参照。** `STAFF_OPS_PASSWORD` / `STAFF_OPS_SESSION_SECRET`
の2つのWorker Secretを設定するまでログインは機能しない（fail-closed設計のため、
未設定の間は全ルートが安全にログイン画面へリダイレクトされ続けるだけで、データ漏洩は起きない）。

## ローカル確認
```bash
cd cloudflare/staff-ops
npm install
npm test                 # 97 checks
npx wrangler dev --port 8799
# 別ターミナルでfixtureデータをローカルR2へ投入
npx wrangler r2 object put kiraku-staff-ops-data/latest/staff_ops_snapshot.json \
  --file test/fixtures/staff_ops_snapshot.sample.json --content-type application/json --local
```

## 清掃指示書の視覚デザインについて
`cleaningSheetTemplate.js` / `public/ops/print/cleaning.html` / `public/cleaning/today.html` の
見た目は**現時点では仮**。既存の手書き清掃指示書の原本写真確認後、
「原本と可能な限り同じ見た目」に差し替える（ユーザーからの明示的な指示）。

**`public/featureFlags.js` の `CLEANING_VISUAL_READY = false` により、一般スタッフの通常導線
（Daily Ops上のボタン、`/ops/print/cleaning`、`/cleaning/today`）からはこの仮visualへ到達
できないようにガードされている。** 内部QAのみ各URLへ `?preview=1` を付けて直接確認できる
（スタッフへは絶対に案内しないこと）。原本写真の分析・acceptance通過後に `true` へ切り替える。
データモデル・分類ロジック・KV override機構・print/mobileのroute自体はこのフラグと無関係に
production-readyで、フラグはあくまで「見た目」の露出だけを止めている。

## やらないこと
Beds24 APIをこのWorkerから呼ばない / 売上・価格・ADR・RevPAR・手数料等を一切配信しない /
Beds24 raw dataの書き換え（overrideはKVのみ） / 会計処理には触らない。
