# アーキテクチャ

React SPAは共通Application contractを呼び、Legacy HTTPまたはLocal Browser Adapterへ接続します。
[Frontend Application境界](application_contract.md)を参照してください。以下はLegacy側の構成です。
WebやMSM固有の型を計算コアへ持ち込みません。

```text
React SPA
        |
FastAPI Web facade / ProjectService / CalculationService / ForecastService
        |                                      |
        |                              DestinationTafProvider
        |                                      |
Project / AdoptedValue / CalculationOutcome
        |
nav + performance policies
        |
WeatherProvider           ProjectRepository
   |                             |
Msm/Fake/FTD adapter       Local adapter
```

`CalculationService.calculate()`はProjectをdeep copyし、同じ入力、性能データversion、
Policy version、Forecast Runから同じ結果を生成します。以下のWeb sessionはLegacy内部の状態です。Frontendのタブ単位reload復元は
[Application Session](application_contract.md#frontend-application-session--lifecycle-119)を参照してください。
Legacyでは
Project入力と最後に正常完了した計算はlocal repositoryにも永続化し、新しいsessionで復元します。

Project保存は4層に分けます。

- `project.json`: 明示保存したrevision付きcheckpoint。
- `autosave.json`: server validationを通過した最新draft。読込時はcheckpointより優先します。ownerごとに
  autosave-only Projectは1件だけ`Latest`として一覧・明示読込・削除の対象にし、次の正常なdraft commit後は
  古いautosave-only Project directoryを自動削除します。
- `last-calculation.json`: schema v1の1件固定レコード。計算時Projectのdeep snapshot、
  `CalculationOutcome`、目的地風、Forecast Run・metadata、計算fingerprint、保存時刻を保持します。
- `owners/{owner-key}/state.json`: schema v2で、ownerごとの`last_opened_project_id`、
  `latest_draft_project_id`、削除再試行対象を保持します。owner keyはdomain-separated SHA-256で作り、
  生identityをpathへ含めません。v1の単一markerは安全に移行します。

Project mutationは、更新後Projectを検証してautosaveへatomic writeし、owner stateのLatest/route-confirm時の
last-opened更新も同じmarker writeで確定してから、成功した場合だけsessionへcommitします。新しいLatestを
確定してからだけ旧autosave-only Projectをbest-effortで削除し、失敗時は一覧から隠してmarkerのretry対象へ
残します。明示保存は同じdraftをautosaveした上でcheckpointのrevisionを進めます。checkpoint確定後の
Latest marker整理が失敗しても、checkpoint/revisionを巻き戻したり保存APIを失敗扱いにしたりしません。
計算完了時は、
effective BlockerのないREADYまたはwarning結果だけをself-containedなlast-goodとしてatomic replaceし、
同じProjectをautosaveします。例外、中断、blocked outcome、単なる編集は既存last-goodを消しません。

新sessionはowner marker、owner一致確認、autosaveまたはcheckpoint、last calculationの順で復元します。
Projectの明示読込も同じ経路を通ります。現在draftのfingerprintが計算時snapshotと異なる場合、
NAV LOGは直前の正常結果として維持し、`RECALCULATION_REQUIRED`かつ
`calculationIsCurrent=false`とします。RJFM数値案内はさらに現在の参照identityとも一致する場合だけ
表示します。markerが欠落・破損・owner不一致ならProjectを自動選択せず、blank sessionとowner内の
Project一覧を返します。

各保存pathはUUIDまたはopaque owner keyから決定し、root逸脱とsymlinkを拒否します。書込は一時fileの
検証後にreplaceし、read-backも検証します。壊れたautosaveは有効なcheckpointへfallbackし、壊れた
last calculationは固定名へ隔離してgeneric warningとserver logを残します。Project削除は明示操作だけで、
一致するowner markerもclearします。

Composeの`autonavlog-data` named volumeがProject repositoryを保持します。同じCompose projectで
`git pull`、image build、`docker compose up -d --force-recreate`を行ってもLast draft、last-opened、
last-good calculationは復元できます。`docker compose down -v`またはvolumeの明示削除はこの永続状態を
削除します。

MSM / GSMのmodel spec、Run discovery、coverage、GRIB2 decode、補間、provenanceは
`jma-gpv-weather==0.5.0` の公開APIを正本とします。AutoNavLogのadapterは型、request ID、
単位境界、既存の入力検査と表示metadataを変換します。地上気温も公開
`SurfaceTemperatureQuery` を使用し、private compatibility branchは廃止しました。
Localでは静的供給された `MsmPreparedData` / `GsmPreparedData` をWorkerで必要時に取得・検証し、
Legacyと同じモデル別Providerへ接続します。MSM優先・coverageだけを理由にしたGSM切替、
固定model + Runと2クリック更新、最終Requirementでの全体再計算はアプリの責務です。
[Forecast selection](forecast_selection.md)にpolicy、fingerprint、provenanceを定義します。
Calculation CoreへHTTP、Browser storage、source URL選択を追加しません。
[Local Weather Adapterと静的feedの運用](local_weather.md)を参照してください。

ProjectがFTDモードの場合、Web facadeは実気象adapterの代わりにProject内の地上風・
5,000 ft風から`FtdWeatherProvider`を組み立てます。計算コアは通常の`WeatherProvider`契約だけを
参照するため、気象要求、反復、手動overrideの経路を分岐させません。

`DestinationTafProvider`は、計算完了後に目的空港と到着予定時刻を受け取り、
AviationWeather.govのTAFから卓越風を選びます。結果はWeb sessionとlast-good計算recordへ
保持するとともに、
NAV LOGの独立した`DESTINATION_INFO`行へ表示します。到着区間のWCA、MH、GS、ETE、
燃料には反映せず、同区間はTAF取得成否にかかわらずCALMで計算します。通信やTAF時刻範囲の
不一致はNAV LOGのBlockerにせず、目的空港情報行の風を`UNAVAILABLE`として表示します。
FTDモードではTAFを取得せず、同欄へ`FTD_MODE_NO_TAF`を明示します。

`CalculationOutcome.sections`は重複しないCalculation Zoneであり、距離・時間・燃料の
唯一の集計元です。`display_rows`は`sections`から作る表示専用投影で、Physical Leg小計、
継承空欄、PA記号、目的空港情報、Leg間区切りを含みます。Reactは
`NavLogDisplayCell.text`を表示し、display projectionから合計を作り直しません。

Local CalculationはPython / Pyodideを採用し、Static Webを主方式として継続します。
採用根拠・性能/外部気象の制約・後続Issueは
[Architecture Decision](local_calculation_poc.md#architecture-decision-2026-09-13)を参照してください。
上記FastAPI構成は移行中のLegacy runtimeとして維持します。

取得済みFORECASTを含むPyodide Coreの適用範囲・固定MSM fixture・Run固定・
Reference維持条件は[Calculation Core](local_calculation_core.md)を参照してください。

## Local durable Repository (#124)

Local Browser mode uses the IndexedDB Repository described in [local_persistence.md](local_persistence.md).
Project checkpoint/draft and Last Calculation are independent of ephemeral tab recovery and disposable
Weather cache. The server persistence semantics above remain Legacy-only.

## Platform Capability (#120)

[Platform Capability境界](platform_capabilities.md)へFile / Clipboard / 外部URL /
通信状態と保存先アクセスを集約します。起動時にBrowser実装とfile picker/drop controlを
注入し、Domain / Calculation Coreは依存させません。Session復元方針と#124の
Project / Latest / migration / transaction semanticsはそれぞれ既存境界に残します。
