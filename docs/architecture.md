# アーキテクチャ

AutoNavLogはReact SPA、FastAPI、application service、domain、adapterの順に依存します。
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
Policy version、Forecast Runから同じ結果を生成します。保存対象はProject入力であり、
CalculationOutcomeと気象問い合わせ結果はWeb session内の現在値として扱います。

MSMのGRIB2、RISH URL、NetCDF、気圧面配列は`jma-msm-wind`だけが扱います。AutoNavLogの
MSM adapterは単位、時刻、型、request ID、表示ラベルを変換します。固定中の
`jma-msm-wind==0.2.1`が公開queryを持たない地上気温だけは、adapter内の隔離した
compatibility branchから正規化済み`_surface_scalar`を呼びます。計算コアへその内部表現を
公開せず、補間来歴を通常の`WeatherResult.metadata`へ保存します。

ProjectがFTDモードの場合、Web facadeは実気象adapterの代わりにProject内の地上風・
5,000 ft風から`FtdWeatherProvider`を組み立てます。計算コアは通常の`WeatherProvider`契約だけを
参照するため、気象要求、反復、手動overrideの経路を分岐させません。

`DestinationTafProvider`は、計算完了後に目的空港と到着予定時刻を受け取り、
AviationWeather.govのTAFから卓越風を選びます。結果はWeb sessionへ保持するとともに、
NAV LOGの独立した`DESTINATION_INFO`行へ表示します。到着区間のWCA、MH、GS、ETE、
燃料には反映せず、同区間はTAF取得成否にかかわらずCALMで計算します。通信やTAF時刻範囲の
不一致はNAV LOGのBlockerにせず、目的空港情報行の風を`UNAVAILABLE`として表示します。
FTDモードではTAFを取得せず、同欄へ`FTD_MODE_NO_TAF`を明示します。

`CalculationOutcome.sections`は重複しないCalculation Zoneであり、距離・時間・燃料の
唯一の集計元です。`display_rows`は`sections`から作る表示専用投影で、Physical Leg小計、
継承空欄、PA記号、目的空港情報、Leg間区切りを含みます。Reactは
`NavLogDisplayCell.text`を表示し、display projectionから合計を作り直しません。
