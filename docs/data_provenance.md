# データ来歴

性能CSVの正式な優先順位は、航空大学校の最新学生訓練実施要領、国土交通省承認版の
日本語飛行規程、英語版POHです。

各CSV行は出典ページを持ち、manifestは文書名、改訂日、照合先、ファイルSHA-256を
保持します。資料間の差異を検出した性能表は`VERIFIED`にせず、実行時に利用不能とします。
実行時にPDFは解析しません。

現行SR22 G6データは、P/N 13772-006 Reissue A（Revision A1のLOEP上、性能頁
5-30〜5-34はReissue A）から転記しました。上昇原表19節点と巡航159行を別処理で再抽出し、
全行差分ゼロを確認しています。実行時の上昇CSVはIssue #15添付表に合わせ、原表節点間を
高度方向へ線形補間した500 ft刻み36行です。巡航CSVは原表159行を保持し、派生6,419行を
同梱せず、PWR、ISA偏差、高度の順で区分線形補間します。これにより添付の多次元拡張表
6,419行すべてを再現できることを独立比較しました。外挿は行いません。

CSVのSHA-256、原典PDFのSHA-256、適用する上昇温度・巡航補間Policy、およびIssue #15
添付3件のURL・SHA-256・用途は`data/performance/manifest.json`へ固定しています。
実行時に`PerformanceRepository.from_directory`が読み込み、内容を検証する対象は
`tables[].sha256`で宣言した同梱CSVだけです。`source_artifacts[].sha256`は添付資料を
採用した時点の参照用メタデータであり、実行時にURLを取得したり、その内容を再検証したり
しません。
各巡航結果には軸の上下限・係数、PWR補間corner、参照頁を保存します。
`VERIFIED`は数値転記とmanifest整合の状態であり、対象機への適用性、校内承認、または
Golden NAV2 LOGとのend-to-end一致を意味しません。

## RJFM北上UMK出発参照パック

`data/reference/rjfm`は、RJFMから大分方面へ北上するUMK/RCA例外と出発案内の
固定参照値を保持します。manifestは参照パック版とpayload SHA-256を固定し、起動時に
JSONの重複key、未知field、非有限値、座標範囲、出典参照、地図変換残差、payload hashを
検査します。計算に使う固定参照値は実行時に出典URLから更新せず、日付でも自動失効しません。
例外として、民間訓練試験空域は参照パックに固定した公式URLからWeb表示時だけライブGeoJSONを
取得します。URL・対象tile・用途制限はpayload hashの対象ですが、変化するGeoJSON本文は
hash対象外で、計算入力にはしません。

収録した根拠と制限は次のとおりです。

- 航空大学校宮崎本校運用課『宮崎空港及びその周辺における航空大学校所属機の
  訓練飛行実施要領』R6.5.1改正、有効日2024-05-01。添付PDFを制御資料とし、出発要領、
  場周高度、別添9のNewtabaru Routeを参照します。別添9はUMK、OVER FIELD、OMARUの
  座標数値を公表していません。
- AIP Japan RJFM AD 2の取得物は、JCABが発行者ですが、公式の機械可読AIPを作業環境で
  取得できなかったため一般公開ミラーのPDFを使用しています。統合PDFは2026-03-01版で、
  ARP・飛行場標高はAD 2.2（2026-03-01有効）、Runway座標・方位・標高はAD 2.12
  （2025-05-15有効）、MZE座標・DME標高・局偏差はAD 2.19（2018-11-08有効）によります。
- 宮崎特別管制区の水平境界、9 km除外円、原文の高度200–800 mは国土交通省の公式統合告示
  p.19（2020-11-05有効）によります。MZEの名称、周波数、概略位置は国土交通省の
  航空保安無線施設告示でも照合しました。
- 民間訓練試験空域は、国土交通省の確認案内からリンクされた国土地理院
  `kokuarea_minkankunren` GeoJSONのRJFM周辺z8 tileを表示します。KS4 Polygonだけを
  厳格に検証して描画し、取得失敗・形式不一致・上限超過時はfail-closedで非表示にします。
  これは表示専用のライブ参照であり、出発案内、NAV LOG、PCA制約の計算には使いません。
