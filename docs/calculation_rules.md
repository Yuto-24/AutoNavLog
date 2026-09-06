# 計算規則

この文書は、取得済み一次資料で確認できた要件と、AutoNavLog固有の計算Policyを分けて
記録します。一次資料の同定、版、SHA-256および未確認事項は
[一次資料監査](primary_source_audit.md)を参照してください。

この文書を現行の計算・表示Policyの正本とします。履歴資料の`DESIGN.md`と矛盾する場合は、
この文書と生成済みSchema、現行実装の順に確認し、`DESIGN.md`の旧記述を現行仕様へ
持ち込まないでください。

共通NAV LOG計算で参照した公式原典は次の2件です。RJFM大分方面例外の
原典と利用者Policyは、後述の専用節と[データ来歴](data_provenance.md#rjfm北上umk出発参照パック)で分けて記録します。

- 航空大学校『学生訓練実施要領 単発事業用課程』改正19（2026-06-24）第8章
  8-(2)〜8-(5)、別添8-1。
- Cirrus Design SR22 AFM/POH P/N 13772-006 Revision A1。性能頁5-30〜5-34は
  LOEP上Reissue A。

以下で`規程`は学生訓練実施要領で直接確認した事項、`POH`は取得済み性能頁で
直接確認した事項、
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
- 宮崎Descentの標準はCruise airspeed、500 fpmです。画面で1000 fpmを明示選択した場合は
  その値を代替計画値として使います。降下TASは巡航CASを使用し、EOCから目視位置通報点までの
  高度差と選択降下率からETEを求めます。風は両高度の概ね中間高度で予想します。
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

## RJFM大分方面のUMK/RCA例外

添付『宮崎空港及びその周辺における航空大学校所属機の訓練飛行実施要領』
R6.5.1改正は、大分方面の北上を5,500 ftとし、Newtabaru CENTER Routeの通報順を
`UMK → OVER FIELD → OMARU`としています。以下の自動適用、RCA計算、経路ソルバの
数値条件は、それをNAV LOGに表すための実装Policyです。

### 自動適用と主経路

- 出発空港が`RJFM`で、出発後の最初のWaypointがUMKまたはOMARUの参照座標から
  WGS84測地線距離1.0 NM以内の場合だけ適用します。名称ではなく座標で判定し、
  両方の範囲内なら近い方、同距離ならUMKを採用します。
- 取り込んだKMLに一致座標があるUMK・OMARUは、添付図から数値化した同梱座標より
  KML座標を優先します。
- `UMK_PHYSICAL`では最初のWaypointを物理UMKとします。後続経路にOMARUが
  1.0 NM以内で存在すれば位置を変えず、なければUMKの直後にOMARUを自動挿入します。
  RJFM→UMKを`CLIMB`、UMKからOMARUまでを`CRUISE`とし、計画高度は5,500 ft MSLです。
- `OMARU_VIRTUAL_UMK`では最初の物理WaypointをOMARUのまま保ち、UMKノードは
  挿入しません。RJFMからUMK参照座標までの距離を仮想RCA距離として最初の物理Legを
  分割し、`UMK/RCA（仮定）`と表示します。物理的なRJFM→OMARU直線Legは変更しません。
- 同じ経路への自動適用は冪等です。既存のOMARUを移動・重複させず、自動挿入した
  Legの`LOSS`は0とします。元の後続Legの手動風、気温、TAS、備考、`LOSS`は保ちます。
- OMARU自動挿入前のUMK発Legに手入力距離がある場合、その距離を新しい2 Legの
  測地線距離比で分け、合計は変えません。手入力Courseは端点が変わるため適用中は使わず、
  例外解除時に元の距離・Courseと強制前のPhase・高度を復元します。
- Route Graphと経路編集表ではUMK・OMARUを独立点のまま保持します。NAV LOG主表では、
  UMKが物理点か仮想点かにかかわらずRJFMからOMARUまでを1つの`RJFM→OMARU`親Legとして
  表示し、UMK/RCAを親Leg内の子区間境界にします。
- RJFM→UMKのALTとPhaseは5,500 ft MSL到達／`CLIMB`、UMK→OMARUは5,500 ft MSL／
  `CRUISE`に固定し、画面から変更できません。OMARUを出発するLegから通常編集へ戻します。

### UMK 5,500 ftのETE・燃料

- 物理UMKでは、RCAをRJFM→UMK物理Legの終端に固定します。Legに手入力距離があれば
  その採用距離を使います。仮想UMKでは、RJFM→OMARU親Legの採用距離を直接測地線距離比で
  換算し、採用距離軸上のUMK相当位置をRCAとします。地図上の延長旋回経路は、この距離や
  NAV LOGの経路合計へ加えません。
- RJFMからUMK/RCAまでの`ETE`と燃料は、同じ気温補正を適用したPOH上昇表の
  5,500 ft到達時間・燃料へ固定します。分割された`CLIMB`行へは距離比で配分し、
  未丸め合計をPOH値と一致させます。
- 子区間では主経路の直線Legから求めた`DIST`、`TC`、`WCA`、`MH`、`GS`をそのまま表示します。
  したがってこの例外の`CLIMB`子行では、意図的に`DIST / GS != ETE`となります。
  通常Legの`DIST = GS × ETE`不変条件に対する、この例外だけのカーブアウトです。
- `RJFM→OMARU`親行のDISTは、配下Physical Legの採用距離合計を先に0.5 NM単位へ丸め、
  そのtickを子区間のexact距離比へ最大剰余法で配賦した表示値をZONEとして使います。CUMは
  表示済み親DISTの累計です。ETEは従来どおり子区間を0.5分単位へ丸めた表示値の合計、親SECT
  FUELは各子区間を0.1 galへ丸めた表示値の合計とし、表示REMは
  表示TOTALから表示RUN UPを引いた値を起点に、各親SECT表示値をLeg順に減算します。
  計算・監査用のexact値、燃料計算、`CalculationOutcome.sections`の燃料・残量は未丸め値を
  保持します。親行のTC・VAR・MCはRJFMからOMARUへのWGS84直行測地線値を
  表示し、子行には各Sectionの
  実際のTC・VAR・MCを残します。親行のWIND・WCA・MH・GSは複数の実区間を単一値で
  表せないため空欄です。
- 仮想RCA距離が主経路全長内に収まらない場合は警告を残し、通常のRCA計算に
  フォールバックします。

### RWY09/RWY27出発案内

- 出発案内はNAV LOGの主経路と別の参考オーバーレイです。両方のRunway案を毎回同時に作り、
  `CENTER: UMK → OVER FIELD → OMARU`と併記します。経路合計、ETE、燃料、`LOSS`、
  Readiness判定には使いません。
- WebカードはNorth Up上の滑走路出発方位を水平位置へ投影して左から右へ並べます。RJFMでは
  RWY27が左、RWY09が右です。表には旋回開始高度、旋回開始点のMZE斜距離DMEと、候補UMK到達時間から
  `RJFM→UMK/RCA`採用距離をCLIMB GSで飛行した直線基準時間を引いた差だけを表示します。
  旋回角、個別モデル、MZE位置、全周後ドリフト、残差、制約一覧は内部診断へ保持し、
  利用者向け表には表示しません。
- RWY09はMC 092°を1,000 ft MSLまで維持し、左へ45°変針してMC 047°とします。
  RWY27はMC 272°を1.5 NM維持し、右へ45°変針してMC 317°とします。その後は
  直線、必要な回数の完全旋回、部分旋回、UMKへの接線直線を組み合わせます。
  延長旋回方向はRWY09が左、RWY27が右です。
  NAV LOGと同じ採用風、TAS、POH高度・経過時間プロファイルでUMK 5,500 ftに合わせます。
- まず対気固定バンク20°で解きます。物理解がない場合は、全周でバンクが20°を超えない
  最大地上旋回半径を使う真円フォールバックで再計算します。ハード制約に適合する候補のうち、
  指定方向の総旋回角が最小のものを採用し、同角ならMZEから水平に遠い方を優先します。
- ハード制約は、延長直線とUMK直線のMCが`(272°, 360°) ∪ [0°, 92°)`に入ること、
  設定高度帯内で宮崎特別管制区に入らないこと、UMK位置残差0.01 NM以下、
  高度残差10 ft以下、接線角残差0.1°以下です。
- 最終延長旋回開始点のMZE斜距離DMEは案内値として表示し、候補判定や警告には使いません。
- ハード制約不適合の経路は、Web編集画面に赤い診断経路と理由を残しますが、
  NAV LOGのIssueやReadiness Blockerにはしません。Webには不適合経路と理由を診断表示します。
  物理解なしや入力不足も同様に利用不能理由を保存します。
- 案内結果は参照パック版とpayload SHA-256、元資料の有効日、計算入力fingerprint、制約判定、
  残差とともにProjectへ保存します。経路・気象・性能入力の変更後は旧案内をWeb地図から隠し、
  次の計算で再生成します。Web以外から古い例外計画を直接計算へ渡した場合もBlockerとして
  例外RCAを使いません。資料日付だけを理由に自動失効はさせません。

この案内は、地形、障害物、ここで定義していない他空域、最新のATC指示を判定しません。
Web地図上の宮崎特別管制区境界・9 km中心除外円は参照表示です。国土地理院からライブ取得する
民間訓練試験空域KS4も表示専用で、取得内容はNAV LOGや上記制約判定へ入力しません。
計画時と飛行時に、利用者が最新資料とATC指示に照合する必要があります。

## AutoNavLogの現行実装Policy

- 測地線はWGS84、Courseは初期真方位、Legの自動気象照会点は測地線上の中点です。
  これは実装Policyです。
- Variationは各物理Legの出発点緯度から自動判定します。32.0°N以上（境界を含む）は
  +8°E、32.0°N未満は+7°Eです。Phase境界で計算行が分割されても元の物理Legの
  出発点を使います。保存済みProjectの`default_variation_deg_east`はschema互換のため
  読み込みますが、新しい計算値には使用しません。
- 東偏差を正として`MC = TC + VAR`、右WCAを正として`MH = MC + WCA`とします。
  計算結果には未丸め値を保持します。NAV LOG表示のMHだけは、同じ行へ表示する1°単位のMCと
  WCAを加算して3桁表示し、`291 + (-4) = 287`のように表示欄同士の関係を保ちます。
- [航空法施行規則第177条](https://laws.e-gov.go.jp/law/327M50000800056?occasion_date=20260316)
  は、VFRの飛行方向を磁方位0°以上180°未満と180°以上360°未満に分けます。
  AutoNavLogでは、手動飛行でNAV LOGへ転記するMCと候補判定を一致させるため、exact MCを
  1°単位へhalf-upした値を候補判定用MCとします。179.5°以上は表示180°側、359.5°以上は
  表示360°かつ判定0°側です。exact MCは監査用に保持します。この丸め後MCを法令の
  「磁方位」へ適用する選択は利用者承認の実装Policyであり、学生訓練実施要領が直接指定した
  方法とは扱いません。
- NAV LOG計算では`PA = MSL`とし、計画MSL高度をそのままPA、POH性能検索、CAS/TAS換算へ
  使用します。気圧補正用の入力は持ちません。
- 現行G6上昇表は原表19節点を高度方向に線形補間した500 ft刻みISA行を収録し、
  出発・巡航の累積値の差へ、中間気圧高度のISA温度に対する正の温度差1℃ごとに
  1%（10℃ごとに10%）を一度だけ加えます。標準以下
  では減算しません。高度軸だけ表端から500 ft以内の外挿を許可します。10℃ごとの増加は
  POHで裏付け済みですが、比例適用、標準以下の扱い、補間・外挿は実装Policyです。
- 巡航表は各POH PA / ISA cornerでまず65% PWRを解決し、その後にISA偏差方向、高度方向の
  順で区分線形補間してKTAS/GPHを0.1単位のties-to-evenで確定します。65%を挟むPWR行が
  あるcornerはPWR軸を線形補間し、掲載PWR範囲外のcornerは最寄りの2行から65%へPWR軸を
  線形外挿します。後者はPOH直接値ではないAutoNavLogの実装Policyです。高度・ISA偏差は
  外挿せず、範囲外入力では最寄りの表端条件を採用して計算を継続します。
  各軸の上下限・係数、PWR補間または外挿corner、参照頁を結果metadataへ残します。表端の
  高度・ISA採用だけを`CRUISE_*_TABLE_BOUNDARY_USED`警告として通知し、利用前の確認を
  必要とします。65% PWR外挿は通常の計算経路として扱い、Warningにはせず監査用metadataへ
  記録します。
  この順序・丸めは旧Issue #15添付の多次元拡張表との補間範囲で一致確認済みですが、規程の
  「計画に近い条件のうち不利」へのexactな適合性とGolden NAV LOGは未検証です。
- 巡航表から自動採用したKTASは、ノーズフェアリングありならPOH値どおり、なしなら
  `-10 KTAS`とします。A/C ON時はさらに`-2 KTAS`を加算するため、ノーズフェアリング
  なし・A/C ONでは合計`-12 KTAS`です。GPHは補正しません。
  手入力TASは最終採用値として扱い、この補正を重ねません。両補正値は利用者提供転記で、
  [Cirrus公式Supplement案内](https://store.cirrusaircraft.com/sr22-supplement-13772-127%2C-air-conditioning/5637369215.p)では
  A/C装備時に別Supplementが適用されることを確認していますが、該当Supplement本文は
  リポジトリにないためページ番号は記録しません。
- 手入力TASは Physical Leg の単一値ではなく、同じ Leg から分割された実効
  `CLIMB`、`CRUISE`、`DESCENT` の各Flight Phaseに保存します。したがってRCA後の
  `CRUISE`入力はRCA前の`CLIMB`へ、EOC前の`CRUISE`入力はEOC後の`DESCENT`へ適用しません。
  `DESCENT`入力だけは既存どおり最初のDESCENT基準Legから全DESCENT zoneへの共通overrideです。
  旧Projectのscalar TASは保存時のsection phaseへだけ移行し、他phaseへ複製しません。
- RUN UPありは10分・1.5 gal、なしは0分・0.0 galです。BOF Fuelは
  `CLIMB + CRUISE + DESCENT + TGL + ADDITIONAL`で、RUN UPとRESERVEを含みません。
- 降下はEOC直前の正の距離を持つ実効`CRUISE` Calculation ZoneのCASを共通CASとして、
  以後の全`DESCENT` Zoneへexactに適用します。Physical Legの指定phaseではなく、RCA/EOCで
  分割・snapした後のZoneを基準にするため、EOCがDESCENT指定Leg内または前Legへ移る場合も
  CAS sourceが追従します。EOCとCAS sourceは自己整合的に再計算し、EOC直前にCRUISE Zoneが
  ない自動降下はBlockerです。手動DESCENT TASは既存の共通overrideとして扱います。
  Projectで選択した500 fpmまたは1000 fpm、12 GPHを使用します。
  500 fpmが規程で裏付けられた標準値で、1000 fpmは利用者が明示選択する代替計画値です。
  EOC探索はまずDESCENT基準Leg自身の計画高度から開始し、
  `（基準Leg高度 - VREP高度）/ 選択降下率 + 1分`が同Leg内に収まる場合はそこで終了します。
  前方の高度を先に採用しません。
- 基準Legの必要時間が同Leg内に収まらない場合は、経路始点方向の直前Legを候補へ加え、
  その候補Legの計画高度からVREPまでの**1本の連続降下**として必要時間を再計算します。
  各変針点の計画高度は中間制約にしません。追加した前Legの高度が次Legより低い場合は
  `DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE` Blockerとし、経路始点からでも不足する場合も
  同Blockerとします。metadataには開始高度からVREPまでの1件の遷移、通過Leg、Leg別GSを残します。
- EOC位置を逆算するときは、DESCENT基準LegのDESCENT phaseで採用した風をEOCからVREPまで
  共通して使います。対象の各物理Legについて、その共通風とLeg固有のTAS・TCからWind Triangleを
  解き、Leg固有のGSを求めます。基準Legのphase-specific wind overrideも同じ経路で採用します。
  `deceleration_duration_seconds = 60`は全プロファイルで1回だけであり、
  EOCから直ちに選択降下率でVREP高度まで連続降下し、level off後の最後の1分を減速に使います。
- 目視位置通報点以降はCAS 121 kt・12 GPH、CALM固定で、WCA=0、GS=TASとします。
- RJFM到着で最終VREPがARITA（有田）またはSHIRAHAMA（白浜）に名前又は基準座標から0.5 NM以内で一致するときは、通常の距離式に代えて自動VREP高度を1,500 ft MSLとします。手動VREP高度は維持します。基準座標・名称照合・出典は[RJFM ARITA / SHIRAHAMA final-VREP altitude policy](rjfm_arrival_vrep_altitudes.md)に記録します。
  CAS、標準の500 fpm、燃料流量、到着区間CALMは規程で裏付け済みです。
  1000 fpmは規程上の標準値としては扱いません。
- 目的地TAFの風は、出発予定時刻へ計算済み累積ETEを加えた到着予定時刻に合わせて
  取得します。取得した風は独立した`DESTINATION_INFO`行への表示だけに使い、到着区間の
  ETE、燃料、WCA、MH、GSへは反映しません。到着区間の計算親行はCALM、WCA=0、
  GS=TASを維持します。出発親行と`DESTINATION_INFO`行のTOATは、空港標高を気圧面の
  ALOFT気温へ外挿せず、各表示時刻・空港座標のMSM地上気温`tmp_surface`を使用します。
  `DESTINATION_INFO`行は目的飛行場標高、同地点のMSM予想地上気温、TAF風だけを表示し、
  その他の航法・距離・時間・燃料セルは意図的な空欄です。
- FTD固定気象では、地上を0 ft MSLとし、地上風と5,000 ft風を東西・南北成分へ変換して
  0〜5,000 ftを線形補間します。5,000 ft以上は5,000 ft風を使用し、気温は要求MSL高度の
  ISA値です。出発・目的空港の地上気温は各空港標高のISA値とします。風向角を直接補間せず、
  350°と10°の間で180°を通る誤りを避けます。
  FTD計算では実Forecast Runと目的地TAFを取得せず、合成Run IDと入力値、評価高度、
  補間率を結果metadataへ残します。VREPから目的空港までのCALM固定規則は変更しません。
- RCA/EOCは採用距離軸上の算出位置で物理LegをCalculation Zoneへ分割します。EOCと物理
  変針点の距離差が0.5 NM未満なら内部計算上も変針点へsnapし、`<TP名> / EOC`と表示します。
  VREPである降下終端はsnap候補から除外します。ちょうど0.5 NMではsnapしません。
  このsnap規則をCheck Pointへは適用しません。
  snapは境界を揃える限定的な位置正規化です。EOC metadataの選択降下率による降下時間とlevel off後
  60秒は計画高度から求めた値を保持するため、snapした場合だけZoneのGS×ETE合計と完全一致
  しないことがあります。
  進行方向の次Leg開始へsnapした場合は、EOCの`section_id`、`eoc_source_section_id`、
  `descent_path_section_ids`を実際に始まるDESCENT Legへ揃え、開始高度を決めた候補Legは
  `profile_start_section_id`へ残します。
  Check Point、RCA、EOC、物理終点は未丸めのalong-route distance順に並べ、表示丸めで
  前後関係を変えません。通常の分割ではZone距離合計と`DIST = GS × ETE`を保存します。
  上記RJFM UMK/RCA例外の`CLIMB`行だけはPOH ETEを優先するため、この等式の対象外です。
- `CalculationOutcome.sections`は重複しないCalculation Zoneです。`display_rows`はそこから
  作る表示専用投影です。分割の有無にかかわらず各通常Physical LegへFROM/TOを持つ
  `PHYSICAL_LEG_SUMMARY`と最低1つの`CALCULATION_ZONE`内訳行を置きます。DISTは各Physical
  Legの採用距離を先に0.5 NMへ丸める。通常Legは`Geometry.distance_nm`、RJFM→OMARU集約親は
  配下`Geometry.distance_nm`の合計を採用距離とする。丸め済み親DISTの0.5 NM tickは、子Zoneの
  exact距離比へ最大剰余法（floor後、小数部降順、同値は経路順）で配賦する。親ZONE/CUMはこの
  表示済み親DISTをLeg順に加えた値とし、子の`distance.text`だけを配賦値へ置き換える。
  子のeffective valueと`CalculationOutcome.sections`のexact距離は変更しない。採用距離または
  Zone距離が欠損・不整合なら、当該親子DISTは`UNAVAILABLE`としてfail-closeする。ETEは配下Zoneを
  表示単位へ丸めた値の合計、CUMは表示済み小計をLeg順に加えた値を小計行だけに表示します。親SECT FUELも配下Zoneの表示済み0.1 gal値の合計、REMは
  表示TOTALから表示RUN UPを引いた起点から表示SECTをLeg順に引いた値です。内訳行のFROMと
  CUMは空欄です。最終`VISUAL_ARRIVAL`だけは親計算行と`DESTINATION_INFO`行に分け、
  通常内訳行を作りません。各Physical Legグループの後には`LEG_SEPARATOR`を1行置きます。
  `display_rows`の親小計・目的地情報・区切りは表示専用であり、距離・時間・燃料の集計へ
  使いません。
- NAV LOG Summaryはサーバー側で生成する表示投影です。TTL DISTはcanonicalなPhysical Legの採用距離を既存の0.5 NM配分規則で集計し、TTL TIMEはcanonicalなZone ETEを既存の0.5分丸め（RJFM inboundは固定の一度丸めた配分）で集計した後、最終表示だけ分へhalf-upしてH:MMへ整形します。WebはLeg行や目的地情報行を再集計せず、Summaryの表示セルをそのまま使用します。Summaryは独立したFuelPlanのendurance値から生成しません。旧保存CalculationOutcomeにSummaryがない場合は未取得として扱います。
- `NavLogDisplayCell.state`は表示の意味を明示します。`DISPLAY_VALUE`はcanonicalな値、
  `INHERIT`は親または直前行の計算値を継承しながら表示は完全な空欄、`BLANK`は継承しない
  意図的な空欄、`STATE_SYMBOL`は`↗`/`↘`等、`UNAVAILABLE`は本来必要な値の取得・算出失敗です。
  `INHERIT`と`BLANK`へ`未取得`、`未確定`、`—`を表示しません。`UNAVAILABLE`だけを
  `未取得`として太字・赤系背景で表示します。Webは同じcellの`text`を使用し、
  表示componentで航法値を再計算しません。
- 親行はLeg開始時の既定値を表示し、子行はZone固有値、Check Point固有値、親または直前の
  表示値から変化した値だけを表示します。同値を使う子セルは`INHERIT`です。出発Legの動的な
  CLIMB/CRUISE性能値は親へ集約せず、対応する内訳行へ表示します。
- EOCは風の適用条件が切り替わる継承境界です。EOC後最初の`DESCENT` Zone（物理Leg途中、
  または`<TP名> / EOC`へsnapした変針点のいずれも）は、親・直前行と実効値が同じでも
  `WIND`、`WCA`、`MH`、`GS`を候補セルのまま明示表示します。候補が`UNAVAILABLE`なら
  その状態を維持します。`TOAT`、`CAS`、`TAS`、`TC`、`VAR`、`MC`、PAなどは従来どおり
  継承し、以後のZoneは通常の継承規則へ戻ります。DESCENT ZoneのWIND編集は、EOCが前Legへ
  移動した行から操作した場合もDESCENT基準Legの共通風overrideへ反映します。
- PA表示は計算高度値とは別の表示種別を持ち、数値、上昇`↗`、降下`↘`、推定通過高度
  `(<高度>)`、空欄、未取得を区別します。推定通過高度は高度制約ではなく、EOC後の経過時間と
  計算metadataに保存した選択降下率から求める表示用結果です。
- `LOSS`は地上計画の計算対象外です。旧Projectに残る値はschema v4への読込時に破棄します。
- 同じForecast Runで最大5回反復し、代表時刻差30秒未満を収束とします。これは
  MSMの時刻依存値を扱う実装Policyで、規程にはありません。
- 内部値は丸めず、表示時にhalf-upで方位1°、距離0.5 nm、時間0.5分、燃料0.1 galへ
  丸めます。PAは整数、TOATは0.1℃、CAS/TAS/GSは1 kt、TC/MC/MHは3桁、VAR/WCAは
  正値へ`+`を付け（0は`0`）、WINDは`DDD/kt`または`CALM`で表示します。親と子は同じ
  表示済み値をオペランドとして親小計・累計・残量を作るため、画面上の加減算が成立します。
  内部未丸め小計と`CalculationOutcome.sections`は変更しません。方位・距離・時間の単位は
  規程で確認済みですが、half-upのtie方法、燃料の一律0.1 gal、表示済み値から小計・残量を
  作ること、MH転記値を表示済みMC/WCAから作ること、および丸め時点は実装Policyです。
- Fuel Planの表示はNAV LOGと同じ0.1 gal表示値から再構成します。Phase Fuelは該当する
  Calculation Zoneの表示燃料の合計、`BOF = CLIMB + CRUISE + DESCENT + TGL + ADDITIONAL`、
  `MIN REQUIRED = TAXI/RUN UP + BOF + RESERVE`、`EXTRA = TOTAL - MIN REQUIRED`です。
  表示TOTALはProjectのusable fuelを0.1 galへ丸めた値とし、`TOTAL = MIN REQUIRED + EXTRA`を
  保ちます。Phase TIMEは該当Zoneのexact ETE合計を1分へhalf-upし、MIN REQUIRED TIMEは
  その表示済みPhase TIMEと固定表示時間の合計です。EXTRA TIMEは表示EXTRAを16.5 GPHで
  換算して1分へhalf-upし、TOTAL TIMEは表示MIN REQUIRED TIMEとの和にします。
  canonicalな`FuelPlan`のexact値は性能計算・監査用として変更しません。
- Webの数値入力は文字列Draftを保持し、編集中の空欄を許可します。確定・再計算時だけ
  必須性、整数性、範囲、刻みを検証し、API境界で数値へ変換します。モバイルを含め、
  「全消去してから再入力」できることを数値入力の共通設計とします。
- v1.4.0ではSEA・DEM・陸域マスクを計算対象外とします。旧ProjectのSEA fieldは
  schema v4への読込時に破棄し、計算、Issue、fingerprint、status、画面に使用しません。
- 不明値は後続も未確定とし、0、1013.25 hPa、最近傍気象へ暗黙にフォールバックしません。
  FTDのCALMまたはISA値は、利用者がFTDモードと両高度の風を明示した場合だけ採用します。

## 未確認事項と使用制限

- 対象機の製造番号、日本承認AFM/POH、Supplementに対するP/N 13772-006性能頁の適用性。
- 性能CSV全行の原典再抽出・差分比較と確定hashは完了しています。対象機への適用性、
  省略した性能補正、およびGolden NAV LOGとの一致は未確認です。
- 飛行地域の偏差、気象・航空情報・障害物の利用者確認。SEAは本版の対象外です。
- MSM風・気温の採用、空間/時間補間、Forecast Run選択と反復の校内承認。
- `PA = MSL`、性能補間/外挿、巡航候補選択、LOSS、丸めtie処理のexact適合性。
- 別添8-1原本とのレイアウト同一性。AutoNavLogは別添8-1の帳票を出力しません。

RCA/EOCが全経路端を越える場合はBlockerです。RCAが経路内の最初の変針点を越える場合は
Warningとします。これらの安全側ゲートも実装Policyであり、校内Golden NAV LOGとの照合が
終わるまで公式規則とは表示しません。
