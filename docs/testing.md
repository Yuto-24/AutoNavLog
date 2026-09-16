# Backendテストのレイヤーと計測

## テストを追加する場所

検証したい仕様を先に決め、その仕様を失敗させられる最も小さい境界を使います。
ディレクトリ名だけで速さを判断しません。数値solverのunit testも高コストです。

- `tests/unit/`: 計算式、丸め、表示投影、入力検証、探索予算、保存adapterのatomicityなど。
  表示テストでは必要なdomain modelを直接構築し、fixture作成だけの
  `CalculationService.calculate()`は使いません。tmp_pathで本物の保存adapterを検証する
  既存テストもこの層にあります。mutableなProjectやsessionを広いfixture scopeで共有しません。
- `tests/integration/`: 計算serviceと性能・気象adapterの接続、RCA/EOC分割、結果の集計、
  facadeと保存・readinessの連携。主要golden、距離・時刻・燃料、既知バグの再発防止は
  実際の計算経路を通します。表示だけの詳細値はunitへ寄せます。
- HTTP integration: status code、cookie、JSON変換、job、owner境界、API経由の復元と
  代表的な計算フローを保証します。同じ入力の正常計算を準備だけのために重複させる場合は、
  同一フローへアサーションを統合できるか確認します。異なる入力条件や失敗経路は残します。
- `tests/contract/`: WeatherProviderとのrequest ID、run固定、単位、型、provenanceの変換境界。
  MSM契約テストはstub clientを使い、実MSMの取得可否を保証するものではありません。
  実データの受入れは[real_msm_acceptance.md](real_msm_acceptance.md)を参照してください。

探索上限などの制御テストでは、下位の距離探索を「実行可能・収束済み」の結果で置換し、
回数上限と失敗時の数値抑制を検証できます。返すべき失敗結果そのものをstubしてはいけません。
風・境界・DME・収束の実数値回帰をそのstubへ置き換えないでください。

Issue番号付きのテストは、名前だけを理由に削除・改名しません。
`test_issue_43_golden.py`は数値回帰、Issue #94の2ファイルは永続化の異なる境界の仕様です。
統合時は旧テストの入力・アサーションの行き先をレビューで確認します。

## 同じ条件で比較する

Dockerのtest stageがサポートするCI検査環境です（[README](../README.md)）。
開発用Python環境で計測する場合も、前後でPython、依存関係、coverageオプション、
実行順とCPU負荷を揃えます。並列の重い検査は避けてください。

```bash
pytest --cov=autonavlog --cov-report=term --cov-report=json:/tmp/coverage.json \
  --durations=30 --junitxml=/tmp/pytest.xml
```

変更前後のJSON/XMLは別名で保持します。総時間だけでなく遅いテスト、
モジュール別時間、失敗・skip、実行されたsource行の差を比較します。
coverageの丸め表示だけでは低下を見落とすため、covered_linesとmissing_linesも確認します。
件数の減少やunitへの移動だけを成果としません。

GitHub Actionsのpytest jobにはDocker build、ruff、mypy、pytest、データ・schema検証、
package buildが含まれます。job全体、`docker run` step、pytest自身のログ時間を区別し、
異なるマシンのローカル時間と直接比較しません。hosted CIは実際の完了状態を記録します。

## Issue #142の棚卸し（2026-09-09）

基準はmain `a32a649`。WSL Ubuntu / Python 3.12.3 / pytest 8.4.2 /
pytest-cov 6.3.0、coverage付きの逐次実行で754件が331.44秒で成功しました。
unit 578件、integration 173件、contract 3件です。

上位のcall時間は次の通りです。

- inbound solverの非grid最適解とcoarse scan比較: 77.06秒。
- 同solverの予報風: 56.60秒。
- 同solverの方位上限290度・下限250度: 52.09秒・49.10秒。
- 本番参照を使うinbound HTTP数値回帰: 37.78秒。
- 方位探索予算をcoarse gridで使い切るケース: 10.32秒。

最初の5件（約272.63秒）は数値回帰として維持します。
`tests/unit/test_rjfm_inbound_guidance.py`全体が約259.44秒を占め、
通常の計算integrationを機械的に削る方針は取りません。

優先調査箇所の判断:

- `test_calculation.py`: 58件・計1.49秒。集計2件はフル計算で作ったSectionResultの
  距離・時間を上書きしていました。`unit/test_navlog_display.py`へ移し、
  型付きの最小入力を直接作ります。RCA/EOC、manual override、気象反復、goldenは維持します。
