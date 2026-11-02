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
本番更新とアプリ公開は`--full-run`を指定する。25時間のadmissionを満たす最新Runを
維持し、各Runについてlibraryが認める予報末尾まで生成する。最新2 Runが短期Runでも、
予報末尾が最も遠いRunを追加するため、長期日時の要求を失わない。
通常は最大3 Runを生成する。上限は一律72時間ではなく、libraryのRun別coverageによる。
明示的な`--hours`だけの呼出しは、fixture生成などの限定時間帯として引き続き使える。
全範囲生成に失敗した場合も、短縮したpayloadで成功を装わず既存catalogを維持する。
Run/product/fileの選択はlibraryへ委譲する。payload formatは独自化しない。
catalogはsource listing本文、Runとpayload hash/size/file、生成/失効日時のみを保持する。

運用では3時間ごとを目安に生成し、payloadを先に配信してcatalogを最後に置換する。
catalog lifetimeは6時間。失敗した生成は既存catalogを更新しない。
producerと端末の時計ずれにより生成時刻が未来になる場合は5分まで許容する。
5分を超える未来の生成時刻は引き続き拒否する。
`expires_at <= 端末の現在時刻`は配信更新の失敗として記録するが、catalogの期限だけでは
計算を停止しない。公開feedから取得できる旧catalog/payloadは、初回訪問・別端末でも、
後述の実データ検証をすべて満たす場合に限り利用できる。
このconsumer方針はcatalogの6時間のlifetime、producerのfreshness gate、
公開受入・監視の閾値を変更しない。期限切れfeedは運用監視上は失敗のままとする。
7日以内の旧Run assetとそのsource listingを保持し、再利用前にlibraryでhash/formatを検証する。
新listingを取得したURLは新しい内容を優先する。参照されなくなったpayloadも7日間の猶予後に除去する。
static hostはcatalogをrevalidateし、hash付きpayloadをimmutableとして配信できる。
feedのcatalogとpayloadはcookie・HTTP認証などのcredential不要で取得できるpathへ配信する。
Browser transportは`credentials: "omit"`を使用するため、ログイン必須pathや
認証画面へのredirectは利用できない。#121でこの公開配信契約を維持する。
認証が必要になる場合は#121側でtransport契約を明示的に見直す。
`prepare_local.py`はfeed省略時に古い生成済みfeedを除去する。feedなしでFORECASTが成功することはない。

配信範囲/時間の不足はmodelの範囲外とは異なる。通常のfresh取得では最新compatible Runが
未生成なら`WEATHER_PREPARED_UNAVAILABLE`として停止する。期限切れfeedではlibraryが
発見したcompatible Runのうちcatalogに実assetがあるRunを選ぶ。通信失敗時は端末に
保持された検証済みペアのRunだけを候補にする。未固定時または既存2クリック更新の
明示refresh時だけ選び直す。固定Runを別Runへ黙って置換せず、保持ペアにそのRunが
ない場合も失敗しLast Calculationを維持する。GSMやLegacyへの切替は追加しない。
旧Runのasset保持は全過去時間帯の再計算保証ではない。再計算に必要な配信時間帯が
ない場合も`WEATHER_PREPARED_UNAVAILABLE`で明示失敗し、保存済みNAV LOGは閲覧できる。
consumerはlibraryにpreparedの実収録範囲を確認させてからsource identityを照合する。
不足を破損として再取得し続けない。Run違い、hash破損、source identity不一致の
`WEATHER_PAYLOAD_INTEGRITY_FAILED`は維持する。

## Cacheと失敗

Browserはcatalog、portable payloadと検証済みペアのindexを
`autonavlog.weather.msm.v1` Cache Storageへ保存する。
raw/normalized cacheはproducer側libraryにのみ存在する。

| 項目 | 契約 |
| --- | --- |
| 通常catalog | TTL 5分かつcatalog失効まで。schema検証後に保存し、期限後は公開feedを再取得 |
| last-good index | 検証済みcatalog全文と選択assetの対応。payload検証後に保存、TTL 7日。fallbackで期限を更新しない |
| payload | SHA-256名、TTL 7日、library integrity検証後に保存 |
| 容量 | cache合計128 MiB、payload 32 MiB、catalog 4 MiB、JSON内包index 8 MiB。stream中も上限確認 |
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
| catalog schema/未来の時計異常 | `WEATHER_CATALOG_INVALID` / `WEATHER_CATALOG_EXPIRED` |
| payload hash/format/decode | `WEATHER_PAYLOAD_INTEGRITY_FAILED` |
| 不正cacheの再取得失敗 | `WEATHER_CACHE_CORRUPT` |
| その他端末内処理 | `WEATHER_PROCESSING_FAILED` |

native GRIB decode/prepared生成はproducerの責務で、失敗時は非zero終了と
library exceptionの`error_type`/messageを出力する。Browserに不完全なcatalogを公開しない。
取得/生成失敗をcoverageに変換しない。consumerは次の限定的fallbackを行う。

- 公開feedに期限切れでも有効なcatalog/payloadが残っていれば取得して検証する。
- fetch/CORS/timeoutまたは一時的なHTTP取得失敗では、端末にcatalogとpayloadが
  両方残る検証済みペアだけを再検証する。payloadがevictされた場合にindexだけで成功扱いに
  したり、端末に保存されていないpayloadをoffline利用可能と表示したりしない。
- schema・時計異常・hash・Run/source identity・format/decode・coverage・サイズ上限の
  失敗はfallback条件にしない。公開catalogのlisting検証エラーも通信失敗と区別する。
- 初期要求だけでなく風依存の反復後の最終時刻・必要変数をpublic
  `MsmClient.prepare_run(..., prepared_data=...)`で検証する。計算が要求する地点・高度は
  public queryの結果を検証し、fallbackでは一つでもUNAVAILABLEなら停止する。
  manual overrideで不足を隠さず、アプリ独自の気象上限・外挿を追加しない。
- 成功時は`weather_delivery`にsafe reason code、選択Run、元の固定Run、refresh意思、
  予報初期時刻・計算時の予報経過秒、catalog生成/期限・検証時刻と取得元を保存する。
  `WEATHER_DELIVERY_FALLBACK`警告にも理由と時刻・経過時間を含める。
  Last Calculationの既存metadata/issue経路で保存・reload後も警告を保持する。
  fresh取得で正常計算が成功すればfallback警告を消す。失敗時はLast Calculationを置換せず、
  入力を変更した計算が成功したと見せない。

通信断でも使える保証はCache Storageへの保存成功と保持に依存する。storage拒否、
quota、eviction、別端末ではペアがない場合があり、その場合は明示失敗する。
このポリシーはMSM内の更新障害に限定した承認済み例外であり、#161 / PR #217の
coverage-only MSM/GSM選択を実装するものではない。

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

#161にはGSM、model priority/coverage fallback、model+Run永続化、2クリック更新を残す。
#144ではMSM単独の取得/transport/public library/WeatherProvider境界だけを実装する。

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
