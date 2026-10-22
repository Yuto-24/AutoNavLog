# AIP地図表示用データ（2026-10-29有効）の出典・取込準備

## 確認済み事項

2026-10-22付の航空情報センター配布「AIPに関する地図表示用データ」のKML・Shapefile（各25レイヤー）を確認した。適用日は**2026-10-29**。全5,276地物で、ShapefileのCRSはWGS84（EPSG:4326）、全レイヤーのgeometryは空・不正ともに0件。元ファイルのSHA-256とレイヤー別件数を[`source_manifest.json`](../../data/reference/aip/20261029/source_manifest.json)に記録する。

出典：国土交通省航空局 航空情報センター、[SWIM portal](https://top.swim.mlit.go.jp/swim/) → AIPファイルダウンロードサービス → `3_AIP(KML, Shapefile)`。配布ファイルを検査して本記録を作成した。原本と加工データを混同せず、[SWIM利用規約](https://top.swim.mlit.go.jp/swim/terms.html)に従い出典と加工の有無を表示する。第三者の権利および個別の提供条件も必要に応じて確認する。

## #149 に採用する入力元

| 配布レイヤー | 地物数 | 利用目的 |
| --- | ---: | --- |
| Civil_training_and_testing_areas | 132 | **訓練空域（宮崎周辺のKS4を含む）** |
| JSDF_training_and_testing_areas_HIGH / LOW | 81 / 18 | 自衛隊の訓練空域 |
| Restricted_area_Airspace_restrictions | 89 | 飛行制限・空域制限 |
| Approach_control_area_CIVIL / MIL | 94 / 79 | 進入管制空域 |
| Control_zone_and_Information_zone | 128 | 管制圏・情報圏 |
| ACC_Sector_at_or_above_FL335 / below_FL335 | 50 / 67 | ACCセクター |

航空大学校の訓練時に利用する公示訓練空域は、**今回のAIPの`Civil_training_and_testing_areas`と同じもの**として扱う。学校独自の境界を別に作成したり、#188で重複取り込みしない。`AREA=Kyushu(KS)`のうち、KS4-1、KS4-1/4、KS4-2、KS4-3、KS4-5、KS4-6、KS4-7、KS4-8の8地物を確認した。KS4-2等は`NAME2`/`RMK_ALT`に高度帯別の注記を持つため、ポリゴンを帯ごとに重複生成しない。公示空域と単なる参考MAPデータを区別する。

**収録されない・未確定：** TCAは配布説明書に今後追加予定と明記され、このZIPに独立レイヤーはない。ACAは独立レイヤー名として確認できておらず、他レイヤーとの対応を確認する。AIPデータにはNOTAMによる一時変更や現時点の空域使用状況は含まれない。

## 表示仕様（利用者決定）

- 経路の新規作成中、再編集中、確定後のPlanning画面で、訓練空域と公示空域を同じMAPへ表示する。
- 初期ON、Layer単位でON/OFF。表示設定は端末Localにのみ保存し、Project / Account Syncへ保存しない。
- エリアのクリック・Hoverから詳細表示やRouteへの追加は行わない。MAP上の地点追加・編集操作を妨げない。
- 配布データは共有MAP Reference Dataであり、#176の経路KML Importや個人Overlayとは別責務。

## 既存境界と更新時の注意

`web/src/rjfmAirspace.ts`が参照する国土地理院のライブKS4表示データ、および`data/reference/rjfm-inbound-guidance`にあるAIP由来のKS4-3計算用境界が先行実装として存在する。公式AIPの新たなMAPレイヤーを登録しても、**計算用境界、境界誤差、入力Fingerprint、可用性判定を自動で置き換えない**。差分照合を経て、用途ごとの責務を維持すること。

初期版の配布は既存方針に従い、version付きStatic Web artifactとしてApp releaseに同梱する。AIRACごとの有効日管理、出典・更新履歴、検証を行い、**2026-10-29より前にこの版を現行有効データとして表示しない**。

## この登録作業で未完了の項目

この記録とmanifestは**取り込みの準備資料**であり、元ZIP本体およびMAPで使用するGeoJSON/その他のruntimeデータはGitHubへ登録していない。元ZIPをファイル名・SHA-256照合のうえRepositoryへ取り込み、共通データに正規化し、レイヤー表示のブラウザ検証を行う作業は#149の未完了条件として残る。#188はAIP以外の追加配布データを扱う。
