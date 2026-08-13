# 一次資料監査

監査日: 2026-08-13

この文書は、AutoNavLogの現行Policyを、取得済みの航空大学校規程、Cirrus Aircraftの
SR22 POH、および公開一次資料と照合した記録です。判定語は次の意味で使用します。

- `裏付け済み`: 記載した要件・値・帳票項目を、特定した一次資料で直接確認した。
- `原典確認・実装未検証`: 根拠資料は確認したが、repoのデータ転記、計算結果、または
  適用機との一致をまだ検証していない。
- `未確認`: 一次資料が方法を特定していない、適用範囲を確定できない、またはGolden
  NAV LOGとの照合が終わっていない。

`裏付け済み`はAutoNavLog自体の校内承認、運航承認、または全計算の公式準拠を意味しません。

## 取得済み公式原典

| ID | 資料 | 版・有効頁 | SHA-256 | 今回確認した範囲 |
| --- | --- | --- | --- | --- |
| CAC-REV19 | 航空大学校『学生訓練実施要領 単発事業用課程』 | 改正19、2026-06-24 | `f00fea1903a235491e2079085bc0e65031497bd65d5ace2b471e19d00f96e0e8` | 第8章 8-(2)〜8-(5)、別添8-1 |
| SR22-POH-A1 | Cirrus Design SR22 Airplane Flight Manual / POH, P/N 13772-006 | 文書Revision A1。LOEP上、Section 5の5-1〜5-44はReissue A。今回使用した性能頁5-30〜5-34もReissue A | `0f3d1150137486f506a3f3ad4b37c5c600c26c76e83f9de9c8ceb20f8dbf9213` | 5-30〜5-31 `Time, Fuel, & Distance to Climb`、5-32〜5-34 `Cruise Performance` |

SR22-POH-A1のLOEPは取得PDF内とDrive保管A1版の双方で、5-1〜5-44がReissue Aのままで
あることを確認しました。ただし、P/N 13772-006の性能頁が対象機の製造番号、日本承認AFM/
POHおよび全Supplementに対してそのまま適用可能かは、機体別資料との照合が別途必要です。

## 航空大学校規程で直接確認した要件

