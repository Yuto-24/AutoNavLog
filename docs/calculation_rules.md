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
- 東偏差を正として`MC = TC - VAR`、右WCAを正として`MH = MC + WCA`とします。
- QNHは、検証済みMETARの観測QNHへ同じMSM Forecast Runの海面更正気圧変化量を
  加えた`METAR_TREND_CORRECTED`を優先します。METARが欠測、不整合、または2時間超の場合は
  `MSM_MSLP_ONLY`を使います。どちらも取得できない場合は、1013.25 hPaや古い値で補わず、
  手動QNHを要求します。推定値は公式の飛行場QNHではありません。
- PAの未丸め値と500 ft計画値を分け、現行Policyは`CEILING`です。規程の「500 ft単位」は
  確認済みですが、未丸めPAのexactな500 ft選択方法は未確認です。
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
- 降下は500 fpm、12 GPH、目視位置通報点以降はCAS 121 kt・12 GPHとし、取得できた目的地TAF風を使用して計算します。取得不能時だけCALMへフォールバックします。
  CAS、降下率、燃料流量は規程で裏付け済みですが、目的地TAF風の採用は実装Policyです。
- 目的地TAFの風は、出発予定時刻へ計算済み累積ETEを加えた到着予定時刻に合わせて
  取得します。取得した風をNAV LOG最終行へ採用し、到着区間のETE、燃料、WCA、GSへ反映します。
  TAF取得不能時はCALMへフォールバックします。
- RCA/EOCは物理Leg端へ丸めず、採用距離軸上の算出位置でLegを分割します。EOCが
  VREP直前の変針点を越える場合は、その変針点の採用高度を到達条件として一つ前のLegから
  再計算し、必要に応じて同じ処理を経路始点方向へ繰り返します。分割後もZone距離合計、
  `DIST = GS × ETE`、上昇時間・燃料、降下時間を保存します。RCA/EOCの算出原則は
  規程で確認済みですが、物理Leg内のexact splitと変針点高度による再計算は実装Policyです。
  各Legで500 fpmの降下に必要な時間がそのLegのETEを超え、変針点とVREPの高度制約を
  同時に満たせない場合は`DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE` Blockerとします。
- `LOSS`は機上修正値であり地上入力UIを持ちません。旧Projectの非0値もZONE/CUM ETE、
  TTL TIME、Forecast、燃料、fingerprintへ加えず、転記補助のETOは空欄にします。
- 同じForecast Runで最大5回反復し、代表時刻差30秒未満を収束とします。これは
  MSMの時刻依存値を扱う実装Policyで、規程にはありません。
- 内部値は丸めず、表示時にhalf-upで方位1°、距離0.5 nm、時間0.5分、燃料0.1 galへ
  丸めます。方位・距離・時間の単位は規程で確認済みですが、half-upのtie方法、燃料の
  一律0.1 gal、および丸め時点は未確認です。
- v2.6ではSEA・DEM・陸域マスクを計算対象外とします。旧ProjectのSEA fieldは読込互換
  だけに残し、計算、Issue、fingerprint、status、画面、転記補助HTMLのいずれにも
  使用しません。
- 不明値は後続も未確定とし、0、1013.25 hPa、最近傍気象へ暗黙にフォールバックしません。

## 未確認事項と使用制限

- 対象機の製造番号、日本承認AFM/POH、Supplementに対するP/N 13772-006性能頁の適用性。
- 性能CSV全行の原典再抽出・差分比較と確定hashは完了しています。対象機への適用性、
  省略した性能補正、およびGolden NAV LOGとの一致は未確認です。
- 飛行地域の偏差、気象・航空情報・障害物の利用者確認。SEAは本版の対象外です。
- MSM推定QNH、MSM風・気温の採用、空間/時間補間、Forecast Run選択と反復の校内承認。
- 現行のPA選択、性能補間/外挿、巡航候補選択、LOSS、丸めtie処理のexact適合性。
- 別添8-1原本とのレイアウト同一性。AutoNavLogのA4出力は原本ではなく非公式転記補助表です。

RCA/EOCが全経路端を越える場合はBlockerです。RCAが経路内の最初の変針点を越える場合は
Warningとします。これらの安全側ゲートも実装Policyであり、校内Golden NAV LOGとの照合が
終わるまで公式規則とは表示しません。
