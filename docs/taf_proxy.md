# Destination TAF Serverless Proxy (#145)

Local Static Webの目的地TAF取得だけをCloudflare Workers Freeへ分離する。
NAV LOG計算・Project / Last Calculation・認証・MSM取得はProxyに持たせない。
#144の実MSM取得、#121のStatic production切替、#123の配信CI、Account Syncは本変更の対象外。
Legacy runtimeのTAF取得は移行期間中そのまま残す。

## Boundary and failure contract

`local.worker.ts`は`calculate` / `updateAndRecalculate`の直前にPython Local Applicationから
目的地ICAOを取得し、`destinationTaf.ts`でTAFを取得する。FTD、bootstrap、save、loadでは取得しない。
前回の取得結果をリセットし、各計算で取得したbounded decoded recordsまたは失敗理由を
`DecodedTafProvider`へ渡す。Pythonの既存ETA計算・卓越風選択（TEMPO / PROB除外）を共用し、
別のTAF解釈をTypeScriptへ実装しない。UI / Application contractへHTTPを漏らさない。

Proxyへの通信はICAOのみ。Cookie / credential、Project、出発時刻、計算結果は送らない。
`VITE_TAF_PROXY_URL`が未設定・不正なら`TAF_PROXY_NOT_CONFIGURED`、通信・HTTP・quota・CORS・
不正payloadは`TAF_FETCH_FAILED`、7秒の取得deadlineは`TAF_FETCH_TIMEOUT`とする。
全て目的地TAFの`UNAVAILABLE`であり、ApplicationError / Calculation failureではない。
自動retry、Legacy / upstream直接取得へのfallback、期限切れcacheによる成功扱いはしない。
既存UIは「目的地風: 取得できませんでした」を表示する。

TAFは最終情報行の参考風だけ。到着区間は常にCALMで、WCA / MH / GS / ETE / Fuelは変わらない。
既存の風metadata / statusはTAF表示の可否を記録するため変化する。
計算は通常どおり完了し、Projectと新しいLast Calculation（TAF unavailableを含む）を端末内保存する。
TAF取得失敗では既存Projectを削除しない。保存自体が失敗した場合は#124の既存保護が適用される。

## Proxy contract and abuse controls

- 許可するrequestは`GET /taf?icao=RJFM`のみ。ICAOは大文字英数字4文字、1件のみ。
  追加・重複query、任意URL、別path、POST等は拒否する。
- upstreamは`https://aviationweather.gov/api/data/taf?ids=<ICAO>&format=json`に固定する。
  独自User-Agent / Acceptだけを設定し、request headerやcookieを転送しない。
  redirectは`manual`で受け、200 / 204以外を拒否する（他URLへ追従しない）。
- `ALLOWED_ORIGINS`の完全一致originだけを許可。空設定、Originなし、`null`は禁止。
  許可originのみ`Access-Control-Allow-Origin`と`Vary: Origin`を付ける。
  credentialsや任意headerを許可せず、単純GETなのでpreflightは不要（OPTIONSは405）。
- upstreamはheaderとstream全体を含め5秒、decoded body最大64 KiB。
  JSON配列とICAO一致を確認し、不正・別空港・oversizeは502、timeoutは504。
- 同時upstream取得はisolateごとに4件。上限では待ちqueueを作らず503。
- Cloudflare native Rate Limiting bindingでclient IPごと20件/分、cache miss全体60件/分、
  同一ICAOのcache missは1件/分。bindingエラー/欠落も503でfail closed。
- Cache APIに正常TAFを5分、204の空配列を1分だけ保存。エラーは`no-store`。
  cache keyは正規化したICAOのみ。cached responseにoriginは保存せず、response時に付ける。
  cache hitのbrowser TTLは残存時間まで。期限切れfallbackはない。
- Originは認証ではなく、非Browser clientは偽装できる。rate limitはCloudflare location単位で
  eventually consistent、同時数はisolate単位であり、世界全体の厳密な上限ではない。
  無制限のpublic abuseを防げると主張しない。無料枠のhard stopを最後の境界とする。
  拒否やcache hitもWorker request枠を消費する。停止時はTAFだけを利用不可にする。

