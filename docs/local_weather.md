# Local Forecast Weather Adapter (#144 / #161)

Local FORECASTは固定fixtureを使わず、同一originの`weather/msm/catalog.json`または
`weather/gsm/catalog.json`と、library-produced `MsmPreparedData` / `GsmPreparedData`を
取得し、Pyodide内で検証・補間・計算する。
`jma-gpv-weather 0.6.0`のpublic APIがRun discovery/selection、model/time/area/altitude
coverage、decode、補間、provenanceの正本。Legacyも同じ公開`SurfaceTemperatureQuery`を使う。
旧private surface-temperature compatibility branchは削除した。

## 配信境界と運用

2026-09-16、実Windows Edge 153からRISHのHTTP/HTTPS listing・GRIBをGETし、
`Range: bytes=0-1023`あり/なしを測定した。全組合せでCORSによりresponseを読めなかった。
通常の証明書検証を有効にしたWindowsでも再現する。HTTPS化やRangeでは解消しない。
ブラウザがresponseを公開しないためredirect chain/Range応答の成功は確認できていない。
[source実測](evidence/issue-144/windows-source.json)を参照。

このため採用した追加componentは**batchによる静的MSM feed生成**。
HTTP proxy、ユーザーごとのserver計算、FastAPI endpointは追加しない。
既存のdesktop libraryがGRIB取得/decodeを行い、public `MsmPreparedData.to_bytes()`を
content-addressed fileへ出力する。Browserはそのfileを検証して気象値を求める。
producerへProjectや経路を送らず、固定範囲・時間帯を定期生成する。
無料の既存マシン/runnerとstatic hostingで運用可能で、有料proxyは不要。
production buildと手動feed更新は[Static production](static_production.md)、定期deploy自動化は#123を参照する。

```sh
# 通常の依存をinstallしたPython環境（pygrib等のnative依存を含む）
python scripts/prepare_msm_feed.py --output /tmp/msm-feed --cache /tmp/msm-source-cache
AUTONAVLOG_MSM_FEED=/tmp/msm-feed npm --prefix web run build:local
```

再現可能なproducer runtimeは`docker build --target weather-feed -t autonavlog-weather-feed .`。
出力とcacheをbind mountし、`--output /output --cache /cache`を渡す。
既定はlibrary既定bounds、現在UTC正時から24時間、最新のcompatible Runを2つ。
`--start`、`--hours`、`--bounds`、`--max-runs`で明示変更できる。
Run/product/fileの選択はlibraryへ委譲する。payload formatは独自化しない。
catalogはsource listing本文、Runとpayload hash/size/file、生成/失効日時のみを保持する。

運用では3時間ごとを目安に生成し、payloadを先に配信してcatalogを最後に置換する。
catalog lifetimeは6時間。失敗した生成は既存catalogを更新しない。
producerと端末の時計ずれにより生成時刻が未来になる場合は5分まで許容する。
5分を超える未来の生成時刻は拒否し、`expires_at <= 端末の現在時刻`は猶予なしで失効とする。
この許容はcatalogの6時間のlifetimeやBrowser cacheのTTLを延長しない。
7日以内の旧Run assetとそのsource listingを保持し、再利用前にlibraryでhash/formatを検証する。
新listingを取得したURLは新しい内容を優先する。参照されなくなったpayloadも7日間の猶予後に除去する。
static hostはcatalogをrevalidateし、hash付きpayloadをimmutableとして配信できる。
feedのcatalogとpayloadはcookie・HTTP認証などのcredential不要で取得できるpathへ配信する。
Browser transportは`credentials: "omit"`を使用するため、ログイン必須pathや
認証画面へのredirectは利用できない。#121でこの公開配信契約を維持する。
認証が必要になる場合は#121側でtransport契約を明示的に見直す。
`prepare_local.py`はfeed省略時に古い生成済みfeedを除去する。feedなしでFORECASTが成功することはない。

配信範囲/時間の不足はmodelの範囲外とは異なる。最新compatible Runが未生成なら
`WEATHER_PREPARED_UNAVAILABLE`として停止し、別Runへ切り替えない。
旧Runのasset保持は全過去時間帯の再計算保証ではない。再計算に必要な配信時間帯が
ない場合も明示失敗し、保存済みNAV LOGは閲覧できる。

## Cacheと失敗

Browserはportable payloadだけをモデル別の`autonavlog.weather.msm.v1` /
`autonavlog.weather.gsm.v1` Cache Storageへ保存する。
raw/normalized cacheはproducer側libraryにのみ存在する。

