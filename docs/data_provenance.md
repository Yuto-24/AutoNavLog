# データ来歴

この文書は、AutoNavLogに同梱するデータについて、公開可能な範囲の来歴と制限を記録します。航空大学校の内部運用資料については、参照・検証した事実と採用済みAutoNavLog Policyだけを示し、章節、ページ、原文要約、hashなど原資料の内容を復元しやすくする対応情報は収録しません。詳細は[出典・provenance方針](source_policy.md)を参照してください。

## SR22 G6性能データ

実行時の性能入力は`data/performance`に同梱するCSVです。

現行SR22 G6データはCirrus Design SR22 Airplane Flight Manual / Pilot's Operating Handbook, P/N 13772-006 Reissue Aを基礎としています。Revision A1のLOEP上、使用しているSection 5の性能頁5-30〜5-34はReissue Aです。

- 上昇原表の節点を再抽出し、同梱CSVとの全行比較を行っています。
- 巡航原表159行を再抽出し、同梱CSVとの全行比較を行っています。
- 実行時には`data/performance/manifest.json`のSHA-256で同梱CSVを検証します。
- POH由来の直接値と、AutoNavLogによる補間・外挿Policyは区別してCalculation metadataへ保持します。
- `VERIFIED`は転記内容とmanifest整合を示すもので、対象機への適用性、運航承認、校内承認を意味しません。

## 空港・Route参照データ

`data/reference/default`は、空港、Route point、Check Pointの既定参照値を保持します。

空港の位置・標高等については公開AIP等の出典を保持します。一方、内部運用資料を参照して検証した場周高度等については、`AutoNavLog validated reference policy`として保持し、元資料のページ、hash、詳細な対応関係は収録しません。

参照値はProjectへsnapshotとして保存されます。後からmasterが更新されても、既存Projectの計算結果を暗黙に別の参照値へ切り替えません。

## RJFM参照パック

`data/reference/rjfm`は、RJFM専用の経路・空域・案内Policyに必要な固定参照値を保持します。

参照パックには次を含みます。

- AIP等の公開資料から確認したRJFM / RWY / MZEの参照値
- 国土交通省の公開告示から確認した宮崎特別管制区の参照値
- 国土交通省・国土地理院が公開する民間訓練試験空域の表示用参照
- AutoNavLogが採用するUMK / OVER FIELD / OMARUの参照座標と不確実性
- RJFM北行き専用Policyと利用者決定に基づく実装値

内部運用資料から確認した情報については、資料を参照して検証した事実だけを記録し、元資料の章節、ページ、ファイルhash、規則との対応表は公開しません。

UMK / OVER FIELD / OMARUの同梱座標は`UNVERIFIED_MAP_DIGITIZATION`として扱い、推定誤差を保持します。一致するKML座標がある場合は、同梱値より利用者のKMLを優先します。

RJFM参照パックはmanifestのpayload SHA-256で検証します。payload変更時は参照fingerprintも変わります。

## 公開資料

AIP、国土交通省告示、国土地理院API等、公開資料については、再現性と更新確認のためURL、取得日、適用日、SHA-256等を保持する場合があります。

国土地理院の民間訓練試験空域GeoJSONはWeb表示時にライブ取得します。変化するGeoJSON本文はversioned payloadのhash対象外で、NAV LOGやPCA制約の計算入力には使用しません。

## 気象

MSMの上空風・気温と地上気温にはForecast Run、元URL、source hash、補間方法、格子・気圧面traceを保存します。

目的地風はAviationWeather.govのTAFを出典とします。採用したTAFは目的空港情報として表示しますが、VREPから目的空港までの航法計算はCALM固定です。

## Project / Calculation

Projectは入力、手動値、選択した参照データを最新draftとして保存します。明示保存時にはcheckpointを更新します。

最後にBlockerなしで完了したCalculationはProjectごとに1件だけ保持し、計算時Project snapshot、CalculationOutcome、Forecast Run等の来歴とともに保存します。履歴を無制限に蓄積する方式ではありません。
