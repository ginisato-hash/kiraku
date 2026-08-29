# 【最優先・ブロッカー】既存トークンの権限拡張が必要（コード側は完成済み・ここだけ人手作業）

`deploy-staff-worker.yml` を実行すると、`npx wrangler deploy` が以下で失敗します:

```
✘ [ERROR] A request to the Cloudflare API (/accounts/***/workers/services/kiraku-staff-ops) failed.
  Authentication error [code: 10000]
```

**確認済みの事実**: 同じ`CLOUDFLARE_API_TOKEN`で**既存の`kiraku-bi` Worker自体の再デプロイ**
（新規作成ではなく、既にある同じスクリプトへの`wrangler deploy`）を試しても、まったく同じ
`Authentication error [code: 10000]`で失敗します（`.github/workflows/deploy-worker.yml`の実行、
`kiraku-staff-ops`とは無関係の既存パイプライン。これは私の今回の変更が原因ではなく、この
変更に着手する前の最後のmainコミット（`b6b0f0e`）時点で既に壊れていました）。

**つまり現在のCLOUDFLARE_API_TOKENに必要な権限は次の3つで、全て確認・整理済みです:**

| 権限 | 現状 | 用途 |
|---|---|---|
| Account → Workers Scripts → Edit | **無し（要追加）** | Worker自体のデプロイ（kiraku-bi再デプロイ・kiraku-staff-ops新規デプロイ双方） |
| Account → Workers R2 Storage → Edit | 有り（動作確認済み） | R2バケット作成・データpublish |
| Account → Workers KV Storage → Edit | **無し（要追加）** | 清掃指示override機能用のKVネームスペース作成（任意機能ではなく必須要件） |

R2は生きているため今まで気づかれていませんでしたが、**既存の経営BI（kiraku-bi）自身の
コードデプロイ経路も現在この権限不足により壊れています**（データ更新=R2書き込みは
15分毎に動き続けているため気づかれていなかったと考えられます）。

**これはコード側の問題ではありません。** `cloudflare/staff-ops/` のコード・テスト・ローカル
`wrangler dev` 動作確認・feature flag（清掃visual非公開化）はすべて完了・green です。
上記2つの権限を追加するだけで、staff-ops・既存bi-web両方のデプロイが自動で成功するように
なります。**ユーザーへtoken値そのものを要求することはありません** — ダッシュボードでの
権限追加だけをお願いします。

## 対処方法（どちらか）

**方法A（推奨）**: 既存トークンを編集し、不足している2権限を追加する
1. https://dash.cloudflare.com/profile/api-tokens → 該当トークンの **Edit**
2. Permissions に以下を追加（既に付いているものはそのまま）:
   - **Account → Workers Scripts → Edit**
   - **Account → Workers KV Storage → Edit**
3. Account Resources の対象が「Specific Workers」で `kiraku-bi` だけに限定されている場合、
   `kiraku-staff-ops` も追加するか、「All Workers」に変更する（新しいWorkerを作成するため）。

**方法B**: 新しいトークンを発行し直し、GitHub Secretsを更新する
1. https://dash.cloudflare.com/profile/api-tokens → **Create Token** →
   **Edit Cloudflare Workers** テンプレート（通常 Workers Scripts / KV / R2 の Edit をまとめて含む）
2. 発行されたトークンを GitHub → Settings → Secrets and variables → Actions →
   `CLOUDFLARE_API_TOKEN` → **Update** に貼り付け。

## 修正後、明示的にデプロイを実行して確認する（push待ちにしない）

```bash
gh workflow run deploy-staff-worker.yml -R ginisato-hash/kiraku
gh workflow run deploy-worker.yml -R ginisato-hash/kiraku
```
両方が green になるまで `gh run list -R ginisato-hash/kiraku` で確認してください。
成功すれば `https://kiraku-staff-ops.s-sato-dce.workers.dev/health` が `{"status":"ok"}` を返します。
**このタイミングではまだ `STAFF_OPS_ACCESS_CONFIRMED` を設定しないでください**
（下記「データゲート」参照。Access確認が先です）。

---

# Cloudflare Access 設定手順（Worker deploy成功後・PII投入前に必須）

`kiraku-staff-ops` Worker は宿泊者氏名・電話番号・住所を配信するため、**Cloudflare Access
（Zero Trust）でアクセス制御されるまで、URLを知っている誰でも閲覧できる状態です。**
これは本リポジトリのコード・GitHub Actions・wrangler設定だけでは自動化できません
（Cloudflare Zero Trust の Access Application 作成にはダッシュボード操作、または
`Access: Apps and Policies: Edit` 権限を持つ別のAPIトークンが必要です）。

**本番URLを一般スタッフへ周知する前に、必ず以下を完了してください。**

## 1. 対象ホスト名

```
https://kiraku-staff-ops.s-sato-dce.workers.dev
```
Cloudflare Access は `*.workers.dev` サブドメインもそのまま保護対象にできます（DNS/カスタムドメイン不要）。

## 2. Access Application 作成手順

1. https://one.dash.cloudflare.com/ にログイン → 対象アカウントを選択。
2. 左メニュー **Access** → **Applications** → **Add an application** → **Self-hosted**。
3. 設定:
   - Application name: `Kiraku Staff Ops`
   - Session duration: `24 hours`
   - Application domain: `kiraku-staff-ops.s-sato-dce.workers.dev`（パスは空欄 = サイト全体を保護）
