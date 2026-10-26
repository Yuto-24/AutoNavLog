# MSM補完起動: 軽量WorkerとActions検証

Related #287 / PR #290。アプリ本体、承認済みSHA、TTL、配信origin、TLSとアクセス保護は変更しない。
Workerは起動の候補を判定し、Actionsが公開可否と公開後の証拠を確認する。

## 鮮度ファイルと起動判断

公開toolingは承認済みアプリのbuild/test後、`weather/msm/freshness.json`を追加し、
`release.json`のinventoryへ実bytes/hashを登録する。catalogと同じPages deploymentに載せる。
アプリcheckoutのソースやcommitは変更しない。既存の承認SHAの古いbuild scriptでも移行できる。

schema 1は `run`（catalog assets中の実在する最新Run）、`generated_at`、`expires_at`、
`app_commit` を持つ。生成・期限はcatalogから転記し、6時間のTTLを更新・延長しない。
2,048 bytes以内で、架空のRunや「次に来るはずのRun」を記録しない。

Workerはこの1ファイルをno-store、HTTPS、redirect拒否、10秒、2,048 bytes上限で読む。
schema、UTCの3時間境界Run、生成日時、6時間の期限、commit形式を確認するだけ。
残り3時間超なら `FRESH` として終了し、上流は `NOT_CHECKED` のまま。
残り3時間以下（有効な古い/期限切れhintを含む）の場合、KVの要求回数・90分間隔と
GitHubのactive 5状態（古い承認待ちを含む）・直近24時間履歴で要求を抑制する。
上限8要求/UTC日。空振り、失敗、受付不明も枠を消費する。POST前にSQLite Durable Objectへ永続予約し、自動retryしない。
旧 `msm-fallback/` と新 `msm-request/` の両方を会計する。

欠落404、SPA HTML、JSON不正、schema不正、過大、取得失敗はUNKNOWNで停止する。
欠落を期限切れに読み替えて起動せず、次tickでもPOSTしない。
`request_id` は起動要求のID（現在時刻のミリ秒文字列）であり予報Runではない。
新Workerは `observed_run` を送らず、実Runの発見を装わない。
`DISPATCH_ACCEPTED` は受付だけ。`publication: NOT_VERIFIED` で、hintの更新から公開成功を宣言しない。
単一の `RequestGate`（固定名 `fallback-requests-v1`）がPOSTを直列化する。
SQLite storageの永続予約後に最大10秒のPOSTを1回だけ実行し、受付不明・失敗でも枠を戻さない。
90分＋最大送信時間の保守的な間隔と、UTC日8件＋rolling 24時間8件の厳しい方を適用する。
日跨ぎ、再起動、古いKV、重複tickでもDO台帳が上限を守る。KV書込失敗でもDO予約は残る。
DO binding欠落・台帳不正・storage障害ではPOSTしない。observeではDOへ予約しない。
同じWorkerの既存Secretを使用し、新しいSecret・権限は要求しない。

Workerからcatalog/release取得、catalog SHA-256、全asset inventory照合、上流listing解析、
予報Run発見、canonical公開完了の判定を取り除いた。

## Actions側の境界

workflow concurrency `static-production` / `cancel-in-progress: false` を共有する。
既存opt-in、`static-publication`等の承認environment、承認済みアプリSHA固定を維持する。
preflightとproducerは同じ保護environmentを使うため、設定によっては各jobに承認が必要となる。
preflightのSHAをproducerへ渡し、承認変化があればidentity gateで停止する。

1. boundedな完全な履歴と全active状態を照合し、自分以外の実行中・承認待ち、日次8、90分、
   重複request ID、rerun、任意SHA指定を拒否する。不明/切詰め/通信失敗は停止する。
2. canonical release・catalogの実bytes/hash、全asset inventoryと全payload bytes/hashを検証する。
   hintがinventoryにあるdeploymentではcatalogのRun・日時・commitとhintを照合する。
   hintを持たない旧deploymentは移行元として認めるが、Workerによる欠落起動は認めない。
3. 既存 `preflight_feed.py` で25時間を満たす新しい完全な予報Runを発見する。
   新Runなしは健康な既存catalog・完全な予報だけNO_NEW_RUN。それ以外は不明/失敗。
4. 検出した実Runをjob名 `msm-detected/<Run>` としてActionsの履歴へ記録する。
   起動要求のrun-name `msm-request/<request_id>` と区別する。旧observed_run要求も会計する。
   先行する同Runの検出履歴が2件ならproducer前に停止し、失敗も含める。
   検出Run時刻まで遡って履歴を読み、日跨ぎでRun制限を解除しない。
   producerのlisting発見直後・payload生成前とupload直前に検出Run一致を検査し、
   承認待ち中のRun変化が会計を迂回しない。
   履歴・jobsの欠落/切詰め/取得失敗では上限判定を通さない。
5. 必要な場合だけ既存restore・生成・全payload decode/coverage・独立build/E2E・公開gateへ進む。
   upload直前にcanonicalとcandidateのRunを比較し、同じ予報の再公開・期限だけの更新を拒否する。
   upload後はcandidate identity、canonical、鮮度一致と全payloadを検査する。

## 既存公開からの移行（このPRでは実行しない）

1. このtoolingをmergeする承認と、既存の公開手順で**新しい完全なRun**を公開する承認を別途得る。
   Workerはobserve・Cron停止、外部opt-in無効のまま。SecretやKVを再作成しない。
