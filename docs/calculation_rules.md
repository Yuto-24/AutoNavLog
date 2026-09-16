# 計算規則

この文書は、AutoNavLogが実際に採用する計算・表示Policyの正本です。

一部のPolicyは航空大学校の内部運用資料を参照して設計・検証していますが、このRepositoryでは原資料の章節、ページ、原文要約、hashとの対応を保持しません。公開範囲の考え方は[出典・provenance方針](source_policy.md)を参照してください。

SR22性能値については、Cirrus Design SR22 AFM/POH P/N 13772-006 Revision A1を参照します。性能頁5-30〜5-34はLOEP上Reissue Aです。

## NAV LOGの基本Policy

- 内部計算は原則として未丸め値を保持します。
- 表示時は方位1°、距離0.5 NM、時間0.5分、燃料0.1 galへ丸めます。
- WGS84測地線でLegの距離と初期真方位を求めます。
- NAV LOG計算では計画MSL高度をPAとして扱い、QNHによるPressure Altitude補正は行いません。
- Variationは各Physical Legの出発点緯度から自動選択します。32.0°N以上は+8°E、32.0°N未満は+7°Eです。
- 東偏差を正として`MC = TC + VAR`、右WCAを正として`MH = MC + WCA`とします。
- NAV LOGへ表示するMHは、同じ行に表示する丸め済みMCとWCAから作り、画面上の算術を一致させます。
- VFR巡航高度候補の方向判定は、NAV LOGへ表示する1°単位のMCを使用します。359.5°以上は表示360°、判定上は0°側とします。

## Phaseと代表気象

Flight Phaseは`CLIMB`、`CRUISE`、`DESCENT`、`VISUAL_ARRIVAL`を扱います。

- CLIMBの代表気象高度は、出発空港標高と巡航高度の中間です。
- CRUISEは巡航高度です。
- DESCENTは巡航高度とVREP高度の中間です。
- 目的空港情報行の地上気温は空港位置・空港標高に対応する地上気温を使用します。
- FORECASTでは同じForecast Runを固定し、代表時刻が収束するまで最大5回反復します。代表時刻差30秒未満を収束とします。
- FTDでは地上風と5,000 ft風を東西・南北成分へ変換し、0〜5,000 ftを線形補間します。5,000 ft以上は5,000 ft風、気温はISAを使用します。

## CLIMB

- 自動TASはCAS 111 ktとCLIMB代表高度の気温から算出します。
- 上昇ETE、燃料、参照距離はSR22上昇性能表を使用します。
- 現行データはPOH原表の高度節点を500 ft刻みへ線形補間したISA基準行を収録します。
- 標準より高温の場合は、中間気圧高度のISA温度との差に応じて1℃あたり1%を加えます。標準以下では減算しません。
- 高度軸だけ、表端から500 ft以内の外挿を許可します。
- RCAは上昇ETEを経路始点から前向きに消費して配置します。経路が途中で変針する場合、Legごとの風力三角形を使用します。

## CRUISE

- 宮崎NAV2の標準Cruiseは65% Best Powerです。
- SR22巡航表の各PA / ISA cornerで65% PWRを先に解決し、その後ISA偏差、高度の順で区分線形補間します。
- 65%を挟むPWR行があるcornerではPWR軸を線形補間します。
- 掲載PWR範囲外では、最寄り2行から65%へPWR軸を線形外挿します。
- 高度・ISA偏差方向の外挿は行わず、範囲外では表端条件を採用し確認事項を残します。
- KTAS/GPHは補間後に0.1単位のties-to-evenで確定します。
- ノーズフェアリングありではPOH KTASを使用し、なしでは-10 KTASとします。A/C ON時はさらに-2 KTASです。GPHは補正しません。
- 手入力TASは最終採用値として扱い、上記のKTAS補正を重ねません。

## DESCENT / EOC

- 降下率は500 fpmを標準とし、利用者が1000 fpmを選択できます。
- 自動DESCENTではEOC直前の実効CRUISE Calculation ZoneのCASを共通CASとして使用します。
- EOC以降のDESCENT燃料は12 GPHです。
- EOCは降下に必要な時間を逆算して経路上へ配置します。
- EOCとPhysical Turn Pointの経路上距離差が0.5 NM未満なら、内部計算上もTurn Pointへsnapします。ちょうど0.5 NMではsnapしません。
- snap後も、計画高度差から求めた降下時間とlevel-off後の60秒は保持します。
- EOC後の風、WCA、MH、GSは継承境界として最初のDESCENT行へ明示します。

## VISUAL ARRIVAL