4. **Policies** で最低1つ追加。喜らくの実際の運用に応じて以下のいずれかを選択:
   - フロント担当者にメールアドレスがある場合: `Include` ルールで `Emails ending in` `@yuge-zao.com`
   - 個別メールアドレスが無いスタッフがいる場合: 個人メールを明示列挙するか、
     Cloudflare Access の **One-time PIN**（メール受信のみで認証）で許可リストを作る
   - 経営BI（`kiraku-bi`）閲覧権限を持つ人物と、Staff Ops/清掃担当者に許可する人物は
     **意図的に別リストにしてください**（清掃担当者に経営BI閲覧権限を与えない）
5. 保存。

## 3. 動作確認（実URLで未認証アクセスを試す — Applicationがあるだけでは不十分）

シークレットウィンドウ等、認証情報の無い状態で以下を確認してください:
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://kiraku-staff-ops.s-sato-dce.workers.dev/
curl -s -o /dev/null -w "%{http_code}\n" https://kiraku-staff-ops.s-sato-dce.workers.dev/api/daily-ops?date=2026-08-30
```
両方とも **200でHTML/JSONがそのまま返ってはいけません**。Cloudflare Accessのログイン
ページへのリダイレクト（302等）またはチャレンジページが返ることを確認してください。
ブラウザでも同様に、未認証状態でアクセスするとログイン画面（メール→PIN or SSO）が
表示されることを確認し、許可リストに無いメールアドレスでは拒否されることも確認してください。

## 4. 清掃担当者向けURLも同じApplicationで保護されます

`/`, `/ops/print/guest-register`, `/ops/print/cleaning`, `/cleaning/today`, `/api/*` は
同一ホスト名なので、1つのApplicationで全て保護されます。

---

# R2バケットの公開設定について（自動チェック済み）

`deploy-staff-worker.yml` に「Ensure staff-ops R2 bucket has NO public access (r2.dev disabled)」
ステップを追加済みです。デプロイの度に `wrangler r2 bucket dev-url disable` を実行し、
念のため `dev-url get` で状態を確認、公開URLが有効なままなら **デプロイ自体を失敗させます**
（`STAFF_OPS_ACCESS_CONFIRMED`を開く前にこのステップがgreenであることを確認してください）。
これによりWorkerのbinding経由でしかsnapshotへ到達できない状態を保証します（Cloudflare Access
はWorkerの前段だけを守るため、R2自体がpublicだと迂回されてしまいます）。

---

# データゲートを開く（Access確認 **後** の最終ステップ・これをやるまで実データは配信されない）

Worker deployのgreen化・Access動作確認まで完了したら:

1. https://github.com/ginisato-hash/kiraku → **Settings** → **Secrets and variables** → **Actions**
   → **Variables** タブ → **New repository variable**
2. Name: `STAFF_OPS_ACCESS_CONFIRMED` / Value: `1`
3. **repository variableを設定しただけでは既存のworkflowは自動実行されません。** 次の15分の
   Cloudflare Cronを待たず、明示的にkickしてください:
   ```bash
   gh workflow run refresh-bi-r2.yml -R ginisato-hash/kiraku
   ```
4. 完了後、`kiraku-staff-ops-data` R2バケットの `latest/staff_ops_snapshot.json` に
   実データが書き込まれたことを確認する（構造のみ確認。実在氏名/電話/住所を
   ターミナル/ログへ大量出力しない）。

これらを行うまでは、Worker自体はデプロイ済みでもR2バケットが空のため、すべての日付が
404/空状態を返すだけで、実在の宿泊者氏名・電話番号・住所は一切公開されません。

---

# 清掃指示書の状態（原本写真到着まで）

- Cleaning infrastructure: **READY**（データモデル・分類ロジック・override機構・print/mobile
  route・テストはすべて実装済み・green）
- Cleaning visual reproduction: **WAITING FOR SOURCE IMAGE**（原本写真未提供）
- Cleaning feature flag: **OFF**（`public/featureFlags.js` の `CLEANING_VISUAL_READY = false`。
  Daily Ops上の「清掃指示書を表示/印刷」ボタンは無効化され「準備中」と表示、
  `/ops/print/cleaning` と `/cleaning/today` も通常アクセスでは「準備中」を表示。
  内部QA用に `?preview=1` を付けると仮visualを直接確認できる — スタッフへは案内しないこと）

原本写真到着後、`cleaningSheetTemplate.js` を写真ベースで100%再現し、acceptance通過後に
`CLEANING_VISUAL_READY` を `true` に切り替えます。

---

## なぜ完全自動化しなかったか

- Cloudflare APIトークンの権限追加はダッシュボード操作（またはトークン再発行）が必要で、
  既存トークンの実際のスコープ・作成経緯が確認できないため、コード側からは変更できません。
- Access Policyの内容（メールドメインか個別メール列挙か）は、清掃担当者が実際にメール
  アドレスを持っているかという運用実態に依存し、コードからは判断できません。
- 誤ったスコープのトークンでAccess Policyを自動作成すると、想定と異なるアクセス制御
  （過剰に緩い/厳しい）が無人で本番反映されるリスクがあるため、この一点だけは
  人手での確認を必須にしています。
