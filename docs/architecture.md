# アーキテクチャ

AutoNavLogはNotebook、application service、domain、adapterの順に依存します。Notebookや
Google Drive、MSMの型を計算コアへ持ち込みません。

```text
React SPA / AutoNavLog.ipynb
        |
FastAPI Web facade / ProjectService / CalculationService / ForecastService
        |                                      |
        |                              DestinationTafProvider
        |                                      |
Project / AdoptedValue / CalculationOutcome / Snapshot
        |
nav + performance policies
        |
WeatherProvider      ProjectRepository
   |                        |
Msm/Fake adapter     Drive/Local adapter
```

`CalculationService.calculate()`はProjectをdeep copyし、同じ入力、性能データversion、
Policy version、Forecast Runから同じ結果を生成します。気象問い合わせと結果はSnapshotへ
保存され、後日同じSnapshotを表示するときに再問い合わせしません。

MSMのGRIB2、RISH URL、NetCDF、気圧面配列は`jma-msm-wind`だけが扱います。AutoNavLogの
MSM adapterは単位、時刻、型、request ID、表示ラベルを変換するだけです。

`DestinationTafProvider`は、計算完了後に目的空港と到着予定時刻を受け取り、
AviationWeather.govのTAFから卓越風を選びます。結果はWeb sessionの参考表示だけに保持し、
`CalculationOutcome`、Snapshot、航法計算、出力可否には渡しません。通信やTAF時刻範囲の
不一致もNAV LOGのBlockerにはしません。
