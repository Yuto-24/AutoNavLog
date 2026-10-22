# #123 無人収集・監視の受入準備（2026-10-22 JST）

**監視・定期公開の有効化はまだ受入可能な状態ではない。**
単発MSM復旧と無人quota収集は別のgateである。今回の調査・修正は後者の準備で、
Cloudflare Pages / Direct Upload、承認済みアプリSHA、Zero-cost方針を維持する。
本番設定、IAM、secret、billing、監視・定期公開のswitchは変更していない。
PR merge、Pages公開、TAF deploy、rollback、#123/#116の完了更新、#146着手も行わない。

正本は [#123](https://github.com/Yuto-24/AutoNavLog/issues/123)、
[AGENTS.md](../../../AGENTS.md)、[Static operations](../../static_operations.md)。
調査のbaseは `3224beb3c443e53afe38d005247dae5dc94a232e`（#264 merge後）。

## 再利用する有効な受入証拠

| Gate | 証拠と今回の扱い |
| --- | --- |
| 10/22の単発復旧、canonical Auth/Sync/Weather/Local | [受入記録](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-6330749805)、[公開run 39624922469](https://github.com/Yuto-24/AutoNavLog/actions/runs/39624922469)。production artifact E2E 10/10、実MSM/TAF、Local保存・browser再起動、Linux Chrome通常/シークレットでAuth/Sync復元。今回再実施したとは扱わない |
| 通知到達 | [10/3のoperator確認](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5966946774)、[実scheduleの失敗run](https://github.com/Yuto-24/AutoNavLog/actions/runs/37105314720)。通知到達は受入済み。健全な監視の証拠ではない |
| 同一origin rollback/restore、Local保持 | [10/3のoperator確認](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5966834735)。ログアウト状態でProject/Last Calculation保持。同じアプリSHA間の受入であり、異なるschema間の互換性は主張しない |
| canonical切替・旧Pages hostからのredirect | [9/29のHTTP受入](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5890743462)と、その後の#123 cutover完了記録を維持 |
| 過去のFree/Spark利用実績 | [9月の観測と10/1の扱い](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5931242864)を歴史的証拠として維持。現在月のcounter、plan観測時刻、limits確認時刻へ転記しない |

## 今回のread-only観測

GitHub RESTの設定・secret **名だけ**を既存CLI認証で読み、値の抽出や権限追加はしなかった。
接続pluginに設定一覧・workflow dispatch機能がないため、これらは既存`gh`で実施した。

- Repositoryはpublic、default branchはmain。
- `STATIC_APPROVED_SHA=a4de2a226dfa205b8be42fb62a710a1b06271ed8`、
  `STATIC_ORIGIN=https://navmate.yuto24.com`、`STATIC_AUTOMATION_ENABLED=false`。
  `STATIC_MONITOR_ENABLED`は未設定。
- repository secretは`STATIC_CLOUDFLARE_READ_TOKEN`のみ。
  `STATIC_GITHUB_READ_TOKEN`は未登録。Google unattended authのworkflow実装もない。
- `static-production` / `static-publication` / `static-validation` のenvironment variablesは空。
  最初の2環境には`CLOUDFLARE_PAGES_TOKEN`があり、validationにはsecretなし。
  productionはbranch policy、publicationはbranch policyとYuto-24 reviewerを保持。
  これは構成の観測であり、トークン内容・全権限の再認定ではない。
- [monitor 39698639436](https://github.com/Yuto-24/AutoNavLog/actions/runs/39698639436)は
  skipped。実行成功・定期監視の証拠には数えない。
- 11:03:31 JSTのcanonical GETはclean v1.20.1、上記承認SHA。
  catalogは622,770 bytes、SHA-256
  `6f871a4c8bfba80537cce5af36530570375f71cd7558d67e26f75373b596c269`で
  10/22受入時と同じ。生成07:06:53、期限13:06:53 JST。
  11:06:53を過ぎれば残り2時間を切り、既存monitorは警告する。
  この単発feedの観測から継続freshnessは主張しない。

既存のmanual-only `static-provider-probe`を、上記main SHAを`reviewed_sha`に固定し、
既存read-only credentialで2回実行した。monitorやpublication workflowではなく、
schedule・設定変更・token作成・新しい権限を伴わない診断である。

| Provider read | 結果 | 判定の限界 |
| --- | --- | --- |
| Cloudflare subscriptions | [39709134858](https://github.com/Yuto-24/AutoNavLog/actions/runs/39709134858)、11:02:29 JST、`reachable_not_evidence` | response到達のみ。plan製品対応、無課金制御、完全account coverageを証明しない |
| Cloudflare Worker invocations | [39709137605](https://github.com/Yuto-24/AutoNavLog/actions/runs/39709137605)、11:02:36 JST、`reachable_not_evidence` | invocation系列への到達のみ。quota対象のcache/rejection/account全体との対応を証明しない |
| GitHub cache usage | 既存CLIでGET成功、target repository一致 | 瞬間使用量だけ。容量設定、月内課金、owner共有枠を証明しない |
| GitHub cache storage-limit | **HTTP 402、支払方法登録を要求** | 数値を取得できない。既定10 GiBや課金額ゼロを補完しない。支払方法の追加はしない |
| GitHub personal billing Usage / Usage Summary | 既存CLIで拒否、`user` scope追加を要求 | このOAuth sessionのアクセス不足。fine-grained `Plan: read`の取得可否・全SKU coverageは未検証。scope追加はしない |

Cloudflareの両runは設計どおりexit 1 / **BLOCKED**、GitHub上はfailure。
正常CIやquota受入と呼び替えない。raw response、billing row、credentialは保存・公開しない。
cache設定の402は実観測で、[REST仕様](https://docs.github.com/en/rest/actions/cache#get-github-actions-cache-storage-limit-for-a-repository)
の公開status一覧から推定したものではない。今回の402だけで全認証方式が不可能とも断定しない。

## 修正と検証の範囲

`verify_limits.py`の既存parserは、Firestoreの可視`aside`注記を収集せず、
GitHubのinline link終端に存在しない空白を挿入していた。
これにより上限・scopeが同じ公式HTMLでも`limits_clause_changed`になった。
可視注記を本文blockとして扱い、inline要素間は元の文字列を保持、`br`は空白として扱う。
値、単位、plan列、section、affirmative clause、重複拒否、hidden/history拒否は維持する。
table/文言への緩い部分一致や、HTTP成功だけでのtimestamp更新は追加しない。

修正後、11:03:30 JSTに取得した実公式HTMLのsemantic checkは両方成功:

- [Firestore quotas](https://firebase.google.com/docs/firestore/quotas):
  SHA-256 `416c0e5e5b6940b218170512aa61de8f8b42c45d634a946c842851421cdc4c7a`
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions):
  SHA-256 `44627886aad6e050c3b3ed092acf3a3495f9d3fe1f68b437a5a9642e675af94e`

同じlocal取得経路のCloudflare Pages/Workers公式文書はHTTP 403。
全4 sourceのauditは依然BLOCKEDで、`limits_checked_at`を発行していない。
個別HTMLの成功を全体のlimits受入やcurrent account planの証明に使わない。
意図したActions runnerでの全4 source受入は残る。
テストは縮小したHTML fixtureによる回帰であり、live取得証拠と分ける。
最終の実行コマンド・Independent Review・CI・local runtime結果は本変更のPRへ記録する。

## 未完了の実装・アクセスを分ける

`collection_gate.evaluate()`はFirebaseのplanしか組み立てられず、5つの必須planと
9 metricの完成条件を満たさない。現在のcredentialを揃えるだけではOKにならない。
`STATIC_QUOTA_REPORT`へのfallback、手動counterの定期入力、UNKNOWNのゼロ補完は行わない。

| 残るgate | 実装済み / 未完了 | 次に必要な証拠・実装 |
| --- | --- | --- |
| Cloudflare Free/Workers plan、account Pages builds | privateなsubscription/project/deployment inventoryあり。native plan・billing control・月間build総数への対応は未実装 | 完全scope、削除履歴・保持期間も考慮したquota counter、製品/plan対応。deployment件数をbuild件数に変換しない |
| Workers daily requests | UTC windowのread-only probeのみ | provider quotaに算入するinvocation/cache/rejectionの範囲とaccount全体を検証しadapter化。no dataはゼロにしない |
| Firestore identity/operations/storage | ACTIVE Firebase、billing-off、freeTier database、日次DELTA/最新GAUGEのadapterあり。実account系列は未検証 | 読取credentialと無人更新、実際のlabel/期間/pagination/ゼロ系列・欠測の検証 |
| Firestore outbound | current three-surface auditを作るadapterなし | Usage/Monitoring/Quotasの現行configurationを無人で確認できる経路。#230の限定例外のみ維持。403や欠測は例外にしない |
| GitHub shared storage/billing control | cache usage/configurationの部分adapterのみ | Plan readでのowner全体billing SKU/期間/単位、Actions+Packages included allowance、個人account課金防止、cacheの月内課金。cache設定402の無課金での解決経路も必要 |
| Official limits | 4 sourceのstrict verifierあり、今回2 sourceのlive HTML誤判定を修正 | runnerから4 sourceすべて取得・意味検証し、初めてtimestampを発行 |
| 全体監視 | collection失敗はjob失敗、古いreportへfallbackなし | native全fragmentを組み立て、redacted audit出力と実scheduleでfreshness/period更新を受入 |

Cloudflareの[usage v2](https://developers.cloudflare.com/api/resources/billing/subresources/usage/methods/get/)
はAlpha/Restrictedと明示され、全Free accountで使える安定quota APIとして採用できていない。
GitHubの[usage API](https://docs.github.com/en/rest/billing/usage)のnet額だけでは
included allowance由来か割引・credit由来か、将来課金が防止されるかを確定できない。
これらを非課金・無人・完全scopeで取得する経路は未確認であり、無条件に「設定すれば完了」としない。

Googleの[公式WIF pipeline手順](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
には、pool/providerを管理するprojectでbilling enabledを確認する前提がある。
これをそのままSpark projectの設定承認案にしない。無料枠内で消費することとbilling-offは別条件。
billingを有効化しない認証経路（既存認証基盤の利用可否も含む）を実際のtargetで検証してから、
権限・trust条件・secret寿命を含む設定差分を作る。これはWIFの全利用が有料という断定ではない。
短命access tokenをsecretへ手動更新する方式では#123の無人要件を満たさない。

## 承認が必要な操作と順序

**現時点ではmonitor有効化の承認は求めない。** providerから取得できない事実を
policyの例外追加・billing有効化で解決しない。API選択・adapter実装は実装担当の仕事とし、
operatorへ数値の手入力や未検証の認証方式の選択を依頼しない。

1. 今回の修正PRのmergeは別途明示承認。mergeだけで本番アプリSHAは変わらない。
2. credential準備は別の設定変更承認。GitHubはtarget repository `Actions: read`と
   owner `Plan: read`が最小候補。Googleはtarget projectの
   `resourcemanager.projects.get`、`firebase.projects.get`、`datastore.databases.list`、
   `monitoring.timeSeries.list` / `monitoring.metricDescriptors.list`が読取対象。
   Google認証の無課金成立とGitHub 402の解決は未確定なので、IAM/OIDC/API enablementの
   実行コマンドを承認済み扱いしない。credential値をchat/PRへ出さない。
3. 全adapterと認証が揃ったら、まず**非公開のprovider応答を外へ出さない単発read-only収集**を
   承認済みrunner/credentialで検証。全plan/metric/limitsの有効性、日/月境界、
   provider失敗・欠測・stale・80%超過でのfail closed、秘匿、古いreport不使用を確認。
   fixture成功だけではこのlive gateを通さない。
4. healthyな無人収集が成立した後に、`STATIC_MONITOR_ENABLED=true`の設定変更を明示承認。
   実際のscheduleでcredential手作業なしの更新とredacted summaryを確認する。
   通知経路を変えない限り10/3の通知到達受入を再利用する。
5. 別途`STATIC_AUTOMATION_ENABLED=true`の定期公開を明示承認。
   approved SHA、6公開設定、Pages target/Free、Git builds/preview停止、in-flightなしを再確認。
   feedが失効していれば、承認された手順でfreshなMSMを再生成し、全artifactをbuild/checkする。
   10/22のartifactや過去のbuild-only成功をfresh候補として再利用しない。
6. hourly preflightの`NO_NEW_RUN`やskipped、manual dispatchを更新成功に数えず、
   **旧catalog期限を跨ぐ実scheduleの成功公開を少なくとも2回**観測する。
   各runのevent、SHA、deployment、catalog生成/期限/hash/bytes、whole-inventory一致、
   独立freshness check、canonical continuityを記録。受入中も古い予報を成功扱いしない。
   既存rollback/Local保持証拠を再利用し、変更していない受入を繰り返さない。
7. 全gateの証拠が揃ってから#123の各AC、親#116、#146の依存状態を更新する。
   今回は完了checkboxを変更しない。

## #123 Acceptance Criteriaとの対応

| AC | 根拠 / 残ること |
| --- | --- |
| React+Local production packaging、Reference/differential、Browser E2E | `test.yml`と`static-production.yml`を維持。#264と10/22公開runの既存成功を再利用。今回UI変更なし、browser再受入不要 |
| 再現可能なStatic deploy | pinned SHAでの10/22単発公開は受入済み。無人定期継続は上記2回の実scheduleが残る |
| canonical Auth/Sync/Weather/Local、旧Pages host回避 | 既存cutover証拠と10/22受入を再利用。現在のMSM freshnessとは区別 |
| Serverless独立deploy | `services/taf-proxy`と[TAF運用](../../taf_proxy.md)の独立手順を維持。今回deployなし |
| Free依存一覧・release時limit確認 | [Static production](../../static_production.md)とstrict verifier。4 sourceのlive完走とprovider native証拠が残る |
| 課金なしのproduction運用 | 歴史的Free/Spark実績あり。無人current evidence、billing prevention、shared allowanceは未完了 |
| rollback文書 | 既存runbookと10/3受入を再利用 |
| FastAPIを最終必須workflowにしない | 現行Pages/static構成を維持。local Compose healthはproduction合格の代用にしない |
| 親#116更新 | 全gate完了後。#123はopenのまま |
