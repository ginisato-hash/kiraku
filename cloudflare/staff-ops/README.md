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

## 構成
```
cloudflare/staff-ops/
  wrangler.toml           # kiraku-staff-ops / [assets]ASSETS / [[r2_buckets]]OPS_DATA / [[kv_namespaces]]CLEANING_OVERRIDES
  package.json            # dev/deploy/test scripts
  src/worker.js           # ルーティング
  src/cleaningOverrides.js
  public/
    index.html, dailyOps.js, dailyOpsViewModel.js, styles.css   # Daily Ops画面
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
| パス | 説明 |
|---|---|
| `GET /health` | JSON（R2/KV読まない） |
| `GET /api/daily-ops?date=YYYY-MM-DD` | その日の arrivals/departures/stayovers/cleaning（R2） |
| `GET /api/cleaning?date=YYYY-MM-DD` | その日の清掃行（R2ベース + KV上書きマージ済み） |
| `POST /api/cleaning/override` | room_number/notesの当日限定上書き（`Cf-Access-Authenticated-User-Email`ヘッダー必須・無ければ403） |
| `GET /` | Daily Ops画面 |
| `GET /ops/print/guest-register?date=` | 宿泊者名簿 印刷専用ページ（1予約=A4 1ページ、window.print自動発火） |
| `GET /ops/print/cleaning?date=` | 清掃指示書 印刷専用ページ（**現在は仮レイアウト**） |
| `GET /cleaning/today?date=` | 清掃担当者向けスマホ画面（既定=JST今日） |

## アクセス制御（必須・手動設定が残っている）
**[ACCESS_SETUP.md](ACCESS_SETUP.md) を参照。** Cloudflare Access（Zero Trust）の
Application/Policy作成はダッシュボード操作が必要で、本リポジトリのCI/コードだけでは
自動化できない。**本番URLをスタッフへ周知する前に必ず完了させること。**

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