- `test_web_app.py`: 26件・計8.59秒。FTD正常計算とlast-good復元が同じ入力で重複。
  `test_ftd_calculation_and_last_good_restore_without_cookie_or_server_session`へ
  全アサーションを統合し、1回の実計算でprovider選択、JSON、cookieなし・再起動復元を確認します。
  owner分離、job競合、入力validation、失敗時draft保持などは別のAPI契約として残します。
- Issue #94: 保存adapterの27件は計0.61秒、facadeの18件は計3.93秒。
  Latest置換、破損marker、書込失敗は見かけ上重なりますが、adapterはファイル・atomicity、
  facadeはsession commit・owner・readinessとの連携を保証します。
  HTTP復元もcookieやアプリ再構築を通す代表ケースとして残します。
- `test_release_ci.py`: 25件・計0.02秒。`test_release_preparation.py`: 28件・計0.18秒。
  前者はdiff分類・fragment要否、後者は実ファイル書換え・check無変更・rollbackを保証し、
  同じ仕様の重複としては扱いません。
- 方位探索予算テスト: 数値距離探索を分離し、9回のcoarse評価で停止すること、
  実行可能な候補があっても未収束なら数値を返さないことを直接検証します。
  実距離探索・幾何・予報風と本番参照の回帰は残します。

上記モジュールの合計はJUnitのsetup/call/teardown時間、上位一覧はpytestのcall時間です。
テスト・文書だけの整理のため、Release: not-required契約に従い、VERSION更新はありません。

