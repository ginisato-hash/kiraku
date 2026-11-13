# 喜らく：MF接続済みの2銀行を読む

きらやか銀行・山形銀行について、既存のMF銀行連携を利用する。銀行の認証情報は喜らく側へ複製しない。
MF APIへの会計リクエストはGETだけで、APIキー交換のみPOSTする。

## データの境界

- MF事業者番号と、2口座の連携サービスID・配下口座IDを固定する。銀行名の部分一致で口座を選ばない。
- GMO、他事業者、他口座、クレジットカード、現金サービスは今回の対象に含めない。
- `data/private/moneyforward/ledger.sqlite` は専用DB。会計用の `bank_transactions` / `journal_entries`、既存BIの `bank_actual_transactions`、Excelを更新しない。
- CSVとの共通計上はまだ行わない。移行照合が済むまで、既存CSV集計にMF明細を加算しない。
- 入出金は資金移動であり、売上・利益の表示に転用しない。借入、返済、口座間振替も入出金に含まれる。
- APIは銀行実残高・銀行側最終同期時刻を返さない。`balance_jpy = null` を保持し、0円や入出金差額で補わない。
- 全仕訳状態を取得する。MFの「対象外」は記帳上の状態であり、銀行で資金移動が無かったことを意味しない。

## 最初の接続設定

必要なMF設定は3つだけ。APIキーは発行元の利用者権限を引き継ぐため、この実装がGET専用であることと、キー自身の権限は別である。MF側で利用可能なら閲覧に絞った利用者・権限を用いる。

| 設定 | 保存先 | 内容 |
|---|---|---|
| `MF_API_KEY` | ローカルのGit管理外 `.env`、本番はGitHub Actions Secret | MFアプリポータルのAPIキー |
| `MF_OFFICE_CODE` | 同上 | 喜らく対象事業者の `0000-0000` 形式の番号 |
| `MF_BANK_ACCOUNTS_JSON` | 同上 | 下記の2口座ID対応 |

APIキーや口座一覧を、PR、Issue、チャット、workflow入力、ジョブログへ貼らない。APIキーを会計APIへ直接送らず、`/auth/exchange` で取得した短期JWTを使う。JWTはメモリ内にだけ保持する。

1. MFで対象事業者と、既存のきらやか・山銀の銀行連携を確認する。
2. APIキーと事業者番号を非公開の環境設定へ保存する。
3. 下記の読取コマンドで口座一覧をローカルの非公開ファイルへ保存する。IDや口座番号を標準出力へ出さない。

```bash
yuge-finance discover-mf-banks
```

`data/private/moneyforward/discovery.json` の対象事業者を確認し、MF画面の2口座と照合する。配下口座の `id` を使う。勘定科目の `account_id` や `sub_account_id` を銀行口座IDとして設定しない。

`MF_BANK_ACCOUNTS_JSON` の形式（下記のIDは置き換えるための架空値）:

```json
{
  "kirayaka": {
    "connected_account_id": "REPLACE_KIRAYAKA_SERVICE_ID",
    "connected_sub_account_id": "REPLACE_KIRAYAKA_BANK_ACCOUNT_ID"
  },
  "yamagata": {
    "connected_account_id": "REPLACE_YAMAGATA_SERVICE_ID",
    "connected_sub_account_id": "REPLACE_YAMAGATA_BANK_ACCOUNT_ID"
  }
}
```

通常の銀行口座では子IDを明示する。MFが配下口座を持たず親サービスに直接明細を紐付ける場合に限り、子IDをJSONの `null` にできる。配下口座が存在する親サービス全体の指定は拒否する。

## 同期と確認

```bash
# 初回は現在のMF会計期間の開始日からJST本日まで、2口座を取得
yuge-finance sync-mf-banks

# 通常は直近90日を重ねて読み直す
yuge-finance sync-mf-banks

# 現在会計期間全体を再照合
yuge-finance sync-mf-banks --full

# 過去期間も明示して再照合できる（将来日不可）
yuge-finance sync-mf-banks --start-date 2026-06-01 --end-date 2026-10-31
```