| 分野 | 原典箇所 | 直接確認した内容 | 現行repoの判定 |
| --- | --- | --- | --- |
| 性能表の選択 | CAC-REV19 8-(2)「4. 性能表」 | 上昇に必要な時間・燃料・距離は飛行規程第5章から500 ft単位で算出する。巡航性能は計画に近い気圧高度、気温、出力のうち不利な条件を採用する | 原則は裏付け済み。500 ft間の補間方法、表外外挿、および「不利」のexact tie-breakは未確認 |
| 記入単位 | CAC-REV19 8-(3)「4. 各経路の方位、距離及び時間」 | 方位1°、距離0.5 nm、時間0.5分単位で記入する | 単位は裏付け済み。ちょうど中間値の丸め方向や内部計算を丸める時点は規程に記載がなく、現行half-upは未確認 |
| SEA | CAC-REV19 8-(3)「3. 巡航高度の決定」 | 各区間のSEAは予定経路両側3 nm内の最高障害物上端+1,000 ftを100 ft単位で切り上げ、天候等も考慮して巡航高度を決める | 定義は裏付け済み。v2.6ではSEA・地形機能を対象外とし、取得・算出・手入力・比較・表示・転記可否判定を行わない |
| 上昇TAS・RCA ETE | CAC-REV19 8-(3)「5.(1)」「6.(1)」 | 出発標高と巡航高度の概ね中間高度の気温、CAS 111 ktで上昇TASを求める。RCAまでのETEは飛行規程第5章の上昇時間を使う | 規則とPOH表転記は裏付け・照合済み。現行上昇TAS処理のGolden照合は未完了 |
| 宮崎巡航 | CAC-REV19 8-(2)「3. 航法諸元」、8-(3)「5.(2)」「6.(2)」 | 宮崎Cruiseは65% Best Power。飛行規程第5章「巡航性能」からTASを求め、予想風との風力三角形からGS・ETEを算出する | 原則は裏付け済み。現行候補選択・tie-breakのexact仕様は未確認 |
| 降下・到着 | CAC-REV19 8-(2)「3. 航法諸元」、8-(3)「5.(3)〜(4)」「6.(3)〜(4)」 | 宮崎DescentはCruise airspeed・500 fpm。降下TASは巡航CASを用いる。EOCから目視位置通報点までは中間高度の予想風、目視位置通報点から目的空港まではCAS 121 kt・無風でETEを算出する | 500 fpm、降下CASの考え方、到着CAS 121 kt・無風は裏付け済み。規程は降下前にFuel Flowを18 GPHへsetする操作も記載するが、計画燃料は下記12 GPH |
| RCA距離 | CAC-REV19 8-(4)「9.(1) RCA」 | 上昇TASと予想風からGSを求め（風を予想しない場合はTAS）、上昇ETEを用いてRCAまでの距離を算出する | `距離 = GS × ETE`の根拠は裏付け済み。物理Leg内でのexact split処理は実装Policy |
| EOC距離 | CAC-REV19 8-(4)「9.(2) EOC」および8-(3)「6.(3)」 | 降下TASと予想風からGSを求め（風を予想しない場合はTAS）、500 fpmで求めた降下ETEからEOC位置を算出する | 原則は裏付け済み。規程本文9.(2)の参照番号表記と実装の区間分割詳細はGolden照合が必要 |
| TAXI・RUN UP | CAC-REV19 8-(5)「10.(2)」および別添8-1 | 0:10、1.5 gal | 値は裏付け済み |
| CLIMB燃料 | CAC-REV19 8-(5)「10.(3)」 | 離陸からRCAまでを飛行規程第5章「上昇に必要な時間、燃料及び距離」で求める | 原則は裏付け済み |
| CRUISE燃料 | CAC-REV19 8-(5)「10.(4)」 | 宮崎課程は飛行規程第5章「巡航性能」から求める | 原則は裏付け済み |
| DESCENT燃料 | CAC-REV19 8-(5)「10.(5)」 | 宮崎課程はEOCから目的地空港まで一律12 GPH | 値は裏付け済み |
| ADDITIONAL | CAC-REV19 8-(5)「10.(6)」 | 宮崎課程は着陸所要時間・空中交代等として10分、2.8 gal | 値は裏付け済み |
| TGL | CAC-REV19 8-(5)「10.(7)」 | 場周1回につき7分、2.0 gal | 値は裏付け済み |
| RESERVE | CAC-REV19 8-(5)「10.(8)」および別添8-1 | VFR Navigationのreserveとして0:45、12.4 gal | 値は裏付け済み。運航規程との版整合は別途確認対象 |
| EXTRA | CAC-REV19 8-(5)「10.(10)」 | TOTALからMIN REQUIREDを引いた燃料を16.5 GPHで時間換算する | 値は裏付け済み |
| 別添8-1の主表 | CAC-REV19 別添8-1 | DATE、SHIP、FROM、TO、PILOT、TTL DIST/TIME、TAKE OFF/LANDING、およびFROM、TO、PA、TOAT、CAS、TAS、TC、VAR、MC、WIND、WCA、MH、ZONE/CUM DIST、GS、ZONE/CUM ETE、ETO、ATO、ATE、SECT/REM FUEL | 項目集合は裏付け済み。AutoNavLogのA4表は原本複製ではなく非公式転記補助表 |
| 別添8-1の燃料欄 | CAC-REV19 別添8-1 | TAXI・RUN UP、CLIMB、CRUISE、DESCENT、TGL、ADDITIONAL、RESERVE、MIN REQUIRED、EXTRA、TOTAL | 項目集合は裏付け済み |

## SR22 POH性能頁で直接確認した事項

| 分野 | 原典箇所 | 直接確認した内容 | 現行repoの判定 |
| --- | --- | --- | --- |
| 上昇表 | SR22-POH-A1 pp.5-30〜5-31 | Full Throttle、Mixture per Section 4、6.0 lb/gal、3600 lb、無風。海面からの高度別Time/Fuel/Distanceを掲載。標準より10℃高いごとに計算値へ10%を加える注記、start/taxi/takeoffに1.5 galを加える注記あり | 原表19節点を完全転記・独立比較し、Issue #15添付に合わせた500 ft線形補間36行をruntime CSV化。対象機適用性とGolden照合は未完了 |
| 巡航表 | SR22-POH-A1 pp.5-32〜5-34 | 3400 lb、無風。Pressure Altitude、RPM、MAPごとにISA -30℃、ISA、ISA +30℃のPWR/KTAS/GPHを掲載 | 159行を完全転記・独立比較し、Issue #15添付のPWR→ISA偏差→高度補間6,419行を全行再現。宮崎65% Best Powerのexact適合性とGolden照合は未完了 |
| 版管理 | SR22-POH-A1 LOEP | 文書はRevision A1、性能頁5-30〜5-34はReissue A | 版は確認済み。対象機固有の承認文書・Supplementとの適用性確認は未完了 |

## 補助的な公開一次資料

