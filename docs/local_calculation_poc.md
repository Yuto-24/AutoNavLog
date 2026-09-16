# Issue #117: Pyodide checkpoint

> #144以降の実MSM取得・cache・静的feed運用は [Local Weather](local_weather.md) を参照してください。
> 以下の固定fixture記述と計測は #125 時点の履歴です。通常のLocal production経路はfixtureを使いません。

> この文書のcheckpointは履歴です。#125によるFTD/FORECASTの拡張・現行制約・
> Safari実機確認条件は[Calculation Core](local_calculation_core.md)を参照してください。
> 現行のSession復元は[#119のApplication境界](application_contract.md)、端末内保存は[#124のLocal Persistence](local_persistence.md)を参照してください。

今回はPython / PyodideによるKML → 経路候補選択・確定 → FTD入力 → 計算 →
NAV LOG表示の検証用checkpointです。#117を完了・採用決定とは扱いません。

## 起動と切替

Legacyは従来どおりDocker Composeの `http://127.0.0.1:8123`。
Localはビルド時の開発設定 `VITE_CALCULATION_MODE=local` で明示選択します。
通常画面には方式選択を追加していません。Local画面のヘッダーは
`Pyodide Local PoC` と `端末内に保存` を表示します。

WSLでrepository rootから、Python 3.12の開発venv（build/setuptools/wheelを含む）を有効にします。

```bash
source .venv/bin/activate
npm --prefix web ci
npm --prefix web run dev:local
```

開発画面は `http://127.0.0.1:5174`。predevで最新Python wheelと実データを準備します。
Pythonはビルド時だけ必要で、配信後の計算はブラウザ内で実行します。

production artifactは次の手順で配信できます。静的HTTP配信にFastAPI・Docker・API proxyは不要です。

```bash
source .venv/bin/activate
npm --prefix web run build:local
python -m http.server 4174 --bind 127.0.0.1 --directory web/dist-local
```

`http://127.0.0.1:4174` を開きます。任意の静的HTTP配信へ同じartifactを置けます。
ファイルを直接開く `file://` は対象外です。本番hostingの選定・deployは行いません。

試す手順:

1. `tests/fixtures/issue_43_golden.kml` を選択またはKMLを貼り付けます。
2. 地図と候補を確認し、「地図とKML記載順を確認しました」をチェックして経路確定。
3. FTD風を地上360°/15 kt、5,000 ft 270°/30 ktにします。
4. RJFM発6,500 ft、米ノ津発7,500 ft、玉名発6,500 ftを入力。
   大牟田VREPは通常の自動計画2,500 ft、佐賀場周は同梱masterの1,000 ftです。
5. 「NAV LOGを作る」でRCA・EOC・TTL DIST・TTL TIMEと各区間を確認します。
   Goldenの日付は2026-09-11、ETDは09:00 JSTです。

## 再利用と最小境界

- `CalculationService`、KML importer、route normalization、FTD provider、
  readiness、NAV LOG display projectionをPythonのまま再利用します。
- `autonavlog.local.LocalApplication` は既存facadeの操作を呼ぶ小さな接続です。
  現行UIの操作名に `/api/` を残していますがLocal時はHTTPを実行しません。
  Application contract全体の整理は#118に残します。
- Pyodide公式npm package、`loadPyodide` / `loadPackage` / `unpackArchive`、
  micropipを使用。独自loader・archive parser・runtimeは作っていません。
- WorkerはUI threadを計算から離します。標準WorkerだけではPromiseのRPCと
  例外伝搬を提供しないため、小さな既存library Comlink 4.4.2を追加しました。
  mutationは同じWorker上で順番に実行し、例外は既存UIのエラー表示へ伝えます。
- 通常のsetuptools wheelを、Web staticの不要な同梱物だけ除いたsourceから作成します。
  PerformanceとReferenceは実データをzipで同梱し、既存repositoryで検証・読込します。
  Golden数値や専用経路は実装へ埋め込んでいません。
- 既存ProjectRepositoryはPyodide標準MEMFS上の一時directoryで再利用します。
  この#117 checkpoint時点ではBrowser storageへmountせず、明示保存は無効でした。
  現行では#119のSession復元と、[#124のIndexedDB Repository](local_persistence.md)が
  この一時runtimeとは独立して保存・一覧・読込・削除を担います。

## Runtimeと制約

Pyodide 0.27.7（Python 3.12.7）を固定しました。現行Python packageの
`>=3.12,<3.13` に合わせるためで、最新runtimeへの追従とは別判断です。
[公式changelog](https://pyodide.org/en/0.27.7/project/changelog.html)、
[公式package loading](https://pyodide.org/en/0.27.7/usage/loading-custom-python-code.html)
を参照してください。

主要packageはPyodide配布のPydantic 2.10.5 / pydantic-core、tzdata 2024.1、micropip 0.9.0、
純Python wheelのdefusedxml 0.7.1 / GeographicLib 2.1です。
これらは既存Python依存またはBrowserで必要な配布方法を使っています。
Pydanticのnative extensionは通常のdesktop wheelを流用せずPyodide同梱buildを使います。

`autonavlog.web.__init__` のeager importがFastAPI・認証・MSM初期化を引き込むため、
公開entry pointを遅延importにしました。facadeの型注釈だけの認証・prewarmer依存は
TYPE_CHECKINGに移しました。FastAPI、uvicorn、PyJWT/crypto、MSMはLocal経路へ
loadしません。これはこれらの全packageがPyodide非対応という評価ではありません。

初回はjsDelivrのPyodide runtimeとPyPIの純Python wheelを取得するためnetworkが必要です。
完全offline・Service Worker・cache管理は未実装。CDNやasset取得失敗はUIへ伝播します。
Loader失敗はそのsession内で自動retryせず、再読込で再試行します。
Workerの致命的エラー・10分の処理timeoutも失敗として示します。ユーザー操作による
計算キャンセルや新しい進捗UIはありません。

#117 checkpoint時点ではFTDのみ対応し、FORECAST・KMZ・Project保存を明示的に拒否していました。
現行では#125でFORECAST、#124でProject保存、[#120でKMZ](platform_capabilities.md)に対応しています。
地図tileは従来の外部配信を利用します。LocalでAPIへ自動fallbackしません。
既存LegacyのAPI・認証・永続化は維持します。

## 再現可能な検証

`tests/fixtures/issue_117_ftd.json` が入力です。
`scripts/local_reference.py` はLocal bindingを使わず、Legacy Python facadeへ
通常のimport/confirm/update/calculateを渡してReferenceを生成します。
`issue_117_ftd_golden.json` はその結果の意味的投影です。

`web/e2e/helpers/localGolden.ts` はPhysical Legとnode座標、Calculation Zoneの全主要値、
RCA/EOC座標、表示cell、燃料、TTL、warning/blockerとpolicy/data revisionを比較します。
ランダムUUID・時刻・fingerprintは比較対象外。表示文字列・状態・コードは完全一致、
内部数値の絶対許容差は1e-8（各fieldのnative単位）です。丸めて差を隠しません。

```bash
source .venv/bin/activate
python scripts/local_reference.py > /tmp/issue117-reference.json
python -m pytest tests/integration/test_local_calculation.py tests/unit/test_version.py
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run build:local
# 別terminalで上記static serverを起動してから:
cd web
npm run test:local
# 開発serverにも同じbrowser testsを実行できる:
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:5174 npm run test:local
```

Browser testはAPI通信を監視し、`**/api/**` をabortしたまま通常UIを完走します。
Workerの実際のComlink返信をtest側で観測し、production専用debug APIは作りません。
1,100 pxの入力→経路→準備→NAV LOG順序、1,440 pxの三列配置も座標で確認します。
asset取得失敗と未対応気象の失敗表示も回帰対象です。正常計算後にtest側で
Comlink requestへPython validation例外を注入し、エラー表示とNAV LOGの完全保持を検証します。
Workerの致命的停止後は既存draft保存の失敗表示になり、同じNAV LOGを保持します。
Python integrationはCalculationServiceの例外時にもlast-goodが保持されることを検証します。
強風200 ktのFTDケースでは、非空のWarning/Blockerを含む結果もPython Referenceと比較します。

## Checkpoint時点で保留した判断

PR #162時点で保留した実機評価・性能計測・MSM/TAF feasibility・最終採用判断は、
下記Architecture Decisionで整理しました。Rust/WASM・TypeScript PoCは実施しません。
#118/#119/#120/#121/#124/#125の本格移行、PWA、完全offline、KMZ、
進捗UI(#159)、キャンセル(#160)、本番配信はこのcheckpointに含めません。

## Checkpointで確認した結果

- Docker test stage: ruff、mypy、pytest 743件、Release source、Performance/Runtime data、
  schema整合、sdist/wheel buildが成功。Pythonの計算仕様は変更していません。
- Web typecheck、release-notes 6件、DockerのLegacy production buildが成功。
- 静的productionとViteのGolden・失敗・配置testsが各5件成功。
  強風Warning/Blocker差分testも追加し、静的productionとViteで成功しました。
- GoldenはTTL DIST 125.5 NM、TTL TIME 0:57。全比較項目が上記許容差内で一致。
  強風caseはWIND_TRIANGLE_FAILEDなどのBlockerとITERATION_NOT_CONVERGEDなどの
  Warningをそのまま表示し、成功へ置き換えません。
- APIをabortし、Browser context内のWorkerを含むrequestを監視して0件を確認。
- Local wheelは約249 KB、同梱data zipは10,066,340 bytes。
  このほかにPyodide runtimeとpackageのnetwork転送が必要です。
- 1,100/1,440 pxの座標検証と画面確認を実施。Linux/WSLのPlaywright Chromiumでの
  自動検証であり、Windows Chromium/iPhone Safariの正式な実機評価ではありません。
- 別モデルによる独立レビューで指摘されたLegacy favicon欠落と計算後失敗のbrowser
  coverage不足を修正し、再レビューで未解決指摘なしとなりました。

初期host検証では旧インストールmetadataのversion testが1件失敗しましたが、
既存uvで1.11.4へ更新後のversion testsは成功しています。root所有の既存web/distへ
host buildを書き込めなかったため、Legacy buildはDockerで検証しました。
これらはPyodideの不成立理由ではありません。


## Architecture Decision: 2026-09-13

**採用: Python / Pyodide。Static Web / Local-firstを主方式として継続する。**
これは#117のArchitecture Gateの完了判断であり、production移行完了ではありません。
根拠コードはmain `958273d1452b97b56076335757646bcf56401fcd`（PR #162）。
本判断の調査・計測でapplication code、dependency lock、計算仕様は変更していません。

### 採用理由と実機結果

- CalculationService、KML importer、FTD provider、facade、実Performance/Airport/Reference dataを
  再利用し、別言語への仕様移植を避けられます。Worker上で同じPython coreを実行します。
- [利用者の実機報告](https://github.com/Yuto-24/AutoNavLog/issues/117#issuecomment-5647015646)と
  Issue #117への今回の依頼により、Windows Chromium、iPad Safari、iPhone Safariで
  KML→経路確定→FTD→Local計算→NAV LOGの完走を確認済みです。
  実機の型番・OS/browser version・秒数・RSSは未採取です。下記数値を実機Safariの測定値とはしません。
- Safariの初回結果スクロール #165 とClipboard確認UI #166 は別UX Issueです。
  NAV LOG結果自体は表示されており、方式採用のblockerではありません。
- static artifact + HTTP配信でFastAPI/Dockerなしに動作。今回も既存Local E2E 6件が成功し、
  API遮断下のGolden、失敗表示、強風Warning/Blocker、Python Reference比較を確認しました。
  #43経路由来の#117 Goldenは数値abs 1e-8、表示文字列・code完全一致を維持します。
- PR #162最終CI run `34701396993` の4 jobsとCodeRabbitは成功、merge後mainの
  `34702145322`（test）/ `34702145339`（release）も成功しています。
  途中cancel runを成功に数えていません。Local browser E2Eのhosted CI化は#123に残します。
- 先行候補が成立すれば後続候補は不要という#117の条件に従い、Rust/WASMと
  TypeScript/JavaScriptの追加PoCを実施しません。これらを技術的に不成立と評価したわけではなく、
  追加rewrite・別実装の保守費用に見合う未解決の採用blockerが見つかっていないためです。
- Native shell評価を開始する理由もありません。広いOS/browser回帰は#122です。

### 性能・memory・配布量

2026-09-13 JST、WSL Ubuntu / Intel i9-13900K（32 logical CPUs）、Playwrightの
headless Chromium 151.0.7922.34、1440×1000、CPU/network throttlingなしで測定しました。
`build:local`の成果物を`python -m http.server 4174 --bind 127.0.0.1 --directory web/dist-local`
から配信。新規Browser contextで初回起動後、通常UIでGolden入力を行い、計算6回、同contextで
1回reloadしました。初回とはBrowser cacheが空の意味で、OS/CDNのcold状態は保証しません。
固定SLOや統計的benchmarkではありません。

- 初回Worker request開始→最初のstate応答: **2.91秒**。runtime/package取得、展開、Python import、
  data準備を含みます。navigation開始からは3.07秒。reload後の同区間は**2.46秒**（navigationから2.49秒）。
  HTTP cacheがあってもPython VMとMEMFSは再作成します。継続実行時はruntime再loadしません。
- Golden計算RPC（serializationを含む）: 初回**91ms**、再計算5回**76–97ms**。
  計算ボタン操作開始から応答・table表示確認までは初回214ms、再計算約1.06–1.09秒でした。
  後者はPlaywrightのactionability待ち・既存UIの保存処理等も含み、pure calculation時間ではありません。
- 計算RPC中もrequestAnimationFrameが動作し、観測gapは最大50ms、同区間の50ms以上の
  main-thread Long Taskは0件。初期表示全体には96msと153msのLong Taskがあり、
  「常に60fps」や「UI遅延ゼロ」は主張しません。通常UI操作と結果表示は完走しました。
- CDP `SystemInfo.getProcessInfo`でrenderer PIDを得てLinux `/proc/<pid>/status`のVmRSSを採取。
  初期化後**384MiB**、計算後の各sampleは**479–488MiB**、6回目は483MiB、reload後466MiB。
  これはUI・Worker・WASM・共有mapping等を含むrenderer全体のRSSで、Python heap単独や
  iPhone memory量でも、連続監視したpeakでもありません。6回目で減少しましたが、これだけで
  長時間のmemory leak不在を証明しません。OOM・予期しないtab reload・crash・browser終了は観測なし。
  約0.5GiBのdesktop footprintは小さくないため、長時間・複数tab・低memory端末は#122/#125で継続確認します。
- artifactの非圧縮ファイル合計: Legacy **5,886,128 bytes**、Local **16,225,543 bytes**、
  差分**10,339,415 bytes（9.86MiB）**。Local wheel約249KB、data.zip 10,066,340 bytesが主要因です。
  初回に別途CDN/PyPIから約**7.6MiB**の圧縮response bodyを取得しました（runtime/packageとmetadata）。
  同一originのLocal assets約9.84MiBと合わせ約17.4MiB。共通UI/font/mapはこの合計から除外します。
  Playwright `request.sizes()`のcold responseBodySizeで集計。cache hit時に負値を返したため
  reloadの転送量は数値化していません。cache方針やSafari転送量の保証ではありません。
- Legacy buildも成功し、LocalClient/Worker chunk・Pyodide文字列・Node externalize warningなしを再確認。

再測定は既存`web/e2e/local-calculation.spec.ts`の通常入力を使用し、testのWorker観測と同様に
Comlink request IDの送受信を`performance.now()`で対応付けます。`/api/state`が初期化、
`/api/calculate`が計算の境界です。`requestAnimationFrame`と`PerformanceObserver('longtask')`、
CDPのrenderer PID、`request.sizes()`を併用します。画面へのdebug APIやbenchmark frameworkは追加しません。
一時probeはapplicationへ組み込んでいません。実機で詳細測定できなくても、上記の再現可能な計測と
利用者の実機完走報告を分けて採用判断に使用します。

### MSM feasibility（#144への引継ぎ）

現行`vendor/jma_msm_wind-0.2.1-py3-none-any.whl`の`MsmClient.prepare_run`は、
RISHの`http://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/YYYY/MM/DD/`をlistingし、
要求時刻を覆うrunの`Lsurf` / `L-pall` GRIB2を取得します。Rangeは途中downloadの再開用であり、
地域subset downloadではありません。全量取得後にpygrib/ecCodesで地域・時刻を切り出し、
xarray/h5netcdfで正規化NetCDFを保存。NumPyでHGTに基づく鉛直・水平・時間補間を行います。

2026-09-12 00UTC runのFH00–15を少量Rangeで検証しました。
地上69,241,393 bytes、気圧面50,522,465 bytes、合計**119,763,858 bytes（114.2MiB）**。
長い時間窓では追加ファイルが必要です。これはGolden FTDがdownloadする量ではありません。
`Range: bytes=0-15`に206 / 正しいContent-Rangeが返り、GRIB magicを確認。
`Accept-Ranges: none`と実挙動が矛盾するため、#144ではheaderだけで可否を判断せず、
206・Content-Range・長さを検証してください。複数rangeや条件付きrangeは未検証です。

**現行sourceのBrowser直接取得は採用できません。** 通常ChromiumでHTTPS originからHTTP URLへ
fetchするとMixed Content、localhost HTTP originからはACAO不在によるCORS拒否でした。
HTTPSへ置換した同URLは今回のChromiumで`ERR_CERT_AUTHORITY_INVALID`、WSLのcurl/Pythonでも
証明書chain検証に失敗。TLS検証を無効化して成功扱いにはしていません。
HTTPのlistingとGRIB取得は200/206で、観測したrequestにredirectや認証はありません。
HTTPS originの検証用HTMLはPlaywrightでローカル生成したもので、公開siteへのdeployではありません。

pygrib/ecCodesはPyodide 0.27.7の公式package indexに存在せず、desktop native wheelを流用できません。
urllibの同期socket、disk cache/lockもBrowser acquisitionへそのまま移せません。
一方、NumPy 2.0.2、netcdf4 1.7.2、h5py 3.12.1、xarray 2024.11.0は公式indexにあります。
「MSM package全体をmicropip installすれば完了」とは扱わず、既存のquery/補間・provenanceを
再利用し、取得・decode・cache責務を#144で接続します。

代替候補は[RISHの予報NetCDF](http://database.rish.kyoto-u.ac.jp/arch/jmadata/gpv-latest.html)を
必要なbyte rangeだけHTTPSの限定中継で取得する経路です。解析値再構成archiveではなく、
runが固定できるlatest配下の予報を対象にします。CDF1形式を確認し、`MSM-P.nc` / `MSM-S.nc`は
当日の観測で187,313,156 / 198,217,744 bytes。全量をMEMFSへ展開する方式は推奨しません。

小規模検証では`20260912/MSM2026091200P.nc#mode=bytes`を標準netCDF4 Python libraryで読み、
2時刻×10面×58緯度×53経度、z/u/v/tempを取得できました。scaled配列は合計1,721,440 bytes、
検証用float64 CDF1ファイルは1,967,632 bytes。Pyodide公式netcdf4でそのsubsetを読み、
変更していない`msm_wind/interpolation.py`のbilinearを実行し、Pythonと
`-0.33944955260250004`で一致しました。これはdecode・補間再利用のsampleであり、
Browserから上流へ直接取得した証明やForecast全体のGRIB同等性試験ではありません。

地域subset取得にはdesktopでも約63秒を要しました。多数の細かいrangeの素朴な発行は
実用実装にせず、#144で要求の集約・必要時刻/領域・cache・上限を検討する必要があります。
地上も505×481格子と変数の存在を確認しましたが、地上subsetの完走・wind/temperatureの
全Reference比較は#144に残します。全国float配列とGRIB/NetCDFの同時保持はmobileのmemory負荷を
大きくするため、subset単位で上限を持つことが重要です。

最小Serverlessは取得先・run・rangeをallowlistして中継する候補で、GRIB decodeやNAV LOGを
Functionへ載せません。HTTP upstreamには改竄検出上の限界があり、TLS chainが正常な取得元の確保、
信頼できる配布/provenance、上流の利用条件はproduction化前に確認が必要です。
RISHは教育研究向け提供と、一部NetCDF欠測時は原本を使う旨を案内しています。
公式リンク先OPeNDAPは今回DNS解決できず、成立した代替経路に数えていません。

結論: **Static Webを断念するblockerは確認されず、MSM取得方式は#144で変更が必要**。
Range取得と小subsetの標準decode/既存補間は成立。Browserの非同期fetchとreaderの接続、
CORS中継、実機memory・速度、netCDF scale/offset/時間/高度/格子と元GRIBの同等性、
無料枠内のrequest数は未実装・未保証です。これらをFTD Local Calculationの不成立と混同しません。

### TAF feasibility（#145への引継ぎ）

現行`DestinationTafProvider`は`https://aviationweather.gov/api/data/taf?ids=RJFM&format=json`
をurllibで取得。1MiB上限・timeout・5分cache・同時fetch制限を持ちます。
Browserの通常fetchはACAO不在でCORS拒否。一方同URLをサーバー側から取得すると200 JSON、
3,116 bytes、認証不要、redirectなしでした。提供元の[API仕様](https://aviationweather.gov/data/api/)
もCORS非許可、100 requests/minute、scope/frequency制限を明示しています。

必要endpointとICAO/queryだけを許可した最小HTTPS Serverless Proxyで成立する見通しです。
検証はupstream接続とBrowserの拒否条件までで、本番Proxyの実装・deploy・負荷試験はしていません。
TAFの選択/時刻解釈は既存Python処理を再利用できますが、thread/socket取得部分は置換対象です。
Proxy停止・timeout・quota超過ではTAFだけUNAVAILABLEとし、ProjectやLocal計算は影響を受けません。

[Cloudflare Workers Freeの現行上限](https://developers.cloudflare.com/workers/platform/limits/)は
2026-09-13確認時点で100,000 requests/day、CPU 10ms、memory 128MB、外部subrequest 50/request。
network待機はCPU時間に含まれません。小さなresponseの限定中継とcacheなら有料planを必須としない
見通しです。例として100人×20回/日=2,000 proxy requests/dayは日次枠の2%ですが、これは想定例で
実利用予測ではなく、burstや公開abuse、上流100/minuteを別途制限します。Free planのままfail closedで
運用し、上限超過時の自動課金・有料planへの自動移行は前提にしません。#123/#145でrelease時に
現行limit・cache・timeout・size・abuse対策を検証します。MSMの全国decodeをこの無料CPU枠に
載せる見積りではありません。

### 引継ぎとAcceptance Criteria

Pythonは採用したCalculation Coreのsourceそのものとして維持します。desktop Python Referenceと
既存Golden/境界/数値testも#125で主要pathを展開する間は維持し、Browser differentialを追加します。
Core Pythonの削除は予定しません。Legacy FastAPI/Dockerの廃止は#146で、Local回帰・保存・気象の
代替が揃ってから別判断します。Reference testsの削減も今回承認していません。

Pyodide 0.27.7/Python 3.12とのversion整合、native packageは公式WASM buildが必要という制約、
CDN/PyPI依存、再読込でMEMFSを失うことは継続します。wheelの正式filename/installation semanticsと
artifact/data整合・cache更新は#125/#121/#123で扱います。完全offlineは採用の前提ではありません。

#117のACは順に、(1)static起動、(2)通常UI完走、(3)HTTP fallbackなし、(4)通常KML/FTD経路、
(5)実data、(6)Golden一致、(7)失敗表示はPR #162と今回の既存6 testsで充足。
(8)Windows・(9)iPhoneは利用者の実機報告（iPadも完走）、(10)実測は上記の環境・限界付き計測、
(11)外部気象は直取得の阻害条件と代替のfeasibility確認、(12)採否判断は本節で充足。
(13)Pyodide不成立時の次候補、(14)全候補不成立時の記録は条件不成立につき適用不要です。
全方式を実装した・全実機で秒数を測ったというcheckにはしません。

次の主要Issueは**#125**です。#118/#119/#124/#120はApplication/Session/Persistence/Capability、
#144/#145は外部気象、#121/#122/#123はproduction/全browser回帰/CIと無料配信、
#159/#160は進捗/キャンセル、#165/#166はSafari UXを担当します。本変更ではいずれも実装しません。
