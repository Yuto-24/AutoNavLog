# Architecture

AutoNavLogはNotebook、application service、domain、adapterの順に依存します。Notebookや
Google Drive、MSMの型を計算コアへ持ち込みません。

```text
AutoNavLog.ipynb / ipywidgets
        |
ProjectService / CalculationService / ForecastService
        |
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
