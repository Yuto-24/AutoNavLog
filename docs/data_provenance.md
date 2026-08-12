# Data Provenance

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

MSMの上空風・気温にはForecast Run、元URL、source hash、補間方法、格子・気圧面traceを
保存します。自動QNHは最新2時間以内の検証済みMETARを基準に、同一Forecast Runの
MSM MSLP変化量を加えた `METAR_TREND_CORRECTED` として保存します。基準METAR時刻、
対象時刻、両時刻のMSLP、変化量、API response hashと補間traceを保持します。METARが
欠測・不整合・古い場合は `MSM_MSLP_ONLY`、MSMも取得不能なら `MANUAL` へ切り替えます。
Pzs・外部DEMは使用しません。これは公式飛行場予報QNHではなく、その確認を代替しません。
手動上書き後も自動値、方式、警告、根拠を削除しません。

各計算行のVariationは`DEPARTURE_LATITUDE_32N_V1`規則で決定し、
`variation_deg_east.automatic_metadata`へ元の物理Legの出発緯度、32.0°Nの閾値、
境界を北側へ含める条件、選択した緯度帯、採用値を保存します。判定不能な座標は+7/+8の
いずれにも補完せず`VARIATION_UNAVAILABLE` blockerとします。Projectの旧固定VAR項目は
保存形式の後方互換専用であり、この自動値の来歴には使用しません。

SnapshotはProject revision、入力、手動値、性能表version、Policy version、気象要求と
結果、パッケージversion、警告を含む不変JSONです。