変更前のhosted CIは[mainのrun 34330643488](https://github.com/Yuto-24/AutoNavLog/actions/runs/34330643488)。
pytest自身は754件・594.46秒、docker run stepは616秒、pytest jobは674秒でした。

変更後は753件・321.46秒で成功し、単発の同条件比較では9.98秒（3.01%）短縮しました。
探索予算テストは10.324秒→0.011秒、集計2件は合計0.039秒→0.002秒、
統合したFTD HTTPフローは合計0.899秒→0.399秒でした（JUnit時間）。
件数差の1件はHTTP統合によるもので、元のアサーションは維持しています。
複数回の統計的ベンチマークではなく、他の数値探索時間には実行ごとの揺れがあります。

coverageは前後とも10,144 / 11,334行（89.5006%、表示90%）、missing 1,190行。
全sourceファイルのexecuted_lines集合を比較し、失われた行・追加行とも0でした。
branch coverageや実データの正しさまでこの数値だけで証明するものではないため、
主要golden・数値回帰も削除せず全suiteで実行しています。

## Issue #154: inbound solverの関数profile（2026-09-11）

基準はmain `aab2dab`（#142 / #151と#152のRelease契約移行を含む）。
Python 3.12.3 / pytest 8.4.2 / pytest-cov 6.3.0 / coverage 7.15.4 /
GeographicLib 2.1、同じWSL host・依存関係で逐次実行しました。
重い検査を並行実行せず、coverage付き全suiteの時間と、coverageなしの段階タイマーを
分離しています。関数時間は計測オーバーヘッドを含み、性能比較の秒数とは別です。

### 支配的な処理

対象5件の段階タイマーによる累積時間（秒、変更前→変更後）。親子関数の時間は重複するため加算しません。

- 非grid解とcoarse scan: solver 25.477 → 18.410、route metrics 23.140 → 16.052、
  区間距離15.334 → 8.183、clearance 4.960 → 4.993、
  sampling 1.875 → 1.896、DME再構築1.846 → 1.874、
  風補正0.060 → 0.060。方位66件・距離2,574件。
- 予報風: solver 18.727 → 13.494、route metrics 17.041 → 11.770、
  区間距離11.229 → 5.972、clearance 3.689 → 3.649、
  sampling 1.391 → 1.408、DME再構築1.340 → 1.373、
  風補正0.063 → 0.064。方位54件・距離1,937件。
- 下限250度: solver 16.212 → 11.635、route metrics 14.731 → 10.143、
  区間距離9.737 → 5.165、clearance 3.160 → 3.148、
  sampling 1.196 → 1.201、DME再構築1.177 → 1.196、
  風補正0.039 → 0.038。方位42件・距離1,649件。
- 上限290度: solver 17.249 → 12.353、route metrics 15.667 → 10.775、
  区間距離10.370 → 5.482、clearance 3.363 → 3.350、
  sampling 1.268 → 1.279、DME再構築1.266 → 1.266、
  風補正0.041 → 0.041。方位45件・距離1,762件。
- 本番参照HTTP: solver 10.080 → 9.102、route metrics 7.983 → 6.997、
  区間距離2.316 → 1.447、clearance 2.116 → 2.043、
  sampling 2.466 → 2.415、DME再構築1.736 → 1.744、
  風補正0.053 → 0.052。方位39件・距離1,889件。

非gridケースは本探索1回（方位57件・距離2,239件）と独立したcoarse比較9回です。
変更前の本探索22.096秒、coarse比較計3.381秒。HTTPは自動1,500 ft計画のNO_SOLUTION
（1.024→1.002秒）と手動2,500 ft計画のAVAILABLE（9.056→8.100秒）の2 solveです。
HTTP call全体は10.569→9.601秒、solver以外のAPI・計画更新・polling等は約0.49→0.50秒。
HTTPテストの削除やstub化で解消する問題ではなく、両計画の実計算とstatus/JSON/数値を残します。
samplingの段階タイマーは候補経路の生成が対象で、request入口の直接route検査はsolver総時間に含みます。

全15 solveで、入力・全出力field・各段階の呼出し回数・評価したbearing/distanceの順序hashが
前後完全一致しました。各段階の累積時間がその呼出しのsolver時間以下であることも確認済みです。
段階計測はcoverageなしで5件が88.40→65.65秒。coverage付き全suiteとは別の実測です。

主因は、方位→距離→raw/rounded経路→最大0.5 NM区間という反復により、
`_route_distances`が膨大な回数の`geodesic_leg`を呼ぶことです。初期の合成ケースcProfileでは
非grid本探索だけで約38万回の`geodesic_leg`を実行していました。
距離だけを使うのに、毎回WGS84 inverseの後でLineを構築し、Positionで中点も算出して破棄します。
段階タイマーでは合成4件の区間距離再計算はsolver時間の約60%、変更後は約44%です。

### 変更判断と維持する保証

`_route_distances`だけを既存の距離・初期方位用WGS84 inverse関数へ変更します。
同じinverseの`s12 / 1852`を使い、中点のLine/Positionと不要な結果object構築だけを省きます。
中点が必要な通常の`geodesic_leg`やroute samplingは変更しません。

- 方位の5度coarse grid、直接方位候補・近傍細分化、最大64評価、0.001度収束、
  距離17 seed・最大96評価・0.001 NM収束、DME丸め、最大240 NMを維持。
  予算を使い切ったときのCONVERGENCE_FAILUREと数値抑制も維持します。
- samplingを粗くせず、各chordのinverse・0.5 NM制約・240 NM制約・local domain検証・
  numeric guard・境界接触/交差/clearanceを維持。
  sampling距離を理論上の等分値で代用せず、実際の座標間距離を検証し続けます。
- bearing/distanceの同一候補は既に探索内dictで再評価を避けています。
  coarse比較の独立9 solveは非grid最適解に対するoracleなので残します。
  解の検出を弱める探索予算削減や早期打切りは採用しません。
- immutableなpolygon準備は再利用候補ですが、合成ケースでは0.05〜0.09秒、
  HTTPでも約0.25秒で支配的ではありません。今回はcacheを追加しません。
  同じ丸めDMEでもraw距離によって二分探索のbracketが変わり、最終座標は必ずしも同一では
  ありません。DME値だけをkeyにした丸め結果cacheは同値性を保証できません。
  requestを跨ぐcacheも風・高度・boundary/revisionの取り違えやメモリ保持を増やすため不要です。

### 再現する方法

同じcheckoutでpackage metadataも整合させてください（例: `uv pip install --python .venv/bin/python --no-deps -e .`）。
今回の初回試行は古い1.7.0 metadataによるversion testだけが失敗し、数値回帰は成功しました。
その試行を正式な成功baselineとせず、metadataを現行ソースに合わせて取り直しています。

全suiteは上記「同じ条件で比較する」のcoverage/JUnitコマンドをbefore/afterの別名で実行します。
profilingは別プロセスで次のように実行します。outputは新しいdirectoryを指定します。
比較する両revisionで同じ計測スクリプトを使用してください。今回の再計測では、変更前の
幾何module全文を`git show aab2dab:src/autonavlog/application/rjfm_inbound_geometry.py`で読み、
一時プロセス内でmodule namespaceへロードしてから同じスクリプトを実行しました。
他のsolver/test/依存は同一で、worktreeのソースは書き戻していません。

```bash
.venv/bin/python scripts/profile_rjfm_inbound.py --output /tmp/inbound-profile-before
```

`solutions.json`でtest/request/resultとsolver時間、段階ごとのcalls/seconds、
評価したbearing/distance列のSHA-256を確認できます。`test-times.json`はpytest call全体です。
元の関数を実行するwrapperのタイマー・カウンターをthread-localに保持し、HTTP workerでも
外側solverと各段階を同じthreadで計測します。各段階は親子で重複するため加算しません。
計測器の多重設定・solverの再入はエラーにし、終了後にinstrumentationを解除します。
前後でtest/request/result・各段階の呼出し回数・evaluation_traceを完全一致で比較します。
計測時間そのものをテストの合否条件にはしません。

初回のcProfileは合成4件のボトルネック発見に役立ちましたが、HTTP workerのprofileに
非再帰solverが再入扱いになる異常があり、独立レビューで指摘されました。
そのHTTP関数内訳を採用せず、最終的な5件の比較は上記タイマーで取り直しています。

### coverage付き全suiteの比較

成功baselineは731件・316.24秒、変更後は736件・261.00秒で、55.24秒（17.47%）短縮。
単発の同一host比較であり、安定した短縮率の保証ではありません。
対象5件のJUnit時間（setup/call/teardownの合計）は次のとおりです。

- 非grid解とcoarse比較: 76.100 → 61.009秒。
- 予報風: 56.208 → 44.385秒。
- 下限250度: 48.315 → 38.172秒。
- 上限290度: 51.758 → 40.808秒。
- 本番参照HTTP: 37.983 → 35.413秒。

coverageは前後とも10,144 / 11,334行（89.5006%）、missing 1,190行。
幾何moduleも235 / 248行・missing 13行で同一です。変更による行番号移動を対応付けて
全sourceのexecuted_linesを比較し、変更していない行のcoverage欠落は0でした。
新しい距離取得呼出しも実行されています。branch coverageや実データの正しさそのものを
この比率だけで証明するものではありません。
既存golden・数値回帰・toleranceは維持し、追加5件はゼロ長・半NM・RJFM周辺・緯度上限/下限で
旧WGS84距離との完全一致と中点計算がないことを確認します。

### 同PRでのRouteNodeRole整理

通常のルート作成・OMARU追加は`ROUTE_POINT`を使い、`TURN_POINT`を生成する本番経路はありませんでした。
削除前にこの環境の保存領域を読み取り確認し、稼働volumeのJSON 490件、他の保存volume 4つの
JSON計133件、ローカル保存フォルダのJSON 149件で、role/point_roleに該当する実例は0件でした。
他環境や外部に持ち出したデータまで調査したという意味ではありません。
この結果と削除方針に基づきenum/schema/設計書から除去し、互換変換やmigrationは追加しません。
専用のroleテストはなく、参照していたgolden・EOC・性能補間・参照データ保存テストは別仕様の
保証なので、入力roleだけを`ROUTE_POINT`へ置き換え、数値assertを維持します。
上記の性能比較はこの整理より前の測定であり、enum削除による高速化を主張するものではありません。

## Destination TAF Proxy (#145)

`npm --prefix services/taf-proxy ci`後に`npm --prefix services/taf-proxy test`と
`npm --prefix services/taf-proxy run check`を実行する。Node unitに加え、公式workerdで
module export、CORS、cache、native rate binding、redirect拒否を検証する。
`npm --prefix web run test:application`はBrowser network adapterのquota / timeout / size検査も含む。
Python `tests/integration/test_local_calculation.py`はTAFの成否による航法値不変を確認する。

Static Browser回帰は`VITE_TAF_PROXY_URL=https://taf.example/taf npm --prefix web run build:local`で
buildし、静的配信後に`npm --prefix web run test:taf`を実行する（必要なら`AUTONAVLOG_LOCAL_URL`）。
このsuiteはProxy通信をfixtureへ置換し、成功→quota→timeout→停止→reload→復旧と
IndexedDB Project / Last Calculation保持を確認する。通常の`test:local`はTAF未設定buildで実行する。
実upstream smoke、Free tier見積りと本番release gateは[TAF Proxy運用](taf_proxy.md)を参照する。
