# 四捨五入モード（#234）

NAV LOGのTTL DIST／TTL TIMEを含む結果サマリーの後で「通常／四捨五入」を選び、
Local／Legacy共通の計算入口で再計算します。ラジオ入力が現在の選択を示し、
選択済みの項目を押しても再計算しません。
初期OFFは互換維持の暫定既定です。現行動作の正本は[計算規則](calculation_rules.md)です。
ローカル実装・検証と、PR／hosted CI／公開の受入は別の段階です。

`src/autonavlog/nav/rounding_mode.py` は次の数値規則を扱います。

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

## 計算・表示への適用

- `CalculationService._section_weather()` はFORECAST／FTDの補間後、手入力の採用後に
  風向を丸めます。この共通採用風向を上昇・巡航・降下の風力三角形に渡します。
- SectionResultにも同じ丸め規則の採用風向を格納し、NAV LOGとRJFMの派生案内が参照します。
  VREP以降の航法CALM固定、目的地TAFの独立表示は維持します。
- `navlog_display.py` の `_temperature()` 自体はOFF互換の0.1℃刻みです。
  ONでは実際に構築した各行のTOAT文字列を1℃へ投影し直します。
  地上気温・採用気温のeffective値、継承／欠測状態は変更しません。
- VOR/DMEはTypeScriptの `formatVorRadialDistance()` でON時のみ0.5 NM half-upにします。
  測地計算の内部距離とradialは維持し、OFFは従来の0.1 NM表示です。
- 手入力風向・気温は元の入力を保持し、ON時の非編集表示は採用表示値を使います。
  フォーカス中は元の値を編集できます。
- #5/#95の表示済みMC/WCAからMHを作る算術、子表示値からの距離・時間・燃料集計は維持します。
  #225のEOC継承変更は別branchの担当範囲であり、その継承条件の行は変更しません。

## 一時設定のライフサイクル

ユーザー確認により、モードは同じ作業中だけ保持し、新規作業・Project読込・再読込でOFFへ戻します。
Projectモデル、保存schema、設定の保存方式は追加しません。

モードはWebSessionに保持し、計算要求の一時オプションとWebStateの `roundingMode` で受け渡します。
既存の結果provenance（風向の `automatic_metadata.rounding_mode`）でON結果を識別します。
既存の結果保存にこの計算条件が含まれても、次回の設定として採用しません。
ON結果を復元した場合、保存データは保持したまま、メモリ内の結果を未計算にしてOFFでの再計算を必要とします。

同じ作業の通常保存、失敗したProject読込の巻き戻し、Legacyの401後の透過的なセッション再作成は、
一時オプションとして現在のモードと対応する結果を一緒に引き継ぎます。
保存設定として永続化する範囲・方式は引き続き未確定です。

## 切替中と失敗時

切替は既存の計算中UIを使い、処理中・未保存／不正な入力・自動再計算中の重複切替を抑止します。
成功したWebStateでモードと結果を一緒に確定し、表示だけを先に切り替えません。
例外だけでなく、丸めにより風力三角形が解けなくなるなどblocker結果の場合も旧モード・旧結果を保持します。
既にONの作業が別の編集で計算不能になった場合は、OFFへの再計算を試せます。
端末保存失敗後に計算自体が確定済みなら、既存のcommittedState経路でモードを含む確定状態を反映します。

Gemini CLIがこの環境では利用できないため、新規UI文言はユーザー指定の「四捨五入モード」「通常／四捨五入」のみです。
処理中・成功・エラーは既存文言を再利用します。
