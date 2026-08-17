# Cloudflare TunnelでAutoNavLog Webを公開する

AutoNavLogはローカルoriginを `127.0.0.1:8123` で起動し、既存の
Cloudflare Tunnel connectorから同じhostの `http://localhost:8123` へ転送します。
originをInternetへ直接listenさせません。

## 1. ローカルorigin

公開serviceではAccess applicationのTeam domainとAudience tagを設定して起動します。
どちらもCloudflare dashboardで確認できる識別子で、tokenや秘密鍵ではありません。

```bash
export AUTONAVLOG_CLOUDFLARE_TEAM_DOMAIN='https://<team>.cloudflareaccess.com'
export AUTONAVLOG_CLOUDFLARE_ACCESS_AUDIENCE='<Access application AUD tag>'
unset AUTONAVLOG_TRUSTED_LOCAL_IDENTITY
AUTONAVLOG_BIND_ADDRESS=127.0.0.1 docker compose up -d --build
docker compose ps
```

Tunnelを使わずloopbackでUI操作まで試す場合だけ、固定identityを明示します。

```bash
AUTONAVLOG_BIND_ADDRESS=127.0.0.1 \
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user \
docker compose up -d --build
```

このoverrideはCloudflare Access headerを使わないローカル試験専用です。設定したまま
Tunnelへ公開すると全利用者が同じ所有者になるため、公開serviceでは必ず未設定にします。

Cloudflare用コマンドは、通常の開発用default（`0.0.0.0`）を明示的に上書きし、container内の
`0.0.0.0:8000` をhostの `127.0.0.1:8123` だけへpublishします。
Projectと参照データをnamed volume `autonavlog-data` に保存します。別terminalで確認します。

```bash
curl --fail --silent http://127.0.0.1:8123/healthz
```

`--weather fake` はUI確認専用で、転記補助HTMLを常に止めます。標準imageは
private `jma-msm-wind==0.2.1` wheelを組み込み、`--weather msm-metar-trend` で起動します。
QNHはMETAR補正付きMSM推定値を優先し、目的地の参考風はAviationWeather.govのTAFから
取得します。

## 2. 接続済みのremotely-managed tunnel

Cloudflare dashboardの `Networking > Tunnels` で既存tunnelを開き、
`Routes > Add route > Published application` を選びます。

| 項目 | 設定 |
| --- | --- |
| Hostname | 管理中zoneの専用subdomain |
| Path | 空欄 |
| Service type | HTTP |
| Service URL | `localhost:8123` |

既存connectorがHealthyであれば、アプリ側にTunnel名・UUID・tokenを保存する必要はありません。
remotely-managed tunnelはorigin側でtokenだけを使って接続し、routeはdashboardで管理します。

公式手順: [Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)

## 3. Cloudflare Access

公開hostnameと同じdomainをCloudflare Accessのself-hosted applicationへ登録し、
利用を許可するidentity／email groupだけのAllow policyを作成します。Accessがoriginへ付与する
`Cf-Access-Jwt-Assertion` をAutoNavLog自身でも検証します。検証対象はRS256署名、Team domainの
`iss`、設定済みapplication `aud`、`exp` です。署名鍵はTeam domainのJWKSから`kid`で選び、
検証済み`email` claimだけをsession所有者と保存Projectの `web_owner_id` に使用します。
`Cf-Access-Authenticated-User-Email` 単独のrequestは拒否します。

作業session tokenはレスポンス本文へ返さず、
`HttpOnly; Secure; SameSite=Strict; Path=/` Cookieだけで送ります。JavaScript、
`localStorage`、`sessionStorage`、独自headerには保存しません。logoutはserver側sessionを
無効化してCookieを削除します。他のidentityには一覧にも404応答にもProjectの存在を漏らしません。

JWT検証に加えて、originは必ず`127.0.0.1:8123`だけへbindします。
Cloudflare公開構成では `AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` はloopback試験だけに使用し、
公開serviceでは必ず未設定にします。信頼済みLAN内のHTTP開発環境はREADMEの分離起動手順を
使用してください。trusted local identityを使用したrequestは毎回警告を記録します。

公式手順: [Add web applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/)、
[Validate JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

## 4. 開発用Quick Tunnel

一時確認だけなら次を使用できます。

```bash
cloudflared tunnel --url http://localhost:8123
```

Quick Tunnelはrandomな `trycloudflare.com` URLを発行する開発機能ですが、通常は
Access identity headerを付与しないため認証済みsessionを作成できません。固定local identityを
設定してQuick Tunnelへ公開する運用は禁止します。正式公開にはremotely-managed tunnelと
Accessを使用します。`~/.cloudflared/config.yaml` が存在する環境ではQuick Tunnelが
動かない場合があります。

公式手順: [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

## 5. 公開前チェック

- Composeのpublished portがhostの `127.0.0.1` にだけbindしている。
- `docker compose ps` でserviceがhealthyである。
- Cloudflare Access未認証のbrowserがアプリへ到達できない。
- Team domainとAccess application AUDがcontainer環境へ設定されている。
- `AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` が公開serviceで空になっている。
- `/`, `/api/session`, `/healthz` が同じhostnameで応答する。
- `/healthz` と画面左上の版番号がリリース版に一致する。
- HTML応答が `Cache-Control: no-cache`、API応答が `no-store` を返す。
- session Cookieに `HttpOnly`、`Secure`、`SameSite=Strict` が付く。
- 2つのAccess identity間でsessionと保存Projectが相互に見えない。
- KML貼付、経路確定、計算、保存・読込が動く。
- `fake`、未検証場周高度、未確認事項などのBlockerが転記出力を止める。
- Tunnel token、Access token、Project JSONをGitへ追加していない。

Cloudflare Tunnelはoriginから外向き接続を作るため、originへの受信portを開ける必要はありません。
参考: [Cloudflare Tunnel overview](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
