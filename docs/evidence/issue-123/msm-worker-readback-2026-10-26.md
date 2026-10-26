# PR #290: Worker軽量化と管理API read-back（2026-10-26 JST）

## 目的と範囲

10月末までのNavMate新環境提供開始へ向け、Worker軽量化と実行可否を確認する。
本番deploy、Cron再開、実dispatch、mergeは行わない。
厳密なDO起動回数管理は後続へ分離し、共有無料枠・課金監視は#281へ戻す。
これらを今回の配置条件にしない。Worker CPUは実行可否の検証対象に残す。

## 401調査: 確認済みと原因の確度

- PRで未確認とされた直近の失敗は19:31 JSTの
  `GET /client/v4/accounts/<account>/workers/scripts/autonavlog-msm-trigger/settings`。
  この最初のGETでHTTP 401 Unauthorizedになり、続く`schedules`には到達していなかった。
- 認証方式はWrangler保存済みOAuth access tokenを直接読み、`Authorization: Bearer`で送る方式。
  `CLOUDFLARE_API_TOKEN`、Global API Key、Worker側GitHub Secretを使った失敗ではない。
- 失敗時はPython `urllib.error.HTTPError: HTTP Error 401: Unauthorized`のみ保存され、
  CloudflareのJSONエラー本文・エラーcode・request IDは保存されていない。
  当時のtokenの有効期限も保存されていないため、歴史的な401の期限切れ・失効を断定できない。
- 直接読み出すコードには有効期限検査・OAuth refreshがない。これはWranglerの通常認証経路と異なる。
  同日09:22 JSTにも同じ直接GETが401となり、`wrangler whoami`の通常refresh後の再GETでは成功した履歴がある。
  保存済みaccess tokenの期限切れ・未更新が有力だが、直近19:31の正確なprovider理由は未確認。
- 新規発行・登録・scope変更はせず、現在有効な同じWrangler OAuth認証で再取得した。
  今回使った保存済みaccess tokenの有効期限は20:49:17 JST、再取得は20:33 JST。
  期限・scopeのメタデータだけを確認し、token・refresh token・Secret値は出力していない。

## 復旧結果（20:33 JST）

| GET API末尾 | HTTP | 確認した内容 |
| --- | --- | --- |
| `/settings` | 200 / success=true / errors=[] | MODE=observe、STATEは既存namespaceと一致、DO bindingなし |
| `/schedules` | 200 / success=true / errors=[] | schedules=[] |
| `/deployments` | 200 / success=true / errors=[] | 18:15 JSTの停止version `62d08cdc-e151-4240-9b35-68e7dcc51109` が100% |
| `/secrets` | 200 / success=true / errors=[] | 名前はGITHUB_TOKENのみ。値は取得・表示していない |

API全体の恒常的な権限不足は今回のGET成功とは整合しない。管理APIのread-backは復旧したが、
新しい認証の作成で原因を回避したものではない。旧401のJSON詳細は復元できていない。
設定の`limits`は未指定で、これをCPU無制限とは扱わない。

今後の管理read-backでは有効期限を先に確認し、必要なら既存Wranglerの通常OAuth refreshを使う。
通常refreshに失敗する場合、新規login・token作成・Secret登録・権限変更を自動で行わず、
必要な操作と理由を示して止める。401/403は別credentialへの自動切替で隠さない。

## 今回の構成と後続

Workerは2,048 bytes以内の同一deploymentのhintで候補だけを判断し、catalog/release/hash/
全payload・上流Run探索・公開成功検査をActionsへ移す。DO実装・export・binding・migrationを外した。
KVへのPOST前の回数・時刻記録、8要求/UTC日・90分以上の抑制、失敗/不明の消費、
全active状態、Actions履歴、Actions側の同Run上限・再公開防止を維持する。
KVで同時tick・古いreadの厳密なadmissionは保証しない。この保証とrolling 24時間上限は後続設計。
詳細は[運用手順](../../msm_external_trigger.md)の後続節に記す。

アカウント全体の無料枠・課金監視は#281の後続。未確認を適合済みにせず、今回の公開前提に戻さない。
公式Workers制限はFreeのCronもCPU 10msであり、CPUは今回の検証対象に残る。
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)、
[KVの結果整合性](https://developers.cloudflare.com/kv/concepts/how-kv-works/)（今回再確認）。

## 未確認と次の限定実測

軽量版のCloudflare上のCPUは未測定。旧版FRESHの17ms・旧526の接続先不明は解決済みとしない。
ローカルworkerdの実行成功・bundle縮小・dry-runをprovider CPU合格や本番公開受入へ読み替えない。

次は別途承認のうえ、固定commit・既存Worker/KV/Secret、MODE=observe・外部opt-in無効で
配置→最大15分のCron反映待ち→最大30分/2tickの観測→停止→read-backを一組として行う。
hint未配置なら先に新しい完全Runでhint付き公開を行う別承認が必要。同Run再公開で移行しない。
CPU 10ms超過、exceededCpu、UNKNOWN、HTTP/IO/BODY_LIMIT、想定外設定、期限、2tick到達で停止する。
FRESHとGitHub到達経路を別々に記録し、未測定経路を受入済みにしない。
実dispatch・opt-in変更・公開はさらに別段階。DO移行と#281完了はこの限定実測の前提にしない。

20:39 JSTの追加read-only確認ではcanonicalの`freshness.json`は404、`release.json`は200 / 29,909 bytes、
公開アプリcommitとGitHub `STATIC_APPROVED_SHA` はともに
`a4de2a226dfa205b8be42fb62a710a1b06271ed8`。`STATIC_EXTERNAL_TRIGGER_ENABLED`は未設定、
既存`STATIC_AUTOMATION_ENABLED`はtrue。既存scheduleの設定は変更していない。
現在のhint欠落を古いhintと見なして起動することはない。

## 提出前検証

- Worker/CLI/native workerd: 45件成功。DOなしのscheduled handler、native KV、outbound mockで実行。
  POST前write失敗、受付不明、401/403、回数・間隔、日跨ぎ、遅いKV、既存pendingを検証。
- Worker TypeScript、Wrangler dry-run成功（9.14 KiB / gzip 3.37 KiB）。本番には送信していない。
- 関連Python 123件、公開後検証42件、release検査、Ruff、Web型検査、git diff --check成功。
- 実装GPT-6.1 Solに対しGPT-6 Astra highが別read-only contextで今回差分と関連Actionsを確認し、
  修正必須の未解決指摘なし。管理APIの再取得は実装担当が実施し、reviewerは資料の整合性を確認。
- setup後、既存隔離Compose `autonavlog-wt-6021b5fb` / 41503はhealthy、healthz ok。
  Workerは別runtimeであり、今回の範囲にアプリ再配置は含めない。volumeは保持。
- 最新HEADのhosted CI・CodeRabbitはPR Conversationで記録し、このローカル結果とは分ける。