初回はMF画面と口座別・対象期間別の件数と入出金合計を照合する。再実行して件数が増えないことを確認する。APIの取得可能期間は接続済みサービスの収録状況によるため、API全ページ取得の成功を銀行の全履歴収録済みと表現しない。

2口座・全ページが検証を通った時だけDBを更新する。主キーは事業者番号とMF明細ID。再取得はupsertし、修正前のJSONも `transaction_versions` に残す。同一日・同一金額・同一摘要でもIDが異なる正当な取引を潰さない。

ページング途中の失敗では明細・前回成功日時を維持する。取得済みの行が同じ期間の再照合で見つからない場合は、履歴を残して要照合とし、現在の入出金集計から外す。画面には要照合件数を表示する。APIに更新日時がないため、通常の90日再取得に加え、毎日会計期間全体を再照合する。過年度の修正は対象期間を明示して再取得する。

## 非公開R2と定期実行

専用bucketは `kiraku-finance-private`。既存の公開BI用bucketへは書かない。

- `.github/workflows/sync-mf-banks.yml` が毎時起動。必要Secret未設定時は同期しない。
- JST03時台は現在会計期間を全件再照合。それ以外は直近90日。
- 毎回、非公開R2から専用DBを復元する。新規checkoutでも過去履歴を失わない。
- stateを世代別に保存した後、`bank-sync/latest/summary.json` を最後に置き換える。summary内の `storage.state_key` / `storage.state_sha256` が復元対象を確定する。
- 復元元404だけを初回扱いにする。権限エラー、通信失敗、ハッシュ不一致を空データ扱いにしない。
- R2のmanaged public URLが無効、custom domainが未設定であることを入出力前に検査する。
- 同時実行は直列化。DB・生明細・口座一覧をActions artifactやcacheに保存しない。
- 取得した銀行明細・金額・口座ID・摘要はジョブログやjob summaryへ出力しない。

本番の初回はworkflowの `ensure-bucket` が専用bucketを確認し、存在しない場合だけ作成する。公開URLを有効化せず、既存の公開設定を変更しない。既存のCloudflareアカウントとR2 read/write権限の資格情報を使う。Guest OSの管理者用Workerへ専用R2 bindingを設定する。未設定時は未接続として動作し、既存の宿泊業務は継続する。

## 管理アプリの表示

Guest OSの `GET /staff/api/finance/banks` が既存の個人認証と `ADMIN` 権限を検証し、集計フィールドだけを返す。`FRONT_DESK`、一般スタッフ、未認証ユーザーには銀行データを返さない。MFキーをGuest OSへ置く必要はない。

表示対象は2口座の月別入金・出金・差引、取得対象期間、要照合件数、MFからの取得日時。会計月・口座をまたいで残高を混ぜない。取得不足月と取得済み0件を区別する。3時間以上取得できていなければ古い取得として示す。この鮮度は喜らくの取得処理についての値であり、MFから銀行への同期日時ではない。

## 運用上の判断

| 状態 | 動作 |
|---|---|
| MF権限・APIキー・対象事業者に問題 | 同期失敗。既存明細と前回成功日時を保持 |
| 口座ID設定が既存DBと変わった | 自動移行せず停止。誤った口座履歴の混合を防ぐ |
| MF明細の金額・状態変更 | 同じ明細IDを更新、変更前後の履歴保持 |
| 再取得で既存明細が見つからない | 削除せず要照合。現行集計からは除外 |
| 片口座だけ取得失敗 | 2口座とも今回分を反映せず、全体を取得失敗にする |
| R2復元・検証失敗 | MF同期・新しいsummary公開を行わない |
| 実残高が必要 | このAPIの入出金合計から作らず、別の残高ソースを接続する |

## 仕様の根拠

2026-11-13確認。実データでの接続完了は、設定・取得・照合が成功してから記録する。

- [MF会計V3 OpenAPI](https://developers.api-accounting.moneyforward.com/v3/openapi.yaml)
- [MF会計APIの利用案内](https://biz.moneyforward.com/support/account/guide/others/ot09.html)
- [APIキー認証](https://developers.biz.moneyforward.com/docs/common/api-keys/overview/)
- [Cloudflare R2 REST API](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/)
