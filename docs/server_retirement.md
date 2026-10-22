# 旧サーバ廃止の準備と救済手順 (#146)

**現時点では停止不可。** この文書と救済ツールは準備用であり、旧runtimeの停止、
移行受付終了、公開、定期運用の有効化を承認するものではない。
[#146](https://github.com/Yuto-24/AutoNavLog/issues/146) の直接前提は
[#123](https://github.com/Yuto-24/AutoNavLog/issues/123) のproduction automation受入。
移行仕様は [Legacy migration](legacy_migration.md)、配信・監視は
[Static operations](static_operations.md) を正本とする。

## 2026-10-23 JSTの準備状況

| 項目 | 確認済みの範囲 / 停止前に残ること |
| --- | --- |
| Static / Browser / MSM / TAF / Account移行 | #121 / #122 / #144 / #145 / #183 / #186 はclosed。機能の受入完了であり、全ownerの移行完了を意味しない |
| 定期公開・監視 | `STATIC_AUTOMATION_ENABLED=false`、monitor variableなし。直近scheduleは [39791264728](https://github.com/Yuto-24/AutoNavLog/actions/runs/39791264728) / [39807114879](https://github.com/Yuto-24/AutoNavLog/actions/runs/39807114879) ともskipped。成功更新2回の代わりにはならない |
| 無人quota収集 | [PR #274の10/22 audit](https://github.com/Yuto-24/AutoNavLog/blob/d98d558eb71b12a049bd8239b5fdc4a656c5e6e3/docs/evidence/issue-123/unattended-readiness-2026-10-22.md)の未実装native adapter・無人Google認証が残る。今回もcache storage-limit GETがHTTP 402。課金有効化、未知値のゼロ化、手入力reportへのfallbackで解消しない |
| 現行公開指定 | 01:50 JSTのcurl GETでclean v1.20.1 / `a4de2a226dfa205b8be42fb62a710a1b06271ed8`、migration URL `https://navlog.yuto24.com`。GitHubの指定と一致。catalogは622,770 bytes、SHA-256 `6f871a4c8bfba80537cce5af36530570375f71cd7558d67e26f75373b596c269`、期限10/22 13:06:53 JSTで失効済み。Pythonの直接取得は拒否されたがcurlの実応答で確認。10/22の成功を現時点の鮮度へ転記しない |
| 初回ログインの旧サーバ依存 | `web/src/createApplication.ts` / `legacyMigration.ts`。移行URL設定中はfresh device / 未確認accountが旧status APIへ依存する。既存端末のactivation markerだけで停止可としない |
| 救済 | `scripts/legacy_rescue.py`でoffline snapshot、hash照合、owner単位の取り出しを実装。fixtureで実repository・Last Calculation・既存移行receiptの復旧を検証。実サーバの最終backup・別媒体からの復元は未実施 |
| 移行受付終了日・保持期限 | **未定**。operator回答により未設定を停止前の必須条件として維持。既定の日数・終了日は置かない |
| 公開・停止 | 本準備では未実施。#123完了、実backup、切替受入、対象を特定した明示承認が必要 |

PR #274は無人監視の一部修正・監査であり、そのmergeだけで#123は完了しない。
#159 / #160 / #161 / #187、branding、任意のUI改善を廃止条件に追加しない。

## 停止までの順序

1. #123の不足adapter・無人credentialを実装/設定し、current evidenceで監視を受入する。
   native evidenceを取得できない場合は取得不能の根拠と未完了gateを残す。
   設定変更・監視有効化・定期公開は個別に承認を得る。
   旧catalog期限を跨ぐ実scheduleの成功公開を2回以上確認する。
   通知到達と同一origin rollback/Local保持の既存受入は範囲内で再利用できる。
2. operatorが移行受付終了日時（timezone付き）、救済保持期限、保管責任者、暗号化先、
   本人確認手段、削除対象と手順を決める。利用者へ終了日と救済窓口を事前告知する。
   本文言は本手順では決めない。UIに追加する場合はAGENTS.mdのGeminiルールに従う。
3. #123受入後、別途承認された窓で旧serverの新規link/編集/計算/移行を静止させる。
   matsuの実Compose project/container/volume、Tunnel service、他アプリとの共有を
   operatorがread-only inventoryで特定する。ローカル8123のComposeを本番と混同しない。
   別operatorも含め書込を排除し、実行中移行がないことを確認する。
   `MIGRATING`は時刻が過ぎただけで完了としない。既存移行手順で状態を解決する。
4. 下記の最終snapshotを採取し、別媒体へ暗号化して保存、別の場所へ復号して再検証する。
   実ownerの代表データで取り出し/復元を確認し、元volumeと対応台帳を保持する。
   件数・識別子・移行状態はprivate記録へ残す。公開PRへProject/owner/SQLiteを載せない。
5. migration URLなしのStatic候補を、明示的に選んだアプリSHAで作る。
   移行終了はMSM更新とは異なる公開設定の変更として承認を得る。
   fresh MSM・artifact・target/競合確認・Auth/Sync/Localを含む公開gateを満たす。
   **初回ログインの旧server依存を外した候補の公開・受入を、恒久停止より先に行う。**
6. canonical originでfresh profileのGoogleログイン・同期済みProject/Last Calculation復元、
   未ログインLocal保存/計算、Sync障害・通信断時のLocal保持を確認する。
   ブラウザ内で旧hostへの通信を拒否しても主要workflowが成立し、旧API通信がないことを確認。
   同じアプリ版で移行URLだけを外す場合でもfresh profileの実Google認証は省略しない。
7. 承認された旧app container/processと専用Tunnel公開経路だけを停止する。
   共有Tunnel全体を停止しない。volume、救済backup、復旧用image/sourceを削除しない。
   Access/DNS/旧host案内、Firebase authorized domainsの不要分は別の具体的変更として扱う。
   `navlog.yuto24.com`の一括redirectは移行受付中に設定しない。
8. 停止後もcanonicalのAuth/Sync/Weather/Localと定期更新を確認し、#146/#116へ証拠を記録。
   物理serverの消去・契約解約とbackup期限終了時の削除は、対象を特定した別承認で行う。

## 移行URLを外す公開の境界

既存Static builderは`VITE_LEGACY_MIGRATION_URL`未設定を許容し、`release.json`からも省略する。
アプリ起動側も未設定なら旧status APIを呼ばない。別の認証方式・移行APIの移設は不要。
ただし、移行期間中にこの設定を外すとLINKEDのデータ引継ぎを省略するため、先に期間終了・
backup・救済手順が必要。古いタブは旧設定を持つので再読み込みの案内も必要。

MSM専用`static-production.yml`はcanonical設定一致を必須にしており、この切替には使わない。
`app_release.sh`もcanonical一致を維持するため、初回の設定削除には使わない。
本準備で、廃止後のcanonicalとGitHub variableの両方にmigration URLがない場合の
アプリ更新を扱えるようにした。variable不在は完全paginationで確認し、他の5設定は必須。
読取失敗や片側だけの削除を不在として通さない。初回切替は
[Static productionの手動公開](static_production.md)を使い、以下をレビュー対象にする。

- 切替前のcanonicalとGitHub variable、候補の公開設定差分。削除はmigration URLだけ。
- 同一アプリSHAでの設定切替か、別途互換性確認済みSHAへの変更かを明示。
  アプリ更新・Rules変更を廃止準備へ暗黙に混ぜない。
- 旧serverは静止/復旧可能な状態で保持。定期公開を停止し、workflowとPagesのidleを確認。
- アプリ候補をmigration URLなしでbuild/check、全artifact・実MSM・browser gateを実施。
- canonical反映を確認後、GitHubのmigration variableも削除して一致させる。
  反映順に一時不一致が生じる間は定期公開を再開しない。
- MSM gateは同設定が両側に存在しなければ従来どおり照合できる。
  後続の`app_release.sh`も同じ設定一致条件で更新できる。

失敗したら変更済み/未変更/UNKNOWNを記録する。自動rollback/再公開/旧server再公開をしない。
移行URLを含む旧artifactへ戻すとfresh loginが旧serverに依存するため、停止後の通常rollback先は
**移行URLなしで受入済みの同origin artifact**とする。旧volumeへの書戻し・台帳の手編集・
NAVMATE_ACTIVE解除はrollback手段にしない。

## Offline救済ツール

Python 3.12の標準ライブラリだけで実行できる。ネットワーク接続、旧HTTP server、
Firebase credentialは不要。ツールはruntimeを停止せず、quiescenceを自動証明しない。
最終snapshotは書込停止済みvolumeのread-only mount、またはその一貫したoffline copyから取得する。
稼働中volumeをコピーして`--quiescence-reference`に任意文字列を入れても停止gateは成立しない。

採取対象は`projects/`、`owners/`、`account-links.sqlite3`。SQLiteはWALを含むprivate copyを
作ってからbackup APIを使うため、元DBは開かない。receipt、link、owner marker、Saved、draft、
Latest、Last Calculation、既存fallbackを保存する。Weather、Cookie/session、秘密鍵、環境設定は
対象外。indexを正本にしてProject directoryを落とさない。

以下の値はoperatorが決定・確認した実値に置き換える。日時は例示の既定値ではない。

```sh
python3 scripts/legacy_rescue.py snapshot \
  --source "$OFFLINE_STORAGE_ROOT" --destination "$NEW_PRIVATE_BACKUP" \
  --migration-ended-at "$APPROVED_MIGRATION_END" \
  --retain-until "$APPROVED_RETENTION_END" \
  --source-revision "$LEGACY_APP_FULL_SHA" \
  --quiescence-reference "$PRIVATE_STOP_EVIDENCE_REFERENCE"
```

source/destinationは別tree、destinationの親は既存directory、destination自体は新規が必要。
timezoneなし日時、未来の移行終了、過去の保持期限、短縮SHA、空の静止証拠は拒否する。
symlink/hardlink/特殊file、SQLite破損/未知schema、MIGRATING、採取途中のsource変化も拒否する。
Project/owner JSONが破損していても全体snapshotには元のbytesを残すが、ownerを判別できない
個別取り出しは拒否する。SQLiteが破損した場合は成功manifestを発行せず、元媒体を保持して
別のprivate forensic recoveryへ引き継ぐ。検証を外して成功扱いしない。

生成directoryは0700、fileは0600。**暗号化はしない**ため、管理対象の暗号化媒体で扱う。
出力の`manifest_sha256`をbackupとは別の信頼できるprivate記録へ保存する。
同じbackupから後で計算し直した値は真正性の確認にはならない。
別媒体へ退避/復号した後、その別記録のdigestで全fileを照合する。

```sh
python3 scripts/legacy_rescue.py verify \
  --bundle "$RESTORED_PRIVATE_BACKUP" \
  --expected-manifest-sha256 "$SEPARATELY_RECORDED_MANIFEST_SHA256"
```

`VERIFIED`はinventory/bytes/hash/台帳構造の確認結果。移行完了・本人確認・外部保管・
復旧成功・停止承認を意味しない。`retirement_authorized`は常にfalse。
失敗はexit 1 / BLOCKED。生成途中のdirectoryは再利用せず、privateな失敗物として扱う。
ツールは元データ・既存outputを上書き/削除しない。失敗物の削除も対象を確認して行う。

## 本人確認後の個別救済

1. privateな旧owner/link台帳と本人確認を使って対象ownerを確定する。
   email一致や本人申告のdigestだけで所有者を認定しない。Googleの既存linkがあれば照合する。
2. 保持期限内の全体backupを別記録のdigestで検証し、管理側の新規private directoryへ取り出す。

   ```sh
   python3 scripts/legacy_rescue.py extract-owner \
     --bundle "$RESTORED_PRIVATE_BACKUP" --destination "$NEW_OWNER_RESCUE" \
     --owner-key "$VERIFIED_OWNER_STORAGE_KEY" \
     --expected-manifest-sha256 "$SEPARATELY_RECORDED_MANIFEST_SHA256"
   ```

3. Project全snapshotと計算結果のowner/idを照合し、該当ownerの台帳/receipt/markerだけを採用する。
   全ownerを含むindexは持ち込まず、必要なら一時作業copy内で既存repositoryが再生成する。
   SQLiteはowner限定の新規DBを構築する。全DBから他ownerをDELETEして配る方式は使わない。
   混在owner・破損JSON・foreign/missing Projectを指すmarkerは個別取り出しを止める。
4. 取り出し物は管理者用であり、そのまま利用者へ渡したりWeb公開したりしない。
   digest確認済み取り出し物を保存し、復旧作業はさらに別copyで行う。
   source SHAのcode/image・reference dataを保持し、既存`LegacyMigration`変換・fingerprint検査・
   receipt/CAS/collision処理を再利用する。実体は一時的な隔離管理作業で、常時稼働サーバに戻さない。
5. Google link済みLINKED ownerは本人のGoogle認証を別途確認した管理手順で既存移行を再開する。
   テストではoffline owner copyから実変換・Last Calculation・lost-ACK receipt再開を確認するが、
   本ツールはGoogle認証・Firestore uploadを実行しない。実uploadは対象account/データを示して
   別承認し、現行Rules・世代削除・remote-aheadを再確認する。正常アプリで復元を受入する。
6. 未link ownerの本人/Google対応、削除済みaccount世代、NAVMATE_ACTIVEなのに同期先がないケースは
   自動再link/再uploadしない。既存APIで状態を戻さず、個別の復旧計画と明示承認を必要とする。
   旧通常認証を再公開しない。適用不能なら救済未完了として原本を保持する。
7. 復旧確認後、一時runtime/作業copyの終了と削除対象をprivate記録へ残す。
   原本は合意済み保持期限まで保持。期限後は全保管媒体・暗号鍵・作業copy・複製を棚卸し、
   対象を明示して承認を得た削除を行い、結果を記録する。filesystemのunlinkだけで物理消去を
   保証したとは報告しない。ツールに自動削除機能はない。

## 残す開発資産

Docker Composeはlocal/Legacyのサポートruntimeとして維持する。CIのruntime image検査は
本番deployではなく、救済・回帰の検証でもあるため、廃止準備だけで削除しない。
現行workflowに旧サーバへの常時deployはない。Python Reference、differential tests、
Local Calculation regressionは #117/#125 の条件に従って残す。
不要な実ホスト上の起動unit/prewarm/cronは実inventory取得後に対象を特定し、承認された停止へ含める。
