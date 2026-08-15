# 計算規則

この文書は、取得済み一次資料で確認できた要件と、AutoNavLog固有の計算Policyを分けて
記録します。一次資料の同定、版、SHA-256および未確認事項は
[一次資料監査](primary_source_audit.md)を参照してください。

参照した公式原典は次の2件です。

- 航空大学校『学生訓練実施要領 単発事業用課程』改正19（2026-06-24）第8章
  8-(2)〜8-(5)、別添8-1。
- Cirrus Design SR22 AFM/POH P/N 13772-006 Revision A1。性能頁5-30〜5-34は
  LOEP上Reissue A。

以下で`規程`は要領で直接確認した事項、`POH`は取得済み性能頁で直接確認した事項、
`実装Policy`は資料がexactな方法を指定していないAutoNavLogの選択を表します。
資料で一部を裏付けられても、性能CSV、適用機、Golden NAV LOGとの照合が終わるまでは
アプリ全体を公式準拠とは扱いません。

## 規程で確認した計画・記入要件

- 方位は1°、距離は0.5 nm、時間は0.5分単位で記入します。規程はexact half-way時の
  丸め方向を指定していません。
- 各区間のSEAは、予定経路の両側3 nm以内の最高障害物上端+1,000 ftを100 ft単位で
  切り上げた値です。SEAと当日の天候等を考慮して巡航高度を決めます。
- 上昇に必要な時間、燃料、距離は飛行規程第5章から500 ft単位で算出します。
- 巡航性能は、計画に近い気圧高度、気温、出力のうち不利な条件を採用します。宮崎Cruiseは
  65% Best Powerで、飛行規程第5章からTASを求めます。
- 上昇TASは、出発空港標高と巡航高度の概ね中間高度の気温、CAS 111 ktから求めます。
  RCAまでのETEはPOHの上昇時間です。
- RCA距離は上昇TASと予想風から求めたGS（風を予想しない場合はTAS）と上昇ETEから
  算出します。
- 巡航は巡航高度の予想風とTASからGSを求め、ETEを算出します。
- 宮崎DescentはCruise airspeed、500 fpmです。降下TASは巡航CASを使用し、EOCから
  目視位置通報点までの高度差と500 fpmからETEを求めます。風は両高度の概ね中間高度で
  予想します。
- EOC距離は降下TASと予想風から求めたGS（風を予想しない場合はTAS）と降下ETEから
  算出します。
- 目視位置通報点から目的空港までは、中間高度の気温とCAS 121 ktからTASを求め、無風で
  ETEを算出します。
- WCAは飛行経路と予想風から求め、符号付きで記入します。右WCAを正とする向きは国土交通省
  航空局の公開学科試験資料でも確認しています。
- 別添8-1の主表には、FROM、TO、PA、TOAT、CAS、TAS、TC、VAR、MC、WIND、WCA、MH、
  ZONE/CUM DIST、GS、ZONE/CUM ETE、ETO、ATO、ATE、SECT/REM FUELを記入します。

## 規程で確認した燃料計画

- `TAXI/RUN UP`: 10分、1.5 gal。
- `CLIMB`: 離陸からRCAまでをPOH「Time, Fuel, & Distance to Climb」から求めます。
- `CRUISE`: 宮崎課程はPOH「Cruise Performance」から求めます。
- `DESCENT`: 宮崎課程はEOCから目的地空港まで一律12 GPHです。別の操作要領として、
  Descent前にFuel Flowを18 GPHへsetする記載がありますが、計画燃料値は12 GPHです。
- `ADDITIONAL`: 着陸所要時間・空中交代等として10分、2.8 gal。
- `TGL`: 場周1回につき7分、2.0 gal。
- `RESERVE`: 45分、12.4 gal。
- `MIN REQUIRED`: TAXI/RUN UP、CLIMB、CRUISE、DESCENT、ADDITIONAL、TGL、
  RESERVEの合計です。
- `EXTRA`: TOTALからMIN REQUIREDを引き、16.5 GPHでenduranceへ換算します。

## POH性能頁で確認した条件

- 上昇表（pp.5-30〜5-31）はFull Throttle、Mixture per Section 4、Fuel Density
  6.0 lb/gal、Weight 3600 lb、無風の条件で、海面から各高度までのTime/Fuel/Distanceを
  掲載しています。
- 同上昇表は、標準より10℃高いごとに計算値へ10%を加えること、start/taxi/takeoffへ
  1.5 galを加えることを注記しています。
- 巡航表（pp.5-32〜5-34）はWeight 3400 lb、無風の条件で、Pressure Altitude、
  RPM、MAPごとにISA -30℃、ISA、ISA +30℃のPWR/KTAS/GPHを掲載しています。