AviationWeather.govは最大100 requests/分を案内し、CORSを許可していない。
cacheと集約rate制限で負荷を抑えるが、分散拠点を合算した厳密なupstream rateを保証するものではない。
429等を受けたらfail closedし、retryで増幅しない。
[公式Data API](https://aviationweather.gov/data/api/)

## Provider decision / zero-cost estimate

確認日: **2026-09-16**。既存docsで候補だったCloudflare Workers Freeを採用する。
単一ES module、標準Fetch / Cache API / native rate bindingで成立し、framework・KV・D1・
Durable Objects・独自認証・秘密鍵を必要としない。workers.devを使えば独自domain購入も不要。

この日の公式値（永続的な設計ルールではない）:

| 項目 | Workers Free | 本Proxy |
| --- | --- | --- |
| account全体のrequest枠 | 100,000 / 日、00:00 UTC reset | 1回のTAF取得に1 request。cache / 拒否分も計上 |
| CPU | 10 ms / invocation（I/O待ちは除外） | 小さいJSON検証とheader付与のみ。実配置後にmetrics確認 |
| memory | 128 MB | 64 KiB / body、4 upstream / isolate |
| 外部subrequest | 50 / invocation | 最大1回、redirect / retryなし |
| 同時外部connection | 6 / request | 最大1本 |
| 枠超過 | Error 1027 | Browser側でTAF UNAVAILABLE、計算と保存は継続 |

[Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)、
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)、
[Rate Limiting binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)、
[Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/)

