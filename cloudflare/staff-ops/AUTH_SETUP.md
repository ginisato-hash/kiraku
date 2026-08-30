# kiraku-staff-ops — 認証セットアップ / 運用手順

**Cloudflare Access(Zero Trust)は不採用です。** 代わりに、このWorker自身が
スタッフ共通パスワード + 署名済みsession cookieで認証します（実装済み・テスト済み）。
以下は現状のステータスと、ユーザー側でCloudflareダッシュボードから行う必要がある
2つのSecret設定手順です。

## 現状(2026-08-30時点)

- ✅ `CLOUDFLARE_API_TOKEN` の権限問題(Workers Scripts:Edit / Workers KV Storage:Edit 不足)は解消済み。
  `deploy-staff-worker.yml`・`deploy-worker.yml`(既存経営BI)とも `workflow_dispatch` で green を確認済み。
- ✅ R2バケット `kiraku-staff-ops-data` の公開アクセス(r2.dev)は自動チェックで無効化・確認済み
  （デプロイ毎に自動実行、有効化されていたらデプロイ自体を失敗させる）。
- ✅ パスワード認証(このファイルの本題)はコード・テストとも実装完了。
  **ただし以下2つのWorker Secretが未設定のため、現在ログインは機能しません**
  （`STAFF_OPS_SESSION_SECRET` 未設定時はfail-closed = 全ルートがログイン画面へ302
  リダイレクトされ続け、`STAFF_OPS_PASSWORD` 未設定時はログイン試行が503を返します）。
- ⏸ `STAFF_OPS_AUTH_CONFIRMED` repository variable は未設定（本番でのログイン動作確認が
  済むまで意図的に未設定のまま。設定するまでstaff snapshotはR2へ一切publishされません）。

## 必要な設定（Cloudflareダッシュボード操作・2箇所のみ）

**設定場所**: https://dash.cloudflare.com/ → 対象アカウント → **Workers & Pages** →
**kiraku-staff-ops** → **Settings** タブ → **Variables and Secrets** → **Add** →
種別は必ず **Secret**（Text/Plaintextではなく）を選択。

**設定するSecret名（値はユーザー側で決めて入力。私には共有しないでください）**:

| Secret名 | 値の決め方 |
|---|---|
| `STAFF_OPS_PASSWORD` | スタッフ全員に共有するログインパスワード（現場で口頭伝達しやすい程度の長さで可） |
| `STAFF_OPS_SESSION_SECRET` | session cookie署名用のランダム文字列。**32文字以上の英数字記号推奨**。決め方が分からなければ、お使いのPCのターミナルで `openssl rand -base64 32` を実行して出力をそのまま貼り付けてください |

代わりに`wrangler` CLIをお持ちの場合は以下でも設定できます（実行はユーザー側の端末で）:
```bash
cd cloudflare/staff-ops
npx wrangler secret put STAFF_OPS_PASSWORD
npx wrangler secret put STAFF_OPS_SESSION_SECRET
```

設定後、次回の `wrangler deploy`（次のpush、または
`gh workflow run deploy-staff-worker.yml -R ginisato-hash/kiraku`）を待たずに
**Secretは即座に反映されます**（Worker再デプロイ不要 — Cloudflareの仕様）。

## パスワードをローテーションしたい場合

`STAFF_OPS_PASSWORD` を変更しただけでは、既にログイン済みの端末のsession cookie
（最大30日間有効）はそのまま有効です。**全端末を強制ログアウトさせたい場合**は、
`cloudflare/staff-ops/wrangler.toml` の `[vars] AUTH_VERSION` の値を変更してcommit・
deployしてください（例: `"1"` → `"2"`）。これにより発行済みの全session署名が
即座に無効になります。

## 完了後の確認手順（私が本番URLで実施します）

1. 未ログイン状態で `https://kiraku-staff-ops.s-sato-dce.workers.dev/` へアクセス → `/login` へ302
2. `/api/daily-ops?date=...` へ未ログインで直接アクセス → 401、PIIなし
3. 誤ったパスワードでログイン → 「パスワードが違います」
4. 正しいパスワードでログイン → Daily Opsへ遷移、session cookieが
   `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=2592000`(30日)であることを確認
5. ログアウト → 再度未ログイン状態に戻ることを確認

---

# R2バケットの公開設定について（自動チェック済み・継続監視）

`deploy-staff-worker.yml` の「Ensure staff-ops R2 bucket has NO public access」ステップが
デプロイの度に `wrangler r2 bucket dev-url disable` を実行し、`dev-url get` の実出力
（`"is enabled at '...'"` / `"is disabled."`）だけを見て状態を判定、公開されていたら
**デプロイ自体を失敗させます**。2026-08-29の初回実行時に実際に公開アクセスが有効に
なっていたのを検知・無効化した実績があります。

---

# データゲートを開く（パスワード認証の本番動作確認 **後** の最終ステップ）

上記「完了後の確認手順」まで完了したら:

1. https://github.com/ginisato-hash/kiraku → **Settings** → **Secrets and variables** → **Actions**
   → **Variables** タブ → **New repository variable**
2. Name: `STAFF_OPS_AUTH_CONFIRMED` / Value: `1`
3. **repository variableを設定しただけでは既存のworkflowは自動実行されません。**
   ```bash
   gh workflow run refresh-bi-r2.yml -R ginisato-hash/kiraku
   ```
4. 完了後、`kiraku-staff-ops-data` R2バケットの `latest/staff_ops_snapshot.json` に
   実データが書き込まれたことを確認する（構造のみ確認。実在氏名/電話/住所を
   ターミナル/ログへ大量出力しない）。

これらを行うまでは、Worker自体はデプロイ済みでもR2バケットが空のため、
すべての日付が404/空状態を返すだけです。

---

# 清掃指示書の状態（原本写真到着まで）

- Cleaning infrastructure: **READY**
- Cleaning visual reproduction: **WAITING FOR SOURCE IMAGE**
- Cleaning feature flag: **OFF**（`public/featureFlags.js` の `CLEANING_VISUAL_READY = false`）

原本写真到着後、`cleaningSheetTemplate.js` を写真ベースで100%再現し、acceptance通過後に
`CLEANING_VISUAL_READY` を `true` へ切り替えます。