| 項目 | 契約 |
| --- | --- |
| catalog | TTL 5分かつcatalog失効まで。schema検証後に保存 |
| payload | SHA-256名、TTL 7日、library integrity検証後に保存 |
| 容量 | モデルごとのcache合計128 MiB、payload 32 MiB、catalog 4 MiB。stream中も上限確認 |
| invalidation | hash変更/TTL/不正cache。古いentryからevict |
| corruption | 不正entryを削除し1回再取得。失敗時は明示error |
| storage拒否/quota | 計算済み値は使えるがwarm reuseは保証しない |
| #124 eviction | 既存`autonavlog.weather.*` callbackで全Weather cacheを解放 |

Project/Last CalculationはIndexedDBの既存#124 contractを維持する。失敗計算では
Last Calculationを置換しない。`updateAndRecalculate`の入力commitと失敗時の保持も維持する。
通信中もWorkerのmutation queueを直列化する。UI/Calculation Coreにfetchは置かない。
`networkAvailability()`はsource health判定に使わない。Legacy/API fallbackはない。

| 失敗 | 表現 |
| --- | --- |
| libraryのmodel/time/area coverage | アプリの候補評価。全MSM候補の明示coverage外のみGSMへ。両モデル外は`FORECAST_UNAVAILABLE` |
| post-prepare altitude/point coverage | `ALTITUDE_OUTSIDE_HGT_RANGE`のみ高度coverage除外。他のUnavailableは処理失敗 |
| listing HTTP/discovery | `WEATHER_DISCOVERY_FAILED` |
| fetch/timeout/CORS | `WEATHER_COMMUNICATION_FAILED` |
| source file 404/503 | `WEATHER_SOURCE_UNAVAILABLE` |
| download中断/サイズ/その他HTTP | `WEATHER_DOWNLOAD_FAILED` |
| library discovery / Run不在 / 未配信 / source値不足 | `FORECAST_PREPARE_FAILED`でBlock（原因を保持）。取得障害から別Run/modelへ退避しない |
| catalog schema/期限 | `WEATHER_CATALOG_INVALID` / `WEATHER_CATALOG_EXPIRED` |
| payload hash/format/decode | `WEATHER_PAYLOAD_INTEGRITY_FAILED` |
| 不正cacheの再取得失敗 | `WEATHER_CACHE_CORRUPT` |
| その他端末内処理 | `WEATHER_PROCESSING_FAILED` |

native GRIB decode/prepared生成はproducerの責務で、失敗時は非zero終了と
library exceptionの`error_type`/messageを出力する。Browserに不完全なcatalogを公開しない。
取得/生成失敗をcoverageに変換しない。公開済みcatalogが期限切れになれば計算は停止する。

## 実データacceptance（2026-09-16）

通常CIはchecked-in portable実データsnapshotをrouteで供給し、Weather sourceへの通信を禁止する。
Pyodide/npm等の依存取得は別であり、完全air-gap CIを意味しない。
fixture packagingは`AUTONAVLOG_TEST_FIXTURES=1`のテストbuildのみ。
実データacceptanceはfixtureなしのStatic production buildを実Windows Edge
153.0.4234.32（Win32）から検証した。`/api/**`はabortし、呼出し0件。

- Run: `20260915210000`、valid window: 2026-09-16 03Zから24時間。
- bounds: library既定29.7–35.2N / 128.5–134.8E。
- RJFM→米ノ津→玉名→RJFT、ETD 12:00 JST、6,500/7,500/6,500/2,500 ft。
- このNAV LOGの要求時間に必要なのはpall/surfaceのFH00-15、
  24時間の共有feedでは加えてpall FH18-33 / surface FH16-33を取得。
  元URLは[producer report](evidence/issue-144/producer.json)とpayload provenanceに保持。
- cold Weather body: **5,629,561 bytes**（catalog+payload）、warm reload後: **0 bytes**。
  アプリ/CDN assetsやHTTP headerを含まない。
- cold計算: **1,744 ms**、warm: **424 ms**。
- Windows browser process treeのRSS sample最大: cold **1,147,670,528 bytes**、
  warm **1,175,248,896 bytes**。専用profileのbrowser全体で、WASM heap単独でも厳密peakでもない。
- native decode/prepared生成: 2 Run / 36.8秒、`/usr/bin/time -v`最大RSS **308,048 KiB**。
- 独立desktop GRIB/NetCDF pathと、風・気温・NAV LOGを絶対誤差1e-8以内、
  表示値とprovenanceを一致確認。OOM、page crash、予期しないreloadなし。

[Windows計測詳細](evidence/issue-144/windows-acceptance.json)。単一host/単一試行で、
低memory端末の上限保証や長時間leak検証ではない。

再実行はfresh feedを生成し、同じ日付/ETDを両方に指定する。

```sh
python scripts/local_reference.py --feed /tmp/msm-feed --native-cache /tmp/msm-source-cache \
  --flight-date YYYY-MM-DD --departure-time HH:MM > /tmp/reference.json
# Windows Nodeで実行。CDPは専用Edge/Chromium profileのloopback endpointを使用
node --experimental-strip-types web/scripts/accept-local-weather.mjs \
  --cdp http://127.0.0.1:9227 --url http://localhost:4179 \
  --flight-date YYYY-MM-DD --departure-time HH:MM --reference /tmp/reference.json --output /tmp/acceptance.json
```