- 現行repoでは全行転記、出典頁、SHA-256、原典からの独立再抽出と差分比較を完了した
  CSVだけを実計算に使用します。対象機への適用性、表外条件、補正、省略列および
  Golden NAV LOGとの一致は別途確認対象です。

## AutoNavLogの現行実装Policy

- 測地線はWGS84、Courseは初期真方位、Legの自動気象照会点は測地線上の中点です。
  これは実装Policyです。
- Variationは各物理Legの出発点緯度から自動判定します。32.0°N以上（境界を含む）は
  +8°E、32.0°N未満は+7°Eです。Phase境界で計算行が分割されても元の物理Legの
  出発点を使います。保存済みProjectの`default_variation_deg_east`はschema互換のため
  読み込みますが、新しい計算値には使用しません。
- 東偏差を正として`MC = TC + VAR`、右WCAを正として`MH = MC + WCA`とします。
  計算結果には未丸め値を保持します。転記表示のMHだけは、同じ行へ表示する1°単位のMCと
  WCAを加算して3桁表示し、`291 + (-4) = 287`のように転記欄同士の関係を保ちます。
- NAV LOG計算では`PA = MSL`とし、計画MSL高度をそのままPA、POH性能検索、CAS/TAS換算へ
  使用します。QNH補正後PAや別の500 ft planning PAは作らず、QNHを必須入力またはBlockerに
  しません。保存済みの手動QNH fieldはschema互換のため残しますが計算には使用しません。
- 現行G6上昇表は原表19節点を高度方向に線形補間した500 ft刻みISA行を収録し、
  出発・巡航の累積値の差へ、中間気圧高度のISA温度に対する正の温度差1℃ごとに
  1%（10℃ごとに10%）を一度だけ加えます。標準以下
  では減算しません。高度軸だけ表端から500 ft以内の外挿を許可します。10℃ごとの増加は
  POHで裏付け済みですが、比例適用、標準以下の扱い、補間・外挿は実装Policyです。
- 巡航表は65% PWRに対してPWR方向、ISA偏差方向、高度方向の順で区分線形補間し、
  KTAS/GPHを0.1単位のties-to-evenで確定します。各軸の上下限・係数、PWR補間corner、
  参照頁を結果へ残します。表外は外挿せず、手入力した高度・TOATまたは65% PWRを挟む
  cornerがない場合は最寄りの表端条件を採用して計算を継続し、採用した軸を
  `CRUISE_*_TABLE_BOUNDARY_USED` 警告と結果metadataへ残します。この警告は転記前の
  確認を必要とします。
  この順序・丸めはIssue #15添付の多次元拡張表6,419行と一致確認済みですが、規程の
  「計画に近い条件のうち不利」へのexactな適合性とGolden NAV LOGは未検証です。
- 降下は直前巡航CAS、500 fpm、12 GPHを使用します。EOC→VREP時間は
  `（巡航高度 - VREP高度）/ 500 fpm + 1分`です。降下Legで不足する時間だけを直前の
  1物理Legへ持ち越し、それより前へ出る場合は`EOC_BEFORE_SUPPORTED_LEG` Blockerとします。
  中間変針点に独立した高度制約は置きません。
- 目視位置通報点以降はCAS 121 kt・12 GPH、CALM固定で、WCA=0、GS=TASとします。
  CAS、降下率、燃料流量、到着区間CALMは規程で裏付け済みです。
- 目的地TAFの風は、出発予定時刻へ計算済み累積ETEを加えた到着予定時刻に合わせて
  取得します。取得した風は独立した`DESTINATION_INFO`行への表示だけに使い、到着区間の
  ETE、燃料、WCA、MH、GSへは反映しません。到着区間の計算親行はCALM、WCA=0、
  GS=TASを維持します。`DESTINATION_INFO`行は目的飛行場標高、同地点のMSM予想気温、
  TAF風だけを表示し、その他の航法・距離・時間・燃料セルは意図的な空欄です。
- RCA/EOCは採用距離軸上の算出位置で物理LegをCalculation Zoneへ分割します。EOCと物理
  変針点の距離差が0.5 NM未満なら内部計算上も変針点へsnapし、`<TP名> / EOC`と表示します。
  ちょうど0.5 NMではsnapしません。このsnap規則をCheck Pointへは適用しません。
  Check Point、RCA、EOC、物理終点は未丸めのalong-route distance順に並べ、表示丸めで
  前後関係を変えません。分割後もZone距離合計と`DIST = GS × ETE`を保存します。
