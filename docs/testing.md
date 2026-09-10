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