**Merge gate: 物理iPhone/iPad Safariでfresh実MSM FORECASTを少なくとも1回完走し、
OOM/予期しないreload/crashがないこと。** 2026-09-16に物理iPad Safariで、
fixtureなしのfresh実MSMを使ったHTTPS Static Local buildをcold/warmとも完走し、
Weather失敗時のProject/Last Calculation保持と、OOM/予期しないreload/page crashがないことを確認した。
[実機確認記録](evidence/issue-144/ipad-safari-acceptance.md)を参照。
今後再確認する場合もOS/browser、Run、日時、cold/warm、失敗時のProject/Last Calculation保持を記録する。
WindowsやPlaywright WebKitの結果をこのgateの代替にしない。

#144のMSM transport境界を#161でモデル別に接続する。優先順位、固定Run、更新意思、
最終Requirementの再計算は[Forecast selection](forecast_selection.md)を参照。

## GSMのon-demand取得

GSMは常時prewarmしない。LegacyはMSMのcoverage不足が確定した計算、または保存済み
GSM Runの再計算でのみ上流GSM clientをprepareする。既存MSM prewarmerはMSM専用のまま。
Local WorkerもPythonの要求に応じて必要なcatalog / 正確なRun assetだけを取得する。
候補評価に必要な複数Runを順番に取得でき、取得中断時には計算結果・Last Calculation・
2クリック更新意思を確定しない。同一actionを再実行し、全体が完了してから既存の
persistenceへ反映する。取得の再実行は128回、モデル切替を伴う全体再計算は4回で制限する。

静的配信のGSM payloadは運用者が必要な範囲・時間帯を明示して生成する。
既存のMSM定期feed workflowにGSM生成は加えない。GSM公開がない・期限切れ・要求する
assetがない場合は取得障害として停止し、モデルcoverage外と偽って計算を続けない。
保存済みNAV LOGの閲覧にはWeather feedを必要としない。
高度を理由に「全MSM候補がcoverage外」と確定するには、discoveryが返した候補をすべて
評価できる配信が必要になる。通常の2 Run feedだけで不足する場合は、MSM producerの
`--max-runs 32`等で対象候補を配信する。未生成の古いRunを候補から隠してGSMへ進めない。
モデル全体のoffline仕様外なら、そのモデルのpayload取得は不要。

```sh
python scripts/prepare_msm_feed.py --model GSM --output /tmp/gsm-feed --cache /tmp/gsm-cache \
  --start YYYY-MM-DDTHH:00:00+00:00 --hours 24 --bounds 29.7 35.2 128.5 134.8
AUTONAVLOG_MSM_FEED=/tmp/msm-feed AUTONAVLOG_GSM_FEED=/tmp/gsm-feed npm --prefix web run build:local
```

`--model`省略時は従来どおりMSM。両モデルとも取得、GRIB decode、cache、portable生成は
公開library APIへ委譲する。同じ配信catalogの形式を再利用し、独自気象formatやHTTP
計算endpointを追加しない。GSM catalogを含むStatic artifactはMSMと同じhash・期限・
assetサイズ検証を必須とする。実際の外部公開は通常のproduction承認手順に従う。

#161のsynthetic GSM / 改変MSM acceptance fixtureは
[生成条件](../tests/fixtures/forecast-policy-README.md)を参照。ライブGSM配信の容量や
物理iOS端末のmemory確認を過去のMSM受入実績で代替しない。

## CALMとRJFM固定RCAの計算境界

LegacyとLocalは共通の`MsmWeatherProvider`で気象値を投影します。
上空気象がAVAILABLEで、ライブラリの`CALM_WIND_DIRECTION_UNDEFINED`警告があり、
風向なし・有限かつ非負の風速が0.1 m/s未満の場合だけ、航法計算用の風速を0 ktへ
正規化します。元のvector・風速・気温は`calm_normalization.original_values`へ保持し、
provenanceと警告も残します。通常風の風向欠落や取得失敗はCALMとして扱いません。

検証済みRJFM固定RCAは通常の風によるRCA探索を経ずに適用します。POH上昇時間・燃料、
上昇代表高度の気温とTAS、および各実効区間の表示風・手動overrideは従来どおりです。
固定境界以降の物理区間にはCLIMB気象を要求しません。仮想UMKが物理区間内にある場合は
同区間のCLIMBとCRUISEの両方を保持します。古い・不整合な計画と経路外固定RCAは
既存の検証・通常RCA処理に従います。

Issue #204の固定fixtureは診断時の代表気象値と経路を保持しています。全時空間のMSM
予報archiveではなく、未記録の気象はテスト用値です。実データのライブ受入とは区別します。