- `CalculationOutcome.sections`は重複しないCalculation Zoneです。`display_rows`はそこから
  作る表示専用投影です。分割の有無にかかわらず各通常Physical LegへFROM/TOを持つ
  `PHYSICAL_LEG_SUMMARY`と最低1つの`CALCULATION_ZONE`内訳行を置きます。小計の
  ZONE DIST/ETEは配下Zoneの未丸め合計、CUMは小計行だけに表示します。内訳行のFROMと
  CUMは空欄です。最終`VISUAL_ARRIVAL`だけは親計算行と`DESTINATION_INFO`行に分け、
  通常内訳行を作りません。各Physical Legグループの後には`LEG_SEPARATOR`を1行置きます。
  `display_rows`の親小計・目的地情報・区切りは表示専用であり、距離・時間・燃料の集計へ
  使いません。
- `NavLogDisplayCell.state`は表示の意味を明示します。`DISPLAY_VALUE`はcanonicalな値、
  `INHERIT`は親または直前行の計算値を継承しながら表示は完全な空欄、`BLANK`は継承しない
  意図的な空欄、`STATE_SYMBOL`は`↗`/`↘`等、`UNAVAILABLE`は本来必要な値の取得・算出失敗です。
  `INHERIT`と`BLANK`へ`未取得`、`未確定`、`—`を表示しません。`UNAVAILABLE`だけを
  `未取得`として太字・赤系背景で表示します。WebとA4転記補助HTMLは同じcellの`text`を
  使用し、各rendererで航法値を再計算しません。
- 親行はLeg開始時の既定値を表示し、子行はZone固有値、Check Point固有値、親または直前の
  表示値から変化した値だけを表示します。同値を使う子セルは`INHERIT`です。出発Legの動的な
  CLIMB/CRUISE性能値は親へ集約せず、対応する内訳行へ表示します。
- PA表示は計算高度値とは別の表示種別を持ち、数値、上昇`↗`、降下`↘`、推定通過高度
  `(<高度>)`、空欄、未取得を区別します。推定通過高度は高度制約ではなく、EOC後の経過時間と
  500 fpmから求める表示用結果です。
- `LOSS`は機上修正値であり地上入力UIを持ちません。旧Projectの非0値もZONE/CUM ETE、
  TTL TIME、Forecast、燃料、fingerprintへ加えず、転記補助のETOは空欄にします。
- 同じForecast Runで最大5回反復し、代表時刻差30秒未満を収束とします。これは
  MSMの時刻依存値を扱う実装Policyで、規程にはありません。
- 内部値は丸めず、表示時にhalf-upで方位1°、距離0.5 nm、時間0.5分、燃料0.1 galへ
  丸めます。PAは整数、TOATは0.1℃、CAS/TAS/GSは1 kt、TC/MC/MHは3桁、VAR/WCAは
  正値へ`+`を付け（0は`0`）、WINDは`DDD/kt`または`CALM`で表示します。親と子は同じ
  未丸め値をそれぞれ独立に丸めるため、表示上の子合計と親表示が0.5単位だけ異なる場合が
  ありますが、内部未丸め小計は必ず一致させます。方位・距離・時間の単位は規程で確認済み
  ですが、half-upのtie方法、燃料の一律0.1 gal、MH転記値を表示済みMC/WCAから作ること、
  および丸め時点は実装Policyです。
- v2.6ではSEA・DEM・陸域マスクを計算対象外とします。旧ProjectのSEA fieldは読込互換
  だけに残し、計算、Issue、fingerprint、status、画面、転記補助HTMLのいずれにも
  使用しません。
- 不明値は後続も未確定とし、0、1013.25 hPa、最近傍気象へ暗黙にフォールバックしません。

## 未確認事項と使用制限

- 対象機の製造番号、日本承認AFM/POH、Supplementに対するP/N 13772-006性能頁の適用性。
- 性能CSV全行の原典再抽出・差分比較と確定hashは完了しています。対象機への適用性、
  省略した性能補正、およびGolden NAV LOGとの一致は未確認です。
- 飛行地域の偏差、気象・航空情報・障害物の利用者確認。SEAは本版の対象外です。
- MSM風・気温の採用、空間/時間補間、Forecast Run選択と反復の校内承認。
- `PA = MSL`、性能補間/外挿、巡航候補選択、LOSS、丸めtie処理のexact適合性。
- 別添8-1原本とのレイアウト同一性。AutoNavLogのA4出力は原本ではなく非公式転記補助表です。

RCA/EOCが全経路端を越える場合はBlockerです。RCAが経路内の最初の変針点を越える場合は
Warningとします。これらの安全側ゲートも実装Policyであり、校内Golden NAV LOGとの照合が
終わるまで公式規則とは表示しません。
