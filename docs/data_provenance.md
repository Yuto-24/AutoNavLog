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
各巡航結果には軸の上下限・係数、PWR補間corner、参照頁を保存します。
`VERIFIED`は数値転記とmanifest整合の状態であり、対象機への適用性、校内承認、または
Golden NAV2 LOGとのend-to-end一致を意味しません。

MSMの上空風・気温にはForecast Run、元URL、source hash、補間方法、格子・気圧面traceを
保存します。自動QNHはMSM海面更正気圧と検証済みPzs地形cacheから求め、
`MSM推定QNH`と表示します。Pzs cacheのpath・SHA-256と、MSM取得・補間・算出の来歴を
保持します。これは公式飛行場気象の観測QNHではなく、その確認を代替しません。取得・
算出不能時は1013.25 hPa等へ補完せず手入力を要求し、手動上書き後も自動値、そのlabel、
警告、根拠を削除しません。

SnapshotはProject revision、入力、手動値、性能表version、Policy version、気象要求と
結果、パッケージversion、警告を含む不変JSONです。
