# Legacy本番のCompose運用

Legacy本番は専用の `release/1.x` checkoutにある **`compose.yaml` と `.env` の一組**で管理します。
通常更新は、そのディレクトリで `docker compose build`、`docker compose up -d` の順に行います。
Static Webの公開手順は変更しません。

この文書は切替手順です。PRのマージや検証成功だけで、本番切替が承認されたことにはなりません。

## 正本と保持する設定

| 項目 | 本番の指定 |
| --- | --- |
| checkout | `/srv/compose/autoNavLog-legacy`、branch `release/1.x` |
| Compose / 環境ファイル | checkout直下の `compose.yaml` / `.env` |
| project / service | `autonavlog` / `autonavlog` |
| build元 / image | このcheckoutの `Dockerfile`、`autonavlog:legacy` |
| data volume | 既存の `autonavlog_autonavlog-data`、external、`/var/lib/autonavlog`へRW mount |
| 公開bind | `127.0.0.1:8123` → container `8000` |
| trusted-local / secure cookie | 空 / `true` |
| 認証と移行先 | 稼働中のAccess Team/AUD・Firebase 4項目を維持、NavMateは `https://navmate.yuto24.com/` |

`.env.legacy.example` は値を入れるためのテンプレートです。本番では `.env` だけを読み込みます。
`COMPOSE_FILE=compose.yaml` を明記し、WSL・開発用overrideを混ぜません。環境設定はGit管理せず、
権限を600にします。テンプレートの空欄を推測値や新しい認証設定で埋めないでください。
実値は既存の稼働設定から、画面・ログ・PRに出さず移します。dotenvで `$` などの文字を
解釈されないよう必要な値は単一引用符で囲み、移した結果を下記の検証で比較します。

`AUTONAVLOG_DATA_EXTERNAL=true` により、既存volumeがなければ起動は失敗します。
空の代替volumeを自動作成して起動を成功させてはいけません。開発・Codexの既定値はexternalではなく、
従来どおりprojectごとに分離したvolumeを使います。

## 最初の切替（別途、本番実行の承認が必要）

1. 稼働containerのImage ID、実際のsource commit、認証設定・bind・mountの一致を確認します。
   Imageラベルが後続の画面差替えを含まない場合もあるため、deployment manifestも照合します。
   設定全体・秘密値・Project内容を出力しません。
2. dirtyな旧 `/srv/compose/autoNavLog` はそのまま残します。新しい専用checkout
   `/srv/compose/autoNavLog-legacy` を `release/1.x` から用意し、レビュー・CI済みのcommitに合わせます。
   切替対象には移行案内の #335 と、この運用整理の両方が含まれていることを確認します。
   旧checkoutのswitch/reset、データコピー、registryへのpushは不要です。
3. 新checkoutだけに `.env` を用意します。稼働設定を基準にしてテンプレートの固定項目を合わせます。
   旧 `.env.prod` は採用しません。2026-11-01の読み取り確認では、そこにあるNavMateホストが
   稼働設定と異なっていました。旧ファイルと復旧記録は削除・書換しません。
4. 通常コマンドと同じshellで、承認済みの完全なcommit SHAを指定します。

   ```sh
   cd /srv/compose/autoNavLog-legacy
   python3 scripts/check_legacy_compose.py --expected-commit <承認済みの40桁SHA>
   ```

   検証は読み取り専用です。branch/commit/追跡ファイルと未追跡ファイルの変更、`.env`の権限、通常Composeと
   一組の明示解決結果の一致、既存volume、稼働中の認証・bind・mountを確認します。
   shell由来の上書きや別Composeとの差があれば停止します。エラー時も値やDockerの生出力は表示しません。
   成功はFirebaseのauthorized domains・Google provider・Rulesや移行成功を保証しません。
5. 旧稼働を続けたまま `docker compose build` で対象をbuildします。build成功時のImage IDと
   source commitを記録します。固定した旧imageとprivate Composeは保持します。
6. 切替時間を確保し、全利用者の計算が完了したことを確認します。既存運用に従い対象serviceだけを
   正常停止し、停止済みvolume全体をバックアップしてSHA-256を照合します。Project数・所有者対応・
   last-good・移行DBの整合性は内容を表示せず比較し、作業中に新しい書込みが入らないようにします。
   30秒で処理を切らないよう、必要な停止は `docker stop --time=-1 autonavlog-autonavlog-1` を使います。
7. 新checkoutで `docker compose up -d`。同じproject/service・同じ既存volumeを引き継ぎます。
   起動後、health・Image ID・commit記録・bind・mount・認証一致と、バックアップ時のデータ保全結果を確認します。
   移行案内の表示と従来画面へ進めることも確認します。実Google認証や移行の受入は別途許可された範囲で行います。
8. 新しい通常経路を運用上の正本に指定します。旧checkoutの通常Composeや `.env.prod` を
   起動に使わないことを記録します。旧private固定構成は切り戻し専用として保管します。

## 切替後の通常更新

承認済み `release/1.x` commitへfast-forwardし、上記の読み取り専用検証を通します。
稼働計算がない更新時間を確保し、既存のバックアップ・停止手順を守った上で実行します。

```sh
cd /srv/compose/autoNavLog-legacy
docker compose build
docker compose up -d
```

shellの一時的な環境変数や `-f` / `--env-file` を追加しません。`build`が失敗したら`up`に進みません。
更新後はhealthだけでなく、稼働Image IDが今回buildしたものか、既存volumeと認証が同じかも確認します。
`down -v`・volume削除・旧backupの上書き復元は通常更新に含みません。

## 切り戻し

`/srv/compose/autoNavLog/.autonavlog-data/deployments/legacy-wait-20261101/` の
`start-current.sh`、`compose.current.private.json`、固定Image ID、manifestは残します。
これは互換性のあるv1.23.0修正版の復旧経路です。一般的な `autonavlog:local` タグで代用しません。

切り戻しが別途承認されたら、実行中の計算と書込みを止め、現在のデータを保全し、旧版との互換性を確認してから
保存済みスクリプトを使います。同じvolumeを維持し、古いbackupで切替後の書込みを消しません。
データをv1.21.0へ戻してはいけません。新しい通常checkoutの `.env` は書き換えず、
切り戻し後はどちらのImage IDが動いているかを記録します。

## 分岐した理由

2026-11-01の復旧記録では、古いcheckoutから再作成されたv1.21.0をv1.23.0へ戻し、
PR #332の互換修正と経過時間表示の削除を適用しています。再び古い可変タグで起動しないよう、
Image ID・認証・volume・bindをprivate Composeに固定し、通常Compose/.envは変更せず残していました。
今回、その復旧経路を保管したまま、以後のbuild元と通常起動の一組を明確にします。