2. 既存scheduleまたは個別承認されたfeed公開で、承認済みアプリcommitを固定したまま
   同じdeploymentにhintを含める。新Runがなければ待つ。同Runを再公開して移行しない。
3. canonical catalog/release/payload/hintが同じ候補と一致することをActionsで確認する。
   404や古いhint、catalogとの不一致がある場合はWorkerを有効化しない。
4. 以下の限定observe実測を別途承認後に行う。CPU・共有枠等の受入後までdispatchを許可しない。
   旧KVの要求数・Run数・pendingは移行時に消去しない。
5. 本番配置の承認にはSQLite DO namespace追加（`request-gate-v1` migration）も明示する。
   このPRは宣言とローカル試験だけ。旧Workerをobserve・Cron空で停止し、伝播・実行終了を待つ。
   初回台帳は既存KVとGitHub履歴の大きい方でseedする。旧版とdispatchを並行稼働させない。
   台帳は以後消去・再作成しない。rollback時も新旧dispatchを同時に有効化しない。

## 実測履歴と次の限定実測

旧526は接続先不明・未解決。初回traceはCPU 38ms / wall 3300ms、outcome exceptionで、
CPU超過が直接の停止理由と確定したものではない。
17daa313の限定observe再実測はFRESHでCPU 17ms（provider集計17.232ms）、wall 348ms。
canonical catalog 733,586 bytes / release 29,909 bytesはともに200。
GitHub・上流未到達で526は再現せず、過去の原因解決を証明しない。
最終読取2026-10-26 18:33 JSTはMODE=observe、Cron空、外部opt-in未設定。
停止後追加tickは観測上0だが約2分のtail欠測がある。このPRでは本番設定を操作しない。

次の実測も**別途承認後のみ**:

1. 対象を既存Worker・既存KV・固定commitに限定し、observe配置、Cron反映待ち最大15分＋
   観測最大30分・最大2tick、停止とread-backまでを一組にする。opt-inは無効、実dispatchなし。
2. 先にhint付き公開の証拠と設定・アプリSHA・無料plan/共有使用量をread-onlyで確認する。
   hint欠落なら欠落経路の確認だけとし、更新候補経路の受入とはしない。
3. Cloudflare tailとprovider CPU集計で各tickのCPU/wall/outcomeを取得する。
   固定stage/hostname/status/read bytesだけを記録し、token/header/本文/Secret URLを出さない。
   新stageは `canonical_freshness`、GitHub 5 active状態・recent、dispatch、heartbeat。
4. 最初のUNKNOWN、HTTP/IO/BODY_LIMIT、exceededCpu、CPU 10ms超過、想定外設定、期限または
   2tick到達で停止し、MODE=observe・Cron空をread-backする。停止伝播と遅着ログも確認する。
5. FRESH・期限切れ・GitHub到達・欠落は異なる経路として記録する。実Runやpendingを捏造せず、
   未測定経路は未受入。少数サンプルだけでCPU 10ms以内や無料枠適合を宣言しない。
   526は同じ接続先の段階別証拠を得るまで未解決。接続先変更やTLS緩和で回避しない。

#273が天気障害・復旧通知を担当する。任意heartbeatは検査完了のlivenessだけで、公開成功ではない。
新たなサービス登録・課金変更は含めない。

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

旧Workerのpendingは新Workerでも成功扱いせず保持する。旧pendingを整理する必要がある場合だけ、
別途承認のうえCron停止・進行中実行終了を確認して `abandon-pending` を実行する。
この操作は日次・同Runの旧カウンタとcooldownを保持する。新Workerは公開確認を行わず、
Actionsのcanonical検証と実公開後の検証を証拠とする。KVは厳密lockではない。

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

## 無料枠と未受入の境界

15分tickは起動判断だけ。無条件dispatchをしない。Workerの通常FRESH経路は鮮度ファイル1取得、
更新候補経路は鮮度1＋GitHub active 5状態＋履歴1、dispatchと任意heartbeatを含め最大9 HTTP取得。
鮮度ファイルは2,048 bytes、GitHub各応答は1 MiBで停止する。KVはtickごと1 read、
要求予約時1 write（方針上最大8/日）。既存TAF、管理操作、重複tickも共有枠に加算する。
Actionsはpreflight最大10分＋producer最大45分、最大8要求なら440 runner分/日が保守的上界。
空振りも要求枠を消費し、永続artifact/cacheを追加しない。実Run記録は既存Actions jobs metadata。
Public repoの標準runner以外を認めない既存identity gateを維持する。

Free CPU 10msは実測による別gate。ローカルテスト、bundleサイズ、HTTP回数から合格と宣言しない。
実アカウントの共有使用量、無料plan、Pages Direct Upload制限、保存枠、課金状態は別途確認が必要。
要求の直列化はFreeでも利用できるSQLite Durable Objectを使用する（legacy KV-backed DOは使わない）。
日次最大8予約、通常各予約で台帳1 read＋1 write、初回/旧counter取り込み時は追加write、最大10秒の外部POST。重複/拒否もDO readを消費する。
Workers・KV・DOの共有枠を個別に確認する。Free planでDO上限に達した場合は失敗で停止し、
有料planへ切り替えない。新namespaceの実配置、provider CPU/課金集計はこのPRでは未実施。


公式資料:

- [Durable Objects pricing / FreeはSQLiteのみ](https://developers.cloudflare.com/durable-objects/platform/pricing/)
- [Durable Objects storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [GitHub workflow jobs metadata](https://docs.github.com/en/rest/actions/workflow-jobs)

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