Cache APIのworkers.dev制限は[公式docsの修正](https://github.com/cloudflare/cloudflare-docs/commit/935b0366c6ca)で
削除済み。古いR2の例に残る制限記述ではなく、現行Workers Cache APIの仕様を参照する。
local emulatorだけで本番cacheを証明せず、release時に以下の実endpoint確認を行う。

通常利用の容量計画上の仮定: **100人 × 20回/日 = 2,000 requests/日**。
実測利用者数ではない。browser cacheの削減を見込まなくても日次枠の2%、50倍の余裕。
10倍の1,000人でも20,000/日（20%）。他Workerと共有するaccount枠は別途合算する。
1空港を継続して利用する場合、edge cacheが保持されれば1拠点あたり概ね12 upstream/時。
cache eviction / 多拠点 / 複数空港は増加要因であり、保証値ではない。

Free planのまま運用する。Paid planへupgradeしないことが費用境界であり、コード上のCPU設定や
rate limiterを課金上限とはみなさない。既にPaidのaccountを使う場合はこのゼロコスト条件を
満たしたと扱わず、Free accountを選ぶ。quota消費時は翌日resetまでTAF利用不可を許容する。
通常運用で課金登録・有料upgrade・自動課金を必要としない。

## Configuration / deploy

1. Cloudflare dashboardで対象accountが**Workers Free**であること、他Worker使用量を確認する。
2. `npm --prefix services/taf-proxy ci`。固定Wranglerで`npm --prefix services/taf-proxy run check`を実行する。
3. `services/taf-proxy/wrangler.jsonc`を同じdirectoryのignored `wrangler.local.jsonc`へコピーし、
   `ALLOWED_ORIGINS`に本番Static Webのorigin（scheme + host + optional port、末尾slashなし）を設定する。
   複数originはcomma区切り。wildcardを入れず、本番でlocalhostを許可しない。
   Rate namespace IDはaccount内で他Workerと重複しないことを確認する。
4. operatorがWrangler loginし、対象accountを確認して
   `npm --prefix services/taf-proxy run deploy -- --config wrangler.local.jsonc`を実行する。
   本番への公開は別途承認を得て行う。TAFの実行自体にはsecretは不要。
5. 表示された`https://autonavlog-taf.<subdomain>.workers.dev/taf`を
   `VITE_TAF_PROXY_URL`に設定して`npm --prefix web run build:local`を実行する。
   この値は公開URLでありsecretではない。既存Application modeの切替方針は変更しない。
6. 静的配信originからのGET・目的地TAF表示を確認する。workers.dev独立endpointを使い、
   AutoNavLog / AviationWeather originへのfail-open routeを設定しない。
   custom routeを後で採用する場合はCloudflare設定もfail closedにする。

ローカル検証:

```bash
npm --prefix services/taf-proxy run dev -- --ip 127.0.0.1 --port 8787 --var ALLOWED_ORIGINS:http://127.0.0.1:4145
VITE_TAF_PROXY_URL=http://127.0.0.1:8787/taf npm --prefix web run build:local
python3 -m http.server 4145 --bind 127.0.0.1 --directory web/dist-local
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4145 npm --prefix web run test:taf
```

Browser fixture testはTAF応答・quota・timeout・停止を制御する。実upstream smokeとは区別する。
MSMは#125の固定fixtureのままなので、live TAFの有効期間がfixtureのETAと合わない場合は
`TAF_TIME_OUT_OF_RANGE`が正しい。live取得の成功と、過去ETAの表示可否を混同しない。

## Release checklist

- [ ] 上記公式pricing / limits / native rate binding / AWC制限を再確認し、確認日・変更を記録する。
- [ ] accountがWorkers Freeで、自動課金を前提とするPaid planへ変更されていない。
- [ ] 全Worker合算の通常request見積りに余裕がある。cache hit・拒否・abuseも枠を消費すると把握する。
- [ ] origin allowlist / 公開TAF URL / namespace ID / 独立endpointを確認する。
- [ ] Proxy unit / workerd test、Application test、Python選択・数値不変test、Static Browser testを実行する。
- [ ] 実配置のCPU時間がFree制限内であること、実TAF / CORSを確認する。
- [ ] 許可origin付きの同一ICAOを、browser cacheを共有しないclient（例: curlを2回）で
  数秒あけて取得し、2回目も200・短くなったmax-age・同じTAFを返すことを確認する。
  1分以内の2回目が429ならedge cacheが機能していないため、release gateを通さず調査する。
  正常確認後、TTL経過時は古いTAFを返さず再取得することも確認する。
- [ ] upstream error / timeout / 429・1027相当 / Proxy停止でTAFだけUNAVAILABLEになる。
- [ ] その状態でNAV LOG計算、保存、reload / reopenが可能でProject / Last Calculationが残る。
- [ ] 利用枠を実際に使い切る負荷試験はせず、quota応答のfixtureで動作を確認する。
- [ ] #116の進捗・依存関係・チェックを、実装・検証・merge・実配置の実状に合わせて更新する。

## Implementation verification (2026-09-16)

- 公式workerdでproduction moduleを実行し、固定upstream、CORS、cache hit、native rate binding、
  redirect拒否を確認。Node unitではsize / header・body timeout / concurrency / 不正queryも検証した。
- ローカルWorkerから実AviationWeather.govのRJFM TAFを取得: HTTP 200、1 record、1,935 bytes。
  Chromiumで静的origin `http://127.0.0.1:4145`から別originのWorkerへfetchし、
  CORS越しに同じRJFM recordを読めることを確認した。応答cache TTLは300秒。
- Static Webの実Pyodide / IndexedDBでTAF成功→quota相当（1027の503）→7秒timeout→
  通信断→reload→復旧を検証。NAV LOG航法値・Fuelは不変、保存済みcheckpointとLast Calculationは保持。
- production bundleのdry-runは約5.12 KiB（gzip 1.88 KiB）。
  日次枠は上記容量計画と公式制限で検証し、枠消尽を模擬した。負荷で無料枠を消費していない。
- **Cloudflare本番accountへのdeploy・本番origin接続・Cloudflare上のCPU実測は未実施**。
  これらは公開承認とaccount設定後のrelease gate。local workerdの結果を本番実測とは扱わない。
