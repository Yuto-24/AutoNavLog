# 計算専用予備サーバー

通常MSM配信が期限切れ・取得不能・必要データ不足の場合、Localアプリは予備サーバーで気象取得と計算を行う。未ログインでも利用する。コードは既定で無効であり、この文書は本番設定の変更や公開承認を意味しない。

## 独立した境界

`PUT /api/forecast-backup/jobs/{UUID}`で計算を登録し、`GET`で同じjobを照会する。各jobはbrowser生成256-bit capabilityを`X-AutoNavLog-Request` headerで渡す。URL・結果・ログ・DBには生capabilityを残さない。別capabilityでの結果照会は404となる。Origin/CORSは追加制限であり、認証や濫用防止の代用ではない。

このendpointはLegacy session、saved Project、migration recordを読書きしない。Cloudflare Access、既存編集APIのowner照合、MIGRATING/NAVMATE_ACTIVEの操作lockは変更しない。Firebase identity/ACTIVE世代の権限を借りず、ログイン利用者も同じ隔離された計算専用境界を使う。Local側のアカウント境界はそのまま維持する。

## 制限

| 対象 | 上限・動作 |
| --- | --- |
| request | 256 KiB、body受信10秒、Project 240 KiB、route/section各64、visual reference128、TGL20 |
| 同時処理 | worker processあたり2計算。DB全体で実行/待機8件、clientごと2件 |
| 新規計算頻度 | clientごと10分6件・24時間20件。capabilityを変更しても共通 |
| HTTP頻度 | clientごと1分120要求。rate table最大4096行 |
| client key | 接続元IP（IPv6は/64）をserver固有秘密でHMAC。生IPはledgerに保存しない |
| result/ledger | result 2 MiB、全体64件、24時間保持。期限後は410/404、古いrequestを再実行しない |
| 実行 | child自身のalarmと親supervisorの両方でwall120秒、CPU110/115秒、address space2 GiB、1 file256 MiB、FD128 |
| 依頼期限 | 登録600秒。失われた実行を再起動後に再実行しない。browserは610秒まで同じjobを照会 |
| 一時data | job単位directory、完了/強制終了後に親が削除。server自体の異常終了で残った600秒超のdirectoryは次回計算前に除去 |

NAT共有利用者は同じclient制限を共有する。IPv4/IPv6分散攻撃への完全な防御ではないため、origin到達制限とedgeの追加rate limitは公開前に確認する。新規capabilityが全体queue上限を回避することはない。request/response本文やheaderをaccess logへ追加しない。

## 設定と未実施の本番有効化

- Server: `AUTONAVLOG_FORECAST_BACKUP_ORIGIN=https://navmate.yuto24.com`。未設定ならrouteを登録しない。
- Browser build: `VITE_FORECAST_BACKUP_URL=https://navlog.yuto24.com/api/forecast-backup/jobs/`。未設定なら通常経路の失敗時に未設定を表示する。
- Proxy: `AUTONAVLOG_FORECAST_BACKUP_TRUSTED_PROXIES`は既定空。指定CIDRからの接続だけ`CF-Connecting-IP`を信頼する。gatewayがheaderを上書きし、originへの直接接続を遮断できる場合にのみ設定する。未設定ではsocketの接続元に制限が掛かるため、proxy配下で利用者が同一clientに集約される。
- Runtimeはnative MSM依存を含む同一revision・同一参照/性能dataが必要。旧release/1.xを無変更で使えるという互換性保証はない。

別途承認が必要な公開対象は、既存の旧server-host `navlog.yuto24.com` の`/api/forecast-backup/jobs/*`のみ。許可methodはGET/PUT/OPTIONS、許可originは`https://navmate.yuto24.com`、許可headerはContent-Type/X-AutoNavLog-Request。旧画面・session・編集・保存・migration pathにAccess例外を広げない。host/runtimeの現在値照合、Access exception、origin防御、proxy header信頼、edge制限、同一engine/data、実MSM取得と最終coverage、正常復帰、監視が配信異常を引き続き検出することを承認/検証してから有効化する。

rollbackは専用pathの公開例外を閉じ、server origin設定とbrowser backup URLを解除して通常Local経路に戻す。保存dataとvolumeは削除しない。本PRはlive設定変更・merge・deploymentを実施しない。

## 再現可能な検証

`tests/integration/serve_forecast_backup_fixture.py`はloopback専用のテストserverで、通常のAPI/計算エンジンと歴史的portable MSMを使う。本番の気象取得成功を意味しない。`web/e2e/forecast-backup.spec.ts`は実browser adapter、Pyodide、server、Local保存/reload、正常復帰を通す。`tests/unit/test_forecast_backup.py`と`web/e2e/forecast-backup-unit.spec.ts`で隔離、上限、deadline、互換性、不正結果、応答喪失と入力/アカウント変更を検証する。
