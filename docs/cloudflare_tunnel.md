# Cloudflare TunnelでAutoNavLog Webを公開する

AutoNavLogはローカルoriginを `127.0.0.1:8123` で起動し、既存の
Cloudflare Tunnel connectorから同じhostの `http://localhost:8123` へ転送します。
originをInternetへ直接listenさせません。

## 1. ローカルorigin

```bash
docker compose up -d --build
docker compose ps
```

Composeはcontainer内の `0.0.0.0:8000` をhostの `127.0.0.1:8123` だけへpublishし、
Projectと参照データをnamed volume `autonavlog-data` に保存します。別terminalで確認します。

```bash
curl --fail --silent http://127.0.0.1:8123/healthz
```

標準imageの `fake` はUI確認専用で、転記補助HTMLを常にblockします。実気象運用は
private `jma-msm-wind==0.2.1` wheelを組み込んだimage、`--weather msm` または
`--weather msm-metar`、検証済みcacheを使用してください。

## 2. 接続済みのremotely-managed tunnel

Cloudflare dashboardの `Networking > Tunnels` で既存tunnelを開き、
`Routes > Add route > Published application` を選びます。

| 項目 | 設定 |
|---|---|
| Hostname | 管理中zoneの専用subdomain |
| Path | 空欄 |
| Service type | HTTP |
| Service URL | `localhost:8123` |

既存connectorがHealthyであれば、アプリ側にTunnel名・UUID・tokenを保存する必要はありません。
remotely-managed tunnelはorigin側でtokenだけを使って接続し、routeはdashboardで管理します。

公式手順: [Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)

## 3. Cloudflare Access

公開hostnameと同じdomainをCloudflare Accessのself-hosted applicationへ登録し、
利用を許可するidentity／email groupだけのAllow policyを作成します。
Accessは各requestを認証してからoriginへ転送します。

AutoNavLogの `X-AutoNavLog-Session` は作業sessionの識別だけを行います。
Accessを省略して認証済みとみなしてはいけません。

公式手順: [Add web applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/)、
[Authorization cookie](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/)

## 4. 開発用Quick Tunnel

一時確認だけなら次を使用できます。

```bash
cloudflared tunnel --url http://localhost:8123
```

Quick Tunnelはrandomな `trycloudflare.com` URLを発行する開発機能です。
正式公開にはremotely-managed tunnelとAccessを使用します。`~/.cloudflared/config.yaml` が
存在する環境ではQuick Tunnelが動かない場合があります。

公式手順: [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

## 5. 公開前チェック

- Composeのpublished portがhostの `127.0.0.1` にだけbindしている。
- `docker compose ps` でserviceがhealthyである。
- Cloudflare Access未認証のbrowserがアプリへ到達できない。
- `/`, `/api/session`, `/healthz` が同じhostnameで応答する。
- KML貼付、経路確定、計算、保存・読込が動く。
- `fake`、未検証場周高度、未承認WarningなどのBlockerが転記出力を止める。
- Tunnel token、Access token、Project JSONをGitへ追加していない。

Cloudflare Tunnelはoriginから外向き接続を作るため、originへの受信portを開ける必要はありません。
参考: [Cloudflare Tunnel overview](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