| 分野 | 公式資料 | 公開資料で確認した事項 | 判定 |
| --- | --- | --- | --- |
| NAV2の教育範囲 | 航空大学校 [教育訓練の内容](https://www.kouku-dai.ac.jp/02_enter/02.html) | NAV2に航法ログ作成、燃料計算、飛行前ブリーフィング、航法計画演習が含まれる | 対象範囲を補強 |
| G5/G6の区別 | 航空大学校 [平成30事業年度 業務実績等報告書](https://www.kouku-dai.ac.jp/cgi-bin/upload/1059_H30gyoumujissekihoukokusyo.pdf) p.91 | 帯広SR22はG5、宮崎SR22はG6 | R71_02の帯広G5値を宮崎G6値として転用できないことを確認 |
| 宮崎G6航法ログ | 航空大学校 [令和元事業年度 業務実績等報告書](https://www.kouku-dai.ac.jp/cgi-bin/upload/1123_gyoumujissekitouhoukokusyo_R1d.pdf) pp.94, 100 | 宮崎版航法ログをSR22性能諸元へ変更しG6/G1000を追記、POH/AMMに沿って教育内容を更新 | CAC-REV19およびSR22-POH-A1との関係を補強 |
| 目的地TAF | NOAA Aviation Weather Center [Data API](https://aviationweather.gov/data/api/) | 目的空港ICAOのdecoded TAFと時刻区間別風を取得できる | 到着予定時刻のNAV LOG最終行への表示だけに使用。到着区間計算は常にCALM |
| WCA符号 | 国土交通省航空局 [航空従事者学科試験問題 A3CC011930](https://www.mlit.go.jp/common/001279240.pdf) pp.25〜26 | WCAはTCからTHへの角度で、TCから右への修正をプラスとする | 右WCA正を裏付け |
| VFR巡航高度 | 国土交通省航空局 [2023年1月期 航空従事者学科試験問題 A3CC042310](https://www.mlit.go.jp/koku/content/001582752.pdf) p.18 | 地表・水面から900 m以上のVFR巡航高度選定 | 性能表検索用PA Policyとは別規則 |
| 公開ガイドの限界 | Cirrus Aircraft [2023 Owners and Pilots Quick Reference Guide](https://cirrusaircraft.com/wp-content/uploads/2023/05/SR-Owners-Guide-Digital.pdf) pp.2〜3 | 同ガイドはPOH/AFMではなく、必須運用・性能情報にはPOH等を参照する | 公開ガイドを性能原典として使用しない |

## なお未確認の実装Policyと適合性

次の事項は、原則または入力表を確認できたものを含め、現時点ではAutoNavLog固有の実装Policy
または未完了の適合性確認です。

- 対象機の製造番号、日本承認AFM/POH、SupplementとSR22-POH-A1の適用関係。
- 性能CSVの全行転記、独立再抽出、各ファイルのSHA-256は確認済み。ただし、上昇表に
  収録していないKIAS/FPM、巡航表の85%超非推奨flag、fairing・A/C・EVS補正の扱い、
  対象機適用性、およびGolden NAV LOGとの一致は未確認。
- NAV LOG計算でQNH補正を行わず`PA = MSL`とするプロジェクト定義。
- 上昇表の500 ft線形補間、表端から500 ft以内の高度外挿、POHの温度補正との対応。
- 巡航表のPWR→ISA偏差→高度という補間順序、0.1単位のties-to-even、および外挿禁止。
  規程が要求する「計画に近い条件のうち不利」を満たすことのGolden照合。
- WGS84測地線、Leg中点での気象照会、RCA/EOCを物理Leg内で分割するexactアルゴリズム。
- Loss Timeを機上修正値として地上の時間・Forecast・燃料から除外する扱い。
- 目的地TAFの卓越風を到着予定時刻へ合わせ、NAV LOG最終行への表示だけに使用する扱い。
- MSM風・気温の時間/空間補間、同一Forecast Runで最大5回反復して30秒未満を収束とすること。
- EOCの追加1分、直前巡航Legだけへの時間持越し、0.5 NM未満の変針点snap、および
  物理Leg小計/Calculation Zone内訳という表示Policy。
- 方位1°、距離0.5 nm、時間0.5分という記入単位に対するhalf-upのtie処理、燃料0.1 galの
  一律half-up、および丸めを適用する計算段階。
- SEA・地形機能はv2.6の対象外。規程が求める気象・航空情報・経路障害物の確認。
- 別添8-1原本とのレイアウト一致、校内でのソフトウェア承認、検証済みGolden NAV LOGとの
  end-to-end一致。

これらが完了するまで、AutoNavLogの出力は非公式の地上準備・転記補助であり、航空大学校の
承認済みNAV LOGまたは運航資料とは表示しません。
