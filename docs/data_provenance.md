# Data Provenance

性能CSVの正式な優先順位は、航空大学校の最新学生訓練実施要領、国土交通省承認版の
日本語飛行規程、英語版POHです。

各CSV行は出典ページを持ち、manifestは文書名、改訂日、照合先、ファイルSHA-256を
保持します。資料間の差異を検出した性能表は`VERIFIED`にせず、実行時に利用不能とします。
実行時にPDFは解析しません。

MSM値にはForecast Run、元URL、source hash、補間方法、格子・気圧面traceを保存します。
手動上書き後も自動値とその根拠を削除しません。QNHは常に`MSM推定QNH`と表示し、公式
QNHと表現しません。

SnapshotはProject revision、入力、手動値、性能表version、Policy version、気象要求と
結果、パッケージversion、警告を含む不変JSONです。
