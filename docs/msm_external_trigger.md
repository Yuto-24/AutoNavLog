# MSM更新の外部起動補完 (#287)

この変更は運用toolingのみ（`Release: not-required`）。アプリのVERSION、計算、
Firestore Rules、公開済みアプリを変更しない。mainにあるv2の公開承認でもない。
外部の登録・Secret作成・配置・Cron・実dispatchは、このPRの検証では行わない。

## 責務と起動条件

`services/msm-trigger` の専用Workerは15分間隔（UTC `*/15 * * * *`）。
GitHub schedule（毎時23分）を優先し、以下の条件で既存 `static-production.yml` を
`main` / `feed_refresh=true` / `publish=true` / `observed_run=<Run>` で起動する。
source SHAを渡す経路はない。

1. canonical catalogとreleaseを取得し、6時間寿命、Run時刻、catalogの実bytes/SHA-256、
   全assetのrelease inventoryとの一致を確認。期限切れも検査できるが正常とはしない。
2. GitHubのqueued/in_progress/waiting/pending/requestedをそれぞれ取得。
   作成時刻で限定せず、前日以前の承認待ちも止める。直近24時間の履歴も確認。
   未知・切り詰め・取得不能・認証失敗は停止する（自動POST再試行なし）。
3. 京都大学の当日と前日のlistingから広告されているMSM Runを確認。
   Workerは完全性を判定せず、予報ファイルを取得・変換しない。
   空listing、404、timeout、不正時刻は「更新不要」にしない。
4. 新しい広告Runがあり、catalog残存が3時間以下、実行中なし、90分cooldown経過、
   UTC日8要求未満、同じ広告Runへの要求2回未満ならdispatch。
   `observe`では `WOULD_DISPATCH` を記録するだけ。

WorkerはKVを各tickで1readし、dispatch直前／公開確認時だけ1writeする。
POSTの応答を失っても先に保存した試行枠を消費し、直ちに再送しない。
KVは結果整合性であり、同時tickが2つのPOSTを発行する可能性は残る。
GitHub履歴もcooldown・回数判定に使用し、GitHubの共有concurrency取得後に
`external_trigger_gate.py` が同じ上限を独立して再確認する。外部runのrerunは拒否。
上限は正常運用の要求・重い処理の制限であり、分散KVでPOST数の厳密な上限を保証しない。
悪意あるActions:write credentialの乱用を防ぐ権限境界でもない。

すべてのfeed renewal（schedule・外部・従来の手動dispatch・build-only）が
`preflight_feed.py` で**25時間分の新しい完全Run**を検査する。
NO_NEW_RUNは健康なcatalogと完全な既存Runが確認できた場合だけ。
外部Run文字列は会計用のヒントであり、取得Runを強制するものではない。
既存restore・生成後の新Run検査・coverage/hash/bytes・承認済みSHAと公開設定・
独立build/E2E・公開前のPages検査を維持し、upload直前にもcanonicalを再取得して
候補の最新Runが厳密に新しいことを検査する。同じ予報の生成日時・期限だけは更新しない。
concurrency `static-production` / `cancel-in-progress: false` は既存公開と共有。

外部dispatchには `STATIC_AUTOMATION_ENABLED=true` に加えて
`STATIC_EXTERNAL_TRIGGER_ENABLED=true` が必要（未設定は無効）。
外部dispatchは既存 `static-publication` environmentの承認を維持する。
承認待ちは次のdispatchを止める。承認保護が設定されていれば人による承認が必要で、
このPRは無人化を理由にそのgateを変更しない。

## 受付・公開・監視の区別

- `DISPATCH_ACCEPTED`: HTTP受付のみ。GitHubが成功でも公開確認の代用にしない。
- `AWAITING_PUBLICATION`: 受付不明／受付済みのRunを待機。
- `publication: FAILED`: 対応するGitHub実行が失敗。cooldown後の再試行も上限内だけ。
- `PUBLICATION_OBSERVED`: canonicalのRunが前回より新しく、要求Run以上、有効期限内、
  同じアプリcommit、catalog実bytes/hashとrelease/asset inventoryが一致。
  scheduleが同じ更新を先に完了した場合も復旧観測であり、Workerの因果的成功とはしない。
