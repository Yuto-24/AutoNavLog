# 一次資料の確認状況

この文書は、AutoNavLogが参照した資料群の確認状況を公開可能な粒度で記録します。

航空大学校の内部運用資料については、設計・検証時に参照した事実だけを記録します。章節、ページ、見出し、原文要約、規則と実装値の対応表、ファイルSHA-256など、原資料の内容を再構成しやすくする詳細provenanceはこのRepositoryには収録しません。

AutoNavLogが実際に採用する値・計算方法は[計算規則](calculation_rules.md)を正本とします。出典情報の公開範囲は[出典・provenance方針](source_policy.md)を参照してください。

## 航空大学校の内部運用資料

NAV LOGの作成・記入、燃料計画、RJFM周辺の訓練運用、他空港運用等について内部資料を参照しています。

公開Repositoryでは次だけを示します。

- 内部資料を設計・検証時に参照したこと
- その結果としてAutoNavLogが採用しているPolicy
- Policyが公式承認を意味しないこと

原資料の特定箇所とAutoNavLogの数値・規則を1対1で対応付ける情報は公開しません。

## SR22 AFM / POH

性能データはCirrus Design SR22 Airplane Flight Manual / Pilot's Operating Handbook, P/N 13772-006を参照しています。

現在使用している性能表はSection 5のpp.5-30〜5-34で、Revision A1のLOEP上はReissue Aです。

- pp.5-30〜5-31: `Time, Fuel, & Distance to Climb`
- pp.5-32〜5-34: `Cruise Performance`

性能CSVは原表から再抽出して全行比較を行い、runtimeではmanifestのSHA-256で同梱データを検証します。詳細は[データ来歴](data_provenance.md)を参照してください。

## 公開航空資料

AIP、法令、国土交通省告示、国土地理院API等の公開資料については、検証や更新確認に必要な範囲で資料名、URL、適用日、取得日、ページ、SHA-256等を保持する場合があります。

これらは内部資料とは分けてprovenanceを管理します。

## 確認状態の意味

`VERIFIED`等の内部状態は、AutoNavLogが期待する資料・データとの整合を確認したことを示します。航空大学校、航空局、機体メーカーその他の機関によるAutoNavLogの承認を意味しません。

対象機へのPOH適用性、最新の運航資料、気象・航空情報、ATC指示は利用時に別途確認が必要です。
