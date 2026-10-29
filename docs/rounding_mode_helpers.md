# 四捨五入モードの先行helper（#234）

これは既存呼出しに接続していない純粋関数の仕様です。利用者向けモードの実装や
Issue #234 全体の受入完了を示しません。現行動作の正本は
[計算規則](calculation_rules.md)です。

`src/autonavlog/nav/rounding_mode.py` は次の確定部分だけを扱います。

| 関数 | 数値契約 |
| --- | --- |
| `wind_direction_for_calculation(value, enabled=...)` | ONは `[0, 360)` へ正規化し、10° half-up後に360を0へ戻す。OFFは有限な入力をそのまま返す。既定引数なし。 |
| `rounded_dme_nm(value)` | 既存 `DisplayRoundingPolicy.distance()` で0.5 NM表示値を作る。1.25は1.5。 |
| `rounded_toat_c(value)` | 既存 `DisplayRoundingPolicy.temperature()` で1℃表示値を作る。負ゼロは正の0へ戻す。 |

全関数で欠測 `None` は `None` のままです。非有限値は `ValueError` とし、0へ補完しません。
風速・CALM判定は変更せず、欠測風向を使用できるかは既存wind triangleの契約に従います。

既存Python `round_half_up` は `Decimal(str(value))` と `ROUND_HALF_UP` を使い、
TypeScript `roundHalfUp` は絶対値の剰余が半分以上なら加算して符号を戻します。
正のquantumに対して両者とも中間値はゼロから遠ざかる方向です。
`-1.5 → -2`、`-0.5 → -1` となり、ties-to-evenや負方向の切捨てではありません。
Python primitiveの `-0.1 → -0.0` に対し、TypeScript primitiveは正の0へ戻します。
TOAT helperも表示用に正の0へ揃えます。風向は正規化後に丸めるため、
5°は10°、355°および入力-5°は0°です。北の文字列表記（000/360）はこのhelperの対象外です。

テストは既存 `ManualWind` の北正規化、helper、`solve_wind_triangle` を合成し、
丸めた風向を計算入力にした際のTH/MH・GSへの影響と、OFF相当の計算結果不変を確認します。
MHやGSそのものを10刻みに丸めません。DMEとTOATは表示投影だけで、
元の距離や気温を性能計算などの内部値から置き換えません。

## 後段で決めること

- 切替UI、既定値、設定の保存範囲。
- 共通採用風向を、FORECAST/FTD/手入力から全計算・表示・派生経路へ渡す統合。
- DME/TOATのモード外表示、および各表示呼出しへの適用。
- 既存の表示済みMC/WCAからMHを作る算術や、子表示値からの集計（#5/#95）との整合。

現行 `navlog_display.py` の `_temperature()` は0.1℃刻みです。
`_zone_cells()` の採用気温と `_weather_temperature()` の地上気温はこの経路を使うため、
`DisplayRoundingPolicy.temperature()` が1℃でも実TOAT表示への統合は未完了です。
同ファイルは#225の作業対象であり、この先行実装では変更しません。
VOR/DMEの実表示呼出しにも接続していません。
