# Issue #125: Pyodide Calculation Core

通常利用のLegacy FastAPI経路は変更しません。開発設定
`VITE_CALCULATION_MODE=local` のCalculation Coreを拡張し、FTDと取得済みFORECASTを
同じPython `CalculationService` で実行します。起動は
[Local起動手順](local_calculation_poc.md#起動と切替)のままです。
MSMの実予報取得・TAF取得を含むStatic Webのproduction移行完了を意味しません。
現行の端末内Project保存は[#124 Local Persistence](local_persistence.md)、同一タブreloadは
[#119 Application Session](application_contract.md#frontend-application-session--lifecycle-119)を参照してください。

## 実装範囲

- 既存のPhysical Leg、Calculation Zone、RCA/EOC、Performance補間・provenance、
  手動override、燃料・残量、Check Point、RJFM出発/到着例外、Warning/Blocker/Unavailable、
  NAV LOG表示投影を共用します。Calculationのrewriteや別の仕様はありません。
- `LocalApplication`のFTD限定拒否を解除し、FORECASTは固定MSM fixtureを渡します。
  `FixtureMsmClient`は取得済み配列を既存`PreparedForecast`へ渡す検証用接続です。
  Runの互換性・選択は既存`select_compatible_runs` / `MsmClient.resolve_run`、
  気象補間は既存`PreparedForecast`、単位・結果変換は既存`MsmWeatherProvider`を使います。
  discovery/download、GRIB decode、cache、Run一覧のnetwork adapterはありません。
- 同梱Performance/Referenceは既存repositoryのmanifest/hash/validation条件で読みます。
  正式なwheel filenameを使い、micropipで`deps=False`のインストールを行います。
  必要依存は既存の固定runtime・package指定とPyodide公式NumPyで供給します。
  wheelとdata.zipは同じbuildのmanifestにSHA-256を記録し、使用前に検証します。
  これは整合性検出であり、署名による配布元認証や#121のcache policyではありません。
- PyodideのPydantic 2.10.5では、before validator後のstrict enumがJSON文字列を
  Python入力と扱う差異がありました。RJFM案内のJSON読込時だけenumを復元します。
  Python入力での文字列enum拒否、数値・bool・未知値の拒否は維持します。
- Worker/Comlinkの順次実行と既存失敗表示を維持します。Local失敗をLegacyへfallbackせず、
  既存のlast-good NAV LOGを保持します。進捗UI/cancelは追加していません。

## MSM fixtureとFORECAST再現

`tests/fixtures/msm/manifest.json`に原URL、変数属性・単位・scale/offset、Run ID、
NPZのSHA-256を記録しています。2026-09-13 JSTにRISHの固定予報URLから取得した
**実JMA MSM由来データ**です。FakeWeatherProviderやFTD風をMSMと呼び替えていません。

- Run: `20260912000000` / `20260912030000`（00 / 03 UTC）。
- 範囲: 31.5–34°N、130–132°E。気圧面26×17、地上51×33格子。
- 有効時刻: 2026-09-12 03–06 UTC（12:00–15:00 JST）。
  気圧面は03/06 UTC、地上は03/04/05/06 UTC。
- 気圧面: 1000/975/950/925/900/850/800/700/600/500 hPaのz/u/v/temp。
  地上はtempのみ。高度m、風m/s、気温Kのdecoded float64配列を保持します。
- netCDF4 1.7.4の標準scale/offset処理で抽出し、NPZへ保存しました。
  runtimeにnetCDF4・pygrib・ecCodes・xarrayを導入せず、NumPyで必要配列だけ読みます。
  原NetCDFとGRIBの独立同等性や本番配信の信頼性は#144へ残します。
- `scripts/capture_msm_fixture.py`は固定fixtureの保守用取得手順です。build/test/runtimeから
  呼びません。上流の保持期間後も、通常の回帰はチェックイン済みfixtureだけで再現できます。
  SHA-256は抽出fixtureのhashであり、全国原NetCDF/GRIBのhashとは主張しません。

通常のReact UIで、DATEを**2026-09-12**、ETD JSTを**12:00**、気象モードを
**予報気象**にし、`tests/fixtures/issue_43_golden.kml`を取り込みます。
経路確定後、RJFM/米ノ津/玉名の計画高度を6,500/7,500/6,500 ftにしてNAV LOGを作成します。
最新compatible fixture Run（03 UTC）を選び、TTL DIST **125.5 NM**、TTL TIME **0:50**を表示します。
画面の気象source欄は固定fixtureと対象日時を明示します。範囲外時刻・地点を
最新気象、最近傍気象、FTDへ補完しません。FTD Goldenは従来の**125.5 NM / 0:57**です。

TAF providerは接続しません。目的地情報行は`TAF_PROVIDER_DISABLED`の未取得表示です。
航法計算の到着区間は既存どおりCALMで、TAFの成否がWCA/MH/GS/ETE/Fuelへ入りません。

## Run固定と差分比較

新規FORECASTは互換Runを選択します。保存済み00 UTC Runを既存repositoryで保存・復元し、
03 UTC Runが利用可能でも00 UTCで再計算して`FORECAST_UPDATE_AVAILABLE`を出すことを
Python/Pyodideで確認します。保存済みRunが非互換ならBlockerになり、最新Runへ切り替えません。
この#125保存試験は既存repositoryを一時filesystem上で使ったものです。
現行のLocal画面では#124で端末内への明示保存が可能となり、#119で同一タブreload時の作業を復元します。

`localGolden`と既存#117 Goldenは維持します。追加の`calculationCoreGolden`はcanonical
outcome全体（Section/Derived Point/表示cell/Performance metadata/Warning等）を比較します。
ランダムProject/Node/Section/Check Point UUIDは対応する入力順へ正規化し、
UUID依存の`generated_against_fingerprint`値だけを正規化し、そのfieldの存在と
安定したReference fingerprintは完全一致で比較します。順序・構造・状態・code・表示文字列は完全一致、
内部floatは**絶対差1e-8以下**で、相対誤差や拡大toleranceを使いません。
既存fuel/ETE/DISTセルの`effective_value`に格納された内部floatの文字列ペアは数値へ戻して
同じ条件で比較し、画面に使う`text`は完全一致を維持します。

| 保証対象 | 主な回帰 |
| --- | --- |
| RCA/EOC、snap/backtrack、phase別override、Fuel、Unavailable | `tests/integration/test_calculation.py`、既存nav/performance unit tests |
| Run選択/固定/更新/coverage、query failure/欠損 | 既存Calculation/Weather tests、`tests/unit/test_msm_fixture.py` |
| RJFM UMK物理/OMARU仮想RCA、到着OMARU→UMK、参照不整合 | 既存`test_rjfm_*` unit/integration tests |
| Pyodide JSON復元・strict validation | `test_guidance_json_roundtrip_keeps_strict_enum_validation`とBrowser RJFMケース |
| FTD/実MSM FORECAST→React NAV LOG、fallbackなし、last-good保持 | `web/e2e/local-calculation.spec.ts`の通常UI tests |
| 保存Run復元、UMK/OMARU出発、RJFM到着、手動TAS/風・1000 fpm・RUN UPなし・TGL・CP | 同specの実Pyodide Worker代表ケースとPython Reference |

細かなWeather異常系をBrowserへ重複展開せず、既存Python suiteを仕様coverageの主とします。
Test専用Workerは同じbuildのwheel/dataと公式Pyodideを使い、既存Python facadeを呼びます。
本体にdebug endpointやfixture専用Calculationを追加していません。

## 検証・残条件

再現コマンド:

```bash
source .venv/bin/activate
python3 scripts/local_reference.py --forecast
python3 scripts/local_reference.py --forecast --pinned
pytest
npm --prefix web run typecheck
npm --prefix web run build:local
# 別terminalでstatic artifactを配信後
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4175 npm --prefix web run test:local
```

Chromium自動検証は実機Safari確認を代替しません。**#125はiPhoneまたはiPad Safariで
上記FORECASTを1回以上完走するまでcloseしません。** 端末/OS/browser、完走結果、
OOM・予期しないreload・crashの有無をPRへ記録してください。秒数の固定SLOはありません。

Python Core・desktop Reference・既存Goldenを移行期間中維持します。廃止/削減判断は#146より
前に行いません。#118のApplication境界全面整理、#124の永続化、#144の本番MSM取得、
#145のTAF/Proxy、#121のStatic production、#123の配信CI、#146のLegacy削除、PWA、
#159/#160の進捗/cancel、#165/#166のSafari UXは本変更に含めません。

## 2026-09-13 JSTの計測

#117と同じWSL / i9-13900K、headless Chromium 151.0.7922.34、1440×1000、
CPU/network throttlingなしで、production artifactの通常UIを測定しました。
Comlinkのrequest→response（serializationを含む）を計測し、各モードで計算6回・reload1回。
初回は新しいBrowser contextで、OS/CDN cacheのcold状態は保証しません。
同hostでDockerのPython回帰も実行中でした。固定SLO・統計的benchmarkではありません。

| 項目 | #117記録 | #125 FTD | #125 FORECAST |
| --- | --- | --- | --- |
| 初回Worker起動 | 2.91 s | 3.39 s | 3.22 s |
| reload後Worker起動 | 2.46 s | 2.70 s | 2.74 s |
| 初回計算RPC | 91 ms | 90 ms | 175 ms |
| 再計算5回 | 76–97 ms | 73–88 ms | 142–166 ms |
| 計算後renderer RSSのsample範囲 | 479–488 MiB | 465–553 MiB | 469–522 MiB |

FTDの計算時間に重大な回帰は観測しませんでした。起動に約0.5秒、RSSの最大sampleに
約65MiBの増加があり、NumPyと正式package installationを追加したコストとして記録します。
どちらのmodeも5/6回目にはRSSが減り、増加し続ける状態、OOM、予期しないreload、crashは
観測しませんでした。これはrenderer全体のsampleで、Python heap単独・peak・実機Safariの
memory保証や長時間のleak不在の証明ではありません。
計算RPC中のrAFは継続し、最大gapは約50ms、同区間の50ms以上のmain-thread Long Taskは0件。

Local artifactは**16,503,543 bytes**（#117の16,225,543 bytesから278,000 bytes増）。
data.zip 10,313,302 bytes、AutoNavLog wheel 250,505 bytes、MSM wheel 27,770 bytesです。
別途CDNからNumPyを取得し、初回の圧縮response bodyは約3.04 MBでした。完全offline・配信/cache最適化・低memory端末回帰は
後続Issueで扱い、#125の実機Safari FORECAST確認は引き続き未実施です。

実施済みチェックはDocker test stageの747 tests、ruff/mypy、Performance/Reference検証、
schema整合、sdist/wheel build、Web typecheck・release-notes 6 tests・Legacy/Local build、
Chromium Local E2E 8件です。Independent Reviewのstrict Python legacy-input指摘を修正後、
関連37 testsとPyodide代表ケースを再検証しました。
Local Composeは1.11.6を`127.0.0.1:8123`で起動し、`/healthz`と
既存`autonavlog_autonavlog-data` volumeの保持を確認しています。
Safari実機FORECASTとhosted CI/reviewの結果はPRを正本とします。

KML/KMZのcontent / metadata importとBrowser依存機能の境界は
[#120 Platform Capability](platform_capabilities.md)を参照してください。
