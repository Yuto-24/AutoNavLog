# Local MSM Weather Adapter (#144)

Local FORECASTは固定fixtureを使わず、同一originの`weather/msm/catalog.json`と
library-produced `MsmPreparedData`を取得し、Pyodide内で検証・補間・計算する。
`jma-gpv-weather 0.5.0`のpublic APIがRun discovery/selection、model/time/area/altitude
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
static hosting/定期実行のproduction公開は#121の責務であり、このPRでは行わない。

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
7日以内の旧Run assetとそのsource listingを保持し、再利用前にlibraryでhash/formatを検証する。
新listingを取得したURLは新しい内容を優先する。参照されなくなったpayloadも7日間の猶予後に除去する。
static hostはcatalogをrevalidateし、hash付きpayloadをimmutableとして配信できる。
`prepare_local.py`はfeed省略時に古い生成済みfeedを除去する。feedなしでFORECASTが成功することはない。

配信範囲/時間の不足はmodelの範囲外とは異なる。最新compatible Runが未生成なら
`WEATHER_PREPARED_UNAVAILABLE`として停止し、別Runへ切り替えない。
旧Runのasset保持は全過去時間帯の再計算保証ではない。再計算に必要な配信時間帯が
ない場合も明示失敗し、保存済みNAV LOGは閲覧できる。

## Cacheと失敗

Browserはportable payloadだけを`autonavlog.weather.msm.v1` Cache Storageへ保存する。
raw/normalized cacheはproducer側libraryにのみ存在する。

| 項目 | 契約 |
| --- | --- |
| catalog | TTL 5分かつcatalog失効まで。schema検証後に保存 |
| payload | SHA-256名、TTL 7日、library integrity検証後に保存 |
| 容量 | cache合計128 MiB、payload 32 MiB、catalog 4 MiB。stream中も上限確認 |
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
| libraryのmodel/time/area coverage | `WEATHER_OUT_OF_COVERAGE` |
| post-prepare altitude/point coverage | 既存Availabilityとlibrary reason codes |
| listing HTTP/discovery | `WEATHER_DISCOVERY_FAILED` |
| fetch/timeout/CORS | `WEATHER_COMMUNICATION_FAILED` |
| source file 404/503 | `WEATHER_SOURCE_UNAVAILABLE` |
| download中断/サイズ/その他HTTP | `WEATHER_DOWNLOAD_FAILED` |
| Run不在/未配信 | `WEATHER_RUN_UNAVAILABLE` / `WEATHER_PREPARED_UNAVAILABLE` |
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
OOM/予期しないreload/crashがないこと。** この環境では実機確認を実施していない。
HTTPSで配信し、OS/browser、Run、日時、cold/warm、失敗時のProject/Last Calculation保持をPRへ記録する。
WindowsやPlaywright WebKitの結果をこのgateの代替にしない。

#161にはGSM、model priority/coverage fallback、model+Run永続化、2クリック更新を残す。
#144ではMSM単独の取得/transport/public library/WeatherProvider境界だけを実装する。