- UMK、OVER FIELD、OMARUは、別添9を6か所の庁舎位置で座標補正した上で
  シンボル中心をデジタイズした値です。庁舎座標は国土地理院住所検索APIを使用し、
  変換のRMS残差は0.033 NM、最大残差は0.043 NMでした。シンボル幅を含む各点の
  推定誤差は0.35 NMとし、`UNVERIFIED_MAP_DIGITIZATION`のままとします。一致する
  KML座標がある点は、この同梱値よりKMLを優先します。
- 案内判定に使う656–2,700 ft MSLは上下端を含む利用者Policyであり、告示の
  200–800 mを厳密にフィート変換した値ではありません。適用トリガ半径1.0 NM、旋回バンク
  20°、位置0.01 NM・高度10 ft・接線角0.1°の
  許容差も、AIPや訓練要領から引用した値ではなく、利用者決定の実装Policyです。RWY09の
  延長旋回を左、RWY27の初期・延長旋回を右とする指定、NAV LOG上でRJFM→OMARUを
  1親Legとして扱う指定も、2026-08-17の利用者決定であり添付資料の記載とは区別します。
  旋回開始点のMZE DMEは計算結果の案内値であり、判定閾値や警告には使用しません。

案内結果には参照パック版、payload SHA-256、計算入力fingerprint、訓練要領・AIP RJFM・PCA告示・
利用者Policyの有効日を保存します。payloadが変われば計算入力fingerprintも変わります。これは
計算時に使用した参照snapshotを追跡するためであり、
現行性、公式承認、飛行可否を保証しません。また、地形、障害物、参照パックで定義していない
他空域、ATC指示は参照パックと出発経路ソルバの対象外です。

MSMの上空風・気温と地表面気温にはForecast Run、元URL、source hash、補間方法、格子・
気圧面traceを保存します。地表面気温はLsurfの`tmp_surface`を使い、気圧面気温を空港標高へ
外挿しません。QNH、MSLP、METARによる補正は取得・保存・計算の対象外です。

巡航性能metadataにはPOH表のKTAS、ノーズフェアリング補正、A/C補正、最終KTASを分けて
保存します。ノーズフェアリングなしの`-10 KTAS`とA/C ON時`-2 KTAS`は利用者提供転記です。
A/C装備時に別POH Supplementが適用されることは
[Cirrus公式Supplement案内](https://store.cirrusaircraft.com/sr22-supplement-13772-127%2C-air-conditioning/5637369215.p)で確認していますが、
Supplement本文を収録していないため未確認のページ番号は付けません。

各計算行のVariationは`DEPARTURE_LATITUDE_32N_V1`規則で決定し、
`variation_deg_east.automatic_metadata`へ元の物理Legの出発緯度、32.0°Nの閾値、
境界を北側へ含める条件、選択した緯度帯、採用値を保存します。判定不能な座標は+7/+8の
いずれにも補完せず`VARIATION_UNAVAILABLE` blockerとします。Projectの旧固定VAR項目は
保存形式の後方互換専用であり、この自動値の来歴には使用しません。

目的地風はAviationWeather.govのTAFを出典とし、目的空港ICAO、到着予定時刻、TAF発表時刻、
有効期間、変化区分、風向・風速・ガスト、TAF原文をWeb sessionへ保持します。採用した風向・
風速と出典metadataはCalculationOutcomeの`DESTINATION_INFO`表示投影へ含めますが、到着区間
計算へは使いません。再計算のたびに到着予定時刻へ合わせて選び直し、取得失敗時は目的空港
情報行だけを`UNAVAILABLE`とします。VREP→目的空港は常に固定CALMで計算します。

SnapshotはProject revision、入力、手動値、性能表version、Policy version、気象要求と
結果、パッケージversion、警告を含む不変JSONです。
