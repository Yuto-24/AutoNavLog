# Data Provenance

性能CSVの正式な優先順位は、航空大学校の最新学生訓練実施要領、国土交通省承認版の
日本語飛行規程、英語版POHです。

各CSV行は出典ページを持ち、manifestは文書名、改訂日、照合先、ファイルSHA-256を
保持します。資料間の差異を検出した性能表は`VERIFIED`にせず、実行時に利用不能とします。
実行時にPDFは解析しません。

現行SR22 G6データは、P/N 13772-006 Reissue A（Revision A1のLOEP上、性能頁
5-30〜5-34はReissue A）から転記しました。上昇19行と巡航159行を別処理で再抽出し、
全行差分ゼロを確認しています。CSVのSHA-256、原典PDFのSHA-256、適用する
`climb_temperature_policy`は`data/performance/manifest.json`へ固定しています。
`VERIFIED`は数値転記とmanifest整合の状態であり、対象機への適用性、校内承認、または
Golden NAV2 LOGとのend-to-end一致を意味しません。

MSMの上空風・気温にはForecast Run、元URL、source hash、補間方法、格子・気圧面traceを
保存します。自動QNHはMSM海面更正気圧と検証済みPzs地形cacheから求め、
`MSM推定QNH`と表示します。Pzs cacheのpath・SHA-256と、MSM取得・補間・算出の来歴を
保持します。これは公式飛行場気象の観測QNHではなく、その確認を代替しません。取得・
算出不能時は1013.25 hPa等へ補完せず手入力を要求し、手動上書き後も自動値、そのlabel、
警告、根拠を削除しません。

各計算行のVariationは`DEPARTURE_LATITUDE_32N_V1`規則で決定し、
`variation_deg_east.automatic_metadata`へ元の物理Legの出発緯度、32.0°Nの閾値、
境界を北側へ含める条件、選択した緯度帯、採用値を保存します。判定不能な座標は+7/+8の
いずれにも補完せず`VARIATION_UNAVAILABLE` blockerとします。Projectの旧固定VAR項目は
保存形式の後方互換専用であり、この自動値の来歴には使用しません。

SnapshotはProject revision、入力、手動値、性能表version、Policy version、気象要求と
結果、パッケージversion、警告を含む不変JSONです。