- VREPから目的空港まではCAS 121 ktを使用します。
- 航法計算上の風はCALM固定で、WCA=0、GS=TASです。
- 目的地TAF風は独立した目的空港情報として表示しますが、この区間の航法計算には使用しません。
- VREP高度はAutoNavLogのstandard distance ruleまたは利用者のmanual overrideで決定します。
- RJFMのARITA / SHIRAHAMAについては専用Policyを適用します。

## 燃料計画

AutoNavLogの固定Fuel Plan Policyは次のとおりです。

- TAXI/RUN UP: 10分、1.5 gal。RUN UPなしの場合は0分、0 galです。
- CLIMB: POH上昇性能から算出します。
- CRUISE: 採用したCruise GPHとETEから算出します。
- DESCENT / VISUAL ARRIVAL: 12 GPHです。
- ADDITIONAL: 10分、2.8 galです。
- TGL: 1回につき7分、2.0 galです。
- RESERVE: 45分、12.4 galです。
- `BOF = CLIMB + CRUISE + DESCENT + TGL + ADDITIONAL`です。
- `MIN REQUIRED = TAXI/RUN UP + BOF + RESERVE`です。
- `EXTRA = TOTAL - MIN REQUIRED`です。
- EXTRA enduranceは16.5 GPHで換算します。

表示Fuel PlanはNAV LOGと同じ表示済み0.1 gal値から再構成し、画面上の加減算を成立させます。canonicalなFuelPlanは未丸め値を保持します。

## RJFM北行き専用Policy

RJFMから大分方面へ北上する一部経路では、AutoNavLogのRJFM専用Policyを適用します。

- UMK / OMARUは名称ではなく参照座標と距離で識別します。
- 適用中はRJFM→UMKをCLIMB、UMK→OMARUをCRUISEとして扱い、目標高度を5,500 ft MSLに固定します。
- 経路にOMARUがない場合は、条件を満たす経路へOMARUを補います。
- OMARUから開始する経路では物理UMKを挿入せず、参照位置を仮想RCAとして扱います。
- NAV LOG主表ではRJFM→OMARUを1つの親Legとして表示し、UMK/RCAを内部境界として扱います。
- RJFM→UMK/RCAのETEと燃料は、同じ気温補正を適用したPOHの5,500 ft到達値を優先します。この専用区間では`DIST / GS = ETE`を要求しません。
- 利用者が取り込んだKMLに対応するUMK / OMARU座標がある場合は、その座標を同梱参照値より優先します。

### RWY別参考案内

RJFMのRWY09 / RWY27について、NAV LOG本体とは独立した参考経路を生成します。

- RWY09はMC 092°を1,000 ft MSLまで維持し、左45°でMC 047°へ変針します。
- RWY27はMC 272°を1.5 NM維持し、右45°でMC 317°へ変針します。
- 延長旋回はRWY09が左、RWY27が右です。
- 固定バンク20°を基本とし、必要時は20°を超えない最大地上旋回半径で再計算します。
- UMK位置残差、高度残差、接線角残差、宮崎特別管制区との関係を候補判定へ使用します。
- 参考案内はNAV LOGのDIST、ETE、Fuel、Readinessへ加算しません。
- 国土地理院から取得する民間訓練試験空域GeoJSONは表示専用で、NAV LOG計算やPCA判定へ使用しません。

## 表示モデル

- `CalculationOutcome.sections`は重複しないCalculation Zoneです。
- `display_rows`は表示専用投影で、Physical Leg summaryとCalculation Zone内訳を分離します。
- Physical Legの表示DISTは0.5 NM単位へ丸め、そのtickを子Zoneへ最大剰余法で配賦します。
- 親ETEは表示済み子ETEの合計、親SECT FUELは表示済み子Fuelの合計です。
- REM Fuelは表示TOTALから表示RUN UPを引いた値を起点に、表示SECTを順に減算します。
- `INHERIT`は計算値を継承しつつ表示を空欄にする状態、`BLANK`は継承しない意図的な空欄、`UNAVAILABLE`は本来必要な値を取得・算出できなかった状態です。
- NAV LOG SummaryはcanonicalなPhysical Leg / Zone値からサーバー側で生成し、Web側で再集計しません。

## 手入力と保存

- 手入力TAS、気温、風はeffective Flight Phase単位で扱います。
- 入力変更後は、保存済みCalculationとのfingerprintが一致しない場合に再計算を要求します。
- Projectには最新draftと、明示保存されたcheckpointを保持できます。
- 最後にBlockerなしで完了したCalculationはProjectごとに1件だけ保持します。

## 使用制限

- AutoNavLogは公式規程、公式様式、運航承認を代替しません。
- SEA、DEM、地形・障害物は現行計算対象外です。
- 参照値、気象、航空情報、ATC指示は利用時に最新情報と照合してください。
- POH性能頁についても、対象機の製造番号、日本承認AFM/POH、Supplementへの適用性は別途確認が必要です。
