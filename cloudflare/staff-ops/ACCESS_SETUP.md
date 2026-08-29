# Cloudflare Access 設定手順（手動・必須・未実施）

`kiraku-staff-ops` Worker は宿泊者氏名・電話番号・住所を配信するため、**Cloudflare Access
（Zero Trust）でアクセス制御されるまで、URLを知っている誰でも閲覧できる状態です。**
これは本リポジトリのコード・GitHub Actions・wrangler設定だけでは自動化できません
（Cloudflare Zero Trust の Access Application 作成にはダッシュボード操作、または
`Access: Apps and Policies: Edit` 権限を持つ別のAPIトークンが必要で、既存の
`CLOUDFLARE_API_TOKEN`（Workers/R2/KV操作用）はこの権限を持っていません）。

**本番URLを一般スタッフへ周知する前に、必ず以下を完了してください。**

## 1. 対象ホスト名

Worker名 `kiraku-staff-ops` は、既存 `kiraku-bi`（`https://kiraku-bi.s-sato-dce.workers.dev`）と
同じアカウントのため、以下のURLで公開されます（カスタムドメイン未設定の場合）:

```
https://kiraku-staff-ops.s-sato-dce.workers.dev
```

Cloudflare Access は `*.workers.dev` サブドメインもそのまま保護対象にできます
（DNS/カスタムドメインの追加設定は不要）。

## 2. Access Application 作成手順（Cloudflare ダッシュボード）

1. https://one.dash.cloudflare.com/ にログイン → 対象アカウントを選択。
2. 左メニュー **Access** → **Applications** → **Add an application** → **Self-hosted**。
3. 設定:
   - Application name: `Kiraku Staff Ops`
   - Session duration: `24 hours`（推奨。頻繁な再ログインを避けつつ、端末紛失時のリスクを抑える）
   - Application domain: `kiraku-staff-ops.s-sato-dce.workers.dev`（パスは空欄 = サイト全体を保護）
4. **Policies** で最低1つ追加。喜らくの実際の運用に応じて以下のいずれかを選択:
   - **フロント担当者にメールアドレスがある場合**: `Include` ルールで `Emails ending in`
     `@yuge-zao.com`（または実際に使っているドメイン）。
   - **清掃担当者など個別メールアドレスが無いスタッフがいる場合**: 個人Gmail等の
     `Emails` を明示的に列挙するか、Cloudflare Access の **One-time PIN**
     （メール受信のみで認証、アカウント登録不要）を使い、許可するメールアドレスの
     リストだけをPolicyに列挙する運用が最も簡単です。
   - 経営BI（`kiraku-bi`）の閲覧権限を持つ人物と、Staff Ops / 清掃担当者に許可する
     人物は**意図的に別リストにしてください**（本タスクの要件：清掃担当者に経営BI閲覧権限を
     与えない。逆にstaff-ops側のPolicyに経営層のみのメールを含めるのは問題ありません）。
5. 保存。

## 3. 動作確認

Policy保存後、シークレットウィンドウ等で対象URLへアクセスし、Cloudflare Access の
ログイン画面（メール入力→PINコード認証、またはSSO）が表示されることを確認してください。
許可リストに無いメールアドレスでは拒否されることも確認してください。

## 4. 清掃担当者向けURLも同じApplicationで保護されます

`kiraku-staff-ops` Worker配下のパス（`/`, `/ops/print/guest-register`, `/ops/print/cleaning`,
`/cleaning/today`, `/api/*`）は同一ホスト名なので、上記1つのApplicationで全て保護されます。
清掃担当者だけ別の狭いPolicyにしたい場合は、Application domainのパスを分けて
（例: `kiraku-staff-ops.s-sato-dce.workers.dev/cleaning*` を別Applicationにする）
個別のPolicyを設定することも可能です（任意・今回は必須ではありません）。

## 5. データゲートを開く（Access確認後の最終ステップ・これをやるまで実データは配信されない）

上記1〜4を完了し、動作確認まで済んだら、**GitHub リポジトリ設定**で以下を追加してください:

1. https://github.com/ginisato-hash/kiraku → **Settings** → **Secrets and variables** → **Actions**
   → **Variables** タブ → **New repository variable**
2. Name: `STAFF_OPS_ACCESS_CONFIRMED` / Value: `1`

これを設定するまでは、`refresh-bi-r2.yml`（15分毎の既存データ更新ジョブ）は
Daily Ops/清掃データのexport・R2publishを**意図的にスキップ**します
（`kiraku-staff-ops` Worker自体は既にデプロイされ動作しますが、R2バケットが空のため
すべての日付が404/空状態として返るだけで、実在の宿泊者氏名・電話番号・住所は
一切公開されません）。**この変数を設定して初めて実データが配信され始めます。**

## 6. 完了後にやること

このファイルの手順が完了したら、ユーザーへ次を報告してください:
- Access Application作成日時
- 設定した許可Policy（メールドメイン/個別メールのどちらか、実際の値は本ファイルに書かない）
- 動作確認結果（未許可メールで拒否されたか）

## なぜ自動化しなかったか

- 適切なPolicy（メールドメインか個別メール列挙か）は、清掃担当者が実際に
  メールアドレスを持っているかという運用実態に依存し、コードからは判断できません。
- 既存 `CLOUDFLARE_API_TOKEN`（GitHub Secrets）はWorkers/R2/KV操作用に発行されたもので、
  Zero Trust Access の管理権限を含んでいる保証がありません。誤ったスコープのトークンで
  Access Policyを自動作成すると、想定と異なるアクセス制御（過剰に緩い/厳しい）が
  無人で本番反映されるリスクがあるため、この一点だけは人手での確認を必須にしています。