- `UNKNOWN`/`PUBLICATION_UNCONFIRMED`/`RUN_LIMIT`/`DAILY_LIMIT`: 成功扱いしない。

WorkerはNPZ本体をダウンロード・decodeしない。既存workflowのcanonical検証と
`operations.py monitor`が全payload検証を担う。Workerの観測だけで完全な公開受入とはしない。
取得不能や公開途中のcatalog/release不一致は失敗し、次tickで再観測する。

#273は独立した天気データ障害／復旧の通知・重複抑制を担当する。
このWorkerは利用者への天気通知を送らず、構造化logと任意のliveness heartbeatだけ。
`HEARTBEAT_URL`をSecretとして設定すると、チェック完了時だけHealthchecksへpingする。
これは「Workerが検査を完了した」意味で、dispatchや天気更新成功の通知ではない。
失敗時はpingせず、同じCloudflare全体の停止は外部heartbeat欠落で検出する。
サービス登録は行わない。[Healthchecks Free](https://healthchecks.io/)は20check、
クレジットカード不要と確認した。既存check数と通知先の利用条件は未確認。
導入する場合は別途承認後に15分周期＋十分なgraceで1checkを登録し、
通知先と欠落・復旧通知を試験する。天気の障害をこちらにも登録して二重通知にしない。

## CLI（通常設定のコピーは不要）

リポジトリrootから実行。Wranglerとlockfileを固定し、Worker名、公開先、GitHub repo/workflow、
閾値はコード管理。account/KV ID・段階の記録はignored `.state/` に自動保存する。
リポジトリ内のtemplateは常にobserve・Cronなし・ダミーKVで、本番deployに直接使わない。

認証不要の開発・検証:

```sh
npm --prefix services/msm-trigger ci
npm --prefix services/msm-trigger run typecheck
npm --prefix services/msm-trigger run check
npm --prefix services/msm-trigger test
npm --prefix services/msm-trigger run ops -- plan
```

以下は**個別承認後のみ**。既存Cloudflare accountを使い、有料planへの変更はしない。
初回だけ承認済み `CLOUDFLARE_ACCOUNT_ID` と `CLOUDFLARE_API_TOKEN` を安全なshell環境に用意する。
CLIはtoken内容を記録しない。`set -x`、debug HTTP logging、tokenを引数にする操作は使わない。

```sh
# 全namespaceを列挙して固定名の既存KVを再利用。欠落時だけ作成。再実行可能。
npm --prefix services/msm-trigger run ops -- ensure-kv
# 初回配置。observe、Cronなし、公開HTTP入口なし。
npm --prefix services/msm-trigger run ops -- deploy-observe
# 隠しpromptへSecretを入力。値をコマンド履歴やGitに残さない。
npm --prefix services/msm-trigger run ops -- stage-secret GITHUB_TOKEN
# 新version IDは自動保存。Secret登録とは別に、そのversionを100%へ配置。
npm --prefix services/msm-trigger run ops -- deploy-staged
# 任意heartbeatも同じ2段階。登録承認がなければ省略。
# npm --prefix services/msm-trigger run ops -- stage-secret HEARTBEAT_URL
# npm --prefix services/msm-trigger run ops -- deploy-staged
# observeのままCronを有効化する段階（これもCron承認が必要）。
npm --prefix services/msm-trigger run ops -- observe-cron
```

`wrangler secret put/delete` は新versionを**即時deploy**するため使わない。
上記は `wrangler versions secret put` で未配置versionだけを作る。
Secret登録時はobserve・Cron停止を必須にし、次のSecret登録前にpending versionを配置する。
同時管理者によるversion変更が検出された場合は停止し、version一覧で確認する。
同じ名前のSecret更新は上書きで、追加リソースを作らない。期限前の更新も同じ手順。

本番dispatchの明示承認と、後述の有効化前確認後:

```sh
# 既存のGitHub認証を使用する。credentialの新規作成を兼ねない。
gh variable set STATIC_EXTERNAL_TRIGGER_ENABLED --repo Yuto-24/AutoNavLog --body true
# 配置するがCronは空。observe-cron稼働中でも、ここで一度Cronを止める。
npm --prefix services/msm-trigger run ops -- enable-dispatch
# 別段階で15分Cronを配置。
npm --prefix services/msm-trigger run ops -- enable-cron
```

停止（既存のGitHub scheduleはそのまま）:

```sh
gh variable set STATIC_EXTERNAL_TRIGGER_ENABLED --repo Yuto-24/AutoNavLog --body false
npm --prefix services/msm-trigger run ops -- stop
```

Cronの追加・変更・削除は伝播に最大15分かかりうる。
停止は進行中の公開を中断しない。緊急時はGitHub側opt-inを先に閉じる。
停止後もKVを保持し、失敗上限を自動リセットしない。KV削除で再試行枠を作らない。
同じRunで2回失敗した場合は上流／GitHub／公開ログを調べ、通常は次の新Runを待つ。
namespace作成応答を失った場合も `ensure-kv` を再実行すればinventoryから再利用する。
`.state`を失った場合はensure-kvから再構成し、observe配置で安全な状態を確立する。

pendingの間に別途承認されたアプリ更新が行われた場合は`APP_CHANGED`で停止する。
旧要求を成功にしないため、自動で基準commitを変えない。復旧の明示承認後、GitHub側opt-inを
閉じて`stop`し、Cron停止の伝播と進行中実行の終了を確認してから次を実行する。

```sh
npm --prefix services/msm-trigger run ops -- abandon-pending
```

古いpending観測だけを除去し、日次・同Runの試行数とcooldownを保持する（再実行可能）。
KVの結果整合性によるread-back遅延を考慮して少なくとも60秒待ち、`observe-cron`で再確認し、
新しい承認済みアプリSHAとcanonical一致を確認した後に、有効化の各段階へ進む。
これは過去の試行を成功に変更する操作でも、同Run2回上限を解除する操作でもない。

## 権限・Secretの承認対象

| 対象 | 最小権限と制約 |
| --- | --- |
| Workerの `GITHUB_TOKEN` | fine-grained PATをYuto-24/AutoNavLogだけに限定、Actions:writeと必須Metadata:read。期限あり。workflow単体への権限限定ではない。他workflow起動・Actions操作も可能な権限であることを承認する |
| Cloudflare管理CLI | 既存account限定のWorkers Scripts:Edit、Workers KV Storage:Edit（inventory/作成）と必要なAccount Settings:Read。アカウント条件によりAPI適合を確認。Worker runtimeには渡さない |
| 既存Pages token | GitHubの既存公開environmentのみ。Workerへ渡さず、新規発行しない |
| `HEARTBEAT_URL` | 任意、Healthchecks ping URLそのものをSecret。閲覧URLやtokenをlogへ出さない。別サービス登録の承認が必要 |

期限・更新担当をtoken作成時に決める。401/403は自動で別tokenへ切り替えず停止する。
漏洩・失効時はGitHub opt-inを閉じ、対象tokenを失効し、承認後に新tokenを同手順で登録。
GitHub App installation tokenを使う場合は有効期限が短いため外部発行機構が必要となり、
この実装はPAT更新を基本とする。長期App秘密鍵の新規管理を暗黙に追加しない。

## 無料枠見積もり（2026-10-25 JST公式資料確認）

| 資源 | 追加使用量・上限との比較 |
| --- | --- |
| Workers | 96 Cron/日、最大2,976/月（31日）。Freeは100,000 requests/日、CPU **10ms/Cron**、50 subrequests、Cron 5/account。既存TAF等と共有。通常10 HTTP subrequests/tick、dispatch/heartbeat込み最大12＋KV操作で50以下 |
| KV | 通常96 reads/日、writesはdispatch最大8＋公開確認最大8 = 16/日を想定。重複tick分は増える。Freeは100,000 reads/日、1,000 writes・delete・list/日、保存1GB。単一の小さな状態key、管理CLIの操作も合算 |
| Pages | 通常MSMは3時間ごと8Run/日、同Run再公開禁止でscheduleと補完を合わせ約248回/月。補完の重い試行上限8/日も最大248/月、これを別に248公開追加する計算ではない。失敗後のretryは実際の新Run公開に至った時だけ数える。アプリ公開等は別途加算 |
| Pages制限 | Freeの500 builds/月はGit buildの枠。Direct Uploadのdeployment数をそのままbuild数に置き換えない。既存仕様の20,000 files、25MiB/fileを維持。実accountの全project利用量・rate limitは未確認 |
| Actions | public repo標準ubuntu-latestは無料。最大8補完試行/日×45分=360 runner分/日が保守的上界。no-new-runや上限拒否は重いbuild前に終了。新artifact/upload/cacheを追加しないため、この公開workflowによる永続保存増分は0。CIのnpm cache・既存failure artifactsは別途共有利用量に加算 |
| 保存枠 | GitHub Freeのartifact/Packages共有枠500MB、cacheはrepo単位10GB。保持期間・所有者全体の現在/積算使用量は未確認で、無料実行分から保存無料を推定しない |

10msを実機保証するため、承認後のobserve CronでCloudflare CPU p50/p95/max、
CPU超過失敗、実catalog/listing/historyサイズを確認する。localhostのwall timeやNode CPU測定は
Cloudflare課金CPUの代用にならない。Workerはcatalog/release各1MiB、listing各256KiBで停止し、
未知・超過を更新成功にはしない。上限内に収まらなければdispatchを有効化せず設計を見直す。
実accountの共有残量・無料plan・支払/自動課金状態、provider側のDirect Upload制限も未検証。
無料枠の数値から既存利用量をゼロとは扱わない。#281の包括的quota収集完成は前提に追加しない。

公式資料:

- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Cron管理・伝播](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [KV pricing](https://developers.cloudflare.com/kv/platform/pricing/) / [整合性](https://developers.cloudflare.com/kv/concepts/how-kv-works/)
- [Pages limits](https://developers.cloudflare.com/pages/platform/limits/) / [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)
- [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [dispatch権限](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) / [実行状態API](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs)
- [Secret登録の副作用](https://developers.cloudflare.com/workers/configuration/secrets/)

## 有効化までの残作業と受入

1. PRのCI・独立レビュー後、別途merge承認。
2. 対象accountの無料plan/共有枠、必要な管理権限、既存環境保護と承認済みアプリSHAを読み取り確認。
3. 上記の権限・期限を説明してtoken作成/Secret登録の承認。登録済みcredentialを使える場合は再作成しない。
4. Worker observe配置・Cron監視段階を個別承認し、設定read-back、CPU/応答サイズ、
   upstream HTTPS到達、GitHub履歴API、KV、非dispatchを確認。任意heartbeatは別承認。
5. GitHub opt-in、dispatch mode、Cron有効化を個別承認して実施。必要なら既存environmentの承認操作。
6. schedule欠落を補完した実Run、catalog期限・全payload整合性・アプリcommit不変、
   旧期限を跨ぐ継続更新を記録し#123へ戻す。ここまではfixture試験では代用できない。

人手が必要なのは権限/Secret、公開承認とenvironment承認、初回のprovider確認。
通常の15分判定・要求・待機・Run確認に値やコマンドのコピペは不要。

## 取得元timeoutの別件提案

外部CronはGitHub schedule欠落を補うだけで、京都大学listing/GRIB通信のtimeoutは解決しない。
既存 `acquire_listings` は60秒socket timeout。調査時は失敗phase（listing/GRIB/decode）、
対象Run・ファイル種別/forecast span、HTTP status、開始/経過時間、試行数を秘密情報なしで記録する。
別件で取得GETだけ最大2回、短いjitter付きbackoff、総wall-clock deadline内のretryを検討する。
401/403、不正データ、hash不一致にはretryしない。dispatch POSTの自動retryと混ぜず、
全失敗時はUNKNOWN/FAILEDを維持する。旧サーバの停止やTTL延長は行わない。
