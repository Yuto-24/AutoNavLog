# AutoNavLog

AutoNavLogは、航空大学校 宮崎課程 NAV2の航法LOG作成を支援する、SR22 G6向けの
**地上準備専用**ツールです。計算結果とA4横の転記補助表は、運航資料、完成帳票、
別添8-1原本ではありません。利用者が根拠と確認事項を照合し、公式様式へ手書きで転記する
ための非公式補助です。航空大学校の公式様式・計算規則への準拠は主張しません。

## 現在の検証状態

アプリケーション、計算Policy、MSM連携、保存、KML/KMZ取込、Web UI、旧Colab UI、テスト基盤を
実装しています。`data/performance`にはSR22 G6 POH P/N 13772-006 Reissue Aの上昇
原表上昇19節点から500 ft刻みに拡張した36行と、巡航原表159行を収録し、原典PDFからの
再抽出・全行差分比較を完了しています。上昇表のISA温度および標準より10℃高いごとの
10%増加を適用し、巡航は65% PWRをPWR、ISA偏差、高度の順に線形補間します。補間は
Issue #15添付表の全6,419行と一致を確認し、表外へは外挿しません。

これは対象機固有の日本承認AFM/POH・Supplementとの適用確認や、校内Golden NAV2 LOGとの
一致を意味しません。これらと気象QNH、丸め等の未確認事項が残るため、結果を
航空大学校の承認済みNAV LOGまたは運航資料とは表示しません。v2.6ではSEA・DEM・
陸域マスクを対象外とし、取得・算出・入力・表示・転記可否の判定には使用しません。
既存schemaのSEA fieldは旧Projectの読込互換だけに残しています。


## Web版（主配布）

実行環境にはDocker EngineとDocker Composeを使用します。Node.jsによるReact buildと
Python packageのinstallはmulti-stage image build内で完結し、hostの`.venv`は使用しません。

```bash
docker compose up -d --build
docker compose ps
```

Cloudflare Access経由では公開hostnameを開きます。直接loopbackでUIを試す場合だけ、
信頼境界をloopbackへ限定した固定identityを明示して起動します。この変数を設定したserviceを
Tunnelへ公開しないでください。

```bash
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user docker compose up -d --build
```

ブラウザで `http://127.0.0.1:8123` を開きます。Project、参照データのactive版、
気象cacheはnamed volume `autonavlog-data` に保存されます。hostへ公開するportはloopbackだけで、
containerは非root・read-only root filesystem・全capability削除で動作します。

```bash
docker compose logs -f autonavlog
docker compose down
```

既存環境と並行してheadless server上の開発環境を起動する場合は、host側port、bind address、
Compose project名を分けます。`hostname -I` の先頭のaddressを使用します。

```bash
# リポジトリのルートから実行
HOST_IP="$(hostname -I | awk '{print $1}')"

AUTONAVLOG_BIND_ADDRESS="$HOST_IP" \
AUTONAVLOG_HOST_PORT=8124 \
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user \
AUTONAVLOG_SESSION_COOKIE_SECURE=false \
docker compose -p autonavlog-dev up -d --build
```

別端末のブラウザから `http://<HOST_IP>:8124` を開きます。停止時にも同じproject名を指定します。

```bash
docker compose -p autonavlog-dev down
```

`-p autonavlog-dev` によりcontainer、network、named volumeが既存の `8123` 環境から分離されます。
`AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` はrequestの接続元を識別せず、portへ到達できる端末を同じ
固定identityとして扱います。`AUTONAVLOG_SESSION_COOKIE_SECURE=false` はHTTP接続でもsession
Cookieを送信できるようにする設定で、trusted local identityが設定され、Cloudflare Accessが
未設定の場合だけ使用できます。このLAN bindは信頼できるLAN内だけで使い、host firewallでも
接続元を制限してください。インターネットへ公開する場合は下記のCloudflare Accessを使用し、
`AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` は設定せず、session Cookieも既定のSecure属性のままにします。
bind先を省略した通常起動は引き続き `127.0.0.1`、host側portを省略した場合は `8123` です。

標準imageの `--weather fake` は決定論的な画面・計算確認用です。必ず
`DEVELOPMENT_WEATHER_PROVIDER` を表示し、A4転記補助HTMLを出力しません。
実気象用imageを作る場合はprivate配布の `jma-msm-wind==0.2.1` をimageへ導入し、
起動引数を `--weather msm` または `--weather msm-metar` に変更してください。

RJFM/RJFOの場周経路高度は画面上で100 ft単位に丸めて `1,000 ft` と表示し、
5 NM VREPは `1,500 ft` とします。ただし同梱参照行は一次資料の出典検証が未完了なので
`UNVERIFIED` のままです。経路取込・入力確認・下書き保存はできますが、
`PATTERN_ALTITUDE_REQUIRED` が転記出力を止めます。

### Cloudflare Tunnel

接続済みのremotely-managed tunnelでPublished applicationを追加し、Service URLを
`http://localhost:8123` にします。AutoNavLogはloopback bindのまま運用してください。
外部共有時はCloudflare Accessのself-hosted applicationとAllow policyを必ず設定します。
originは `Cf-Access-Authenticated-User-Email` を所有者identityとしてsessionと保存Projectへ
拘束します。session tokenは `HttpOnly; Secure; SameSite=Strict` Cookieで送られ、
JavaScriptやWeb Storageへ保存しません。

一時的な開発確認だけなら次も使えますが、Quick Tunnelは正式公開には使いません。

```bash
cloudflared tunnel --url http://localhost:8123
```

詳細は [Cloudflare公開手順](docs/cloudflare_tunnel.md) を参照してください。
Cloudflare公式: [Published application](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)、
[Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)、
[Access applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/)。

## 開発

Python 3.12を主対象とし、3.10〜3.12をサポートします。MSM実連携は
`jma-msm-wind` v0.2.1へ固定しています。以下はlibrary開発・test用であり、Web serviceの
通常起動には不要です。

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[test]"
pytest
ruff check .
mypy src/autonavlog
python scripts/validate_performance_data.py data/performance
python scripts/validate_notebook.py notebooks/AutoNavLog.ipynb
python scripts/export_schemas.py --check
```

MSM契約試験やColabリリースでは、Privateリポジトリから作成した
`jma_msm_wind-0.2.1` wheelをAutoNavLog wheelと同時にインストールします。

## 旧Colab配布（互換・移行確認用）

### Colab操作プレビュー

`notebooks/AutoNavLog_Colab_Preview.ipynb`は、地上準備の操作確認と非公式転記補助だけを
目的とします。参照・SR22 G6性能データ、検証済みPzs `terrain.npz`、AutoNavLog wheel、
固定版`jma-msm-wind==0.2.1` wheelを、内部manifestで全fileのsize/SHA-256を検査する
単一ZIPへまとめて使用します。

```bash
python scripts/build_colab_preview_bundle.py \
  dist/autonavlog-0.2.0-py3-none-any.whl \
  /path/to/jma_msm_wind-0.2.1-py3-none-any.whl \
  dist/autonavlog-colab-preview-0.2.0.zip \
  --terrain /path/to/verified/terrain.npz
```

ZIPをColab VMの`/content`、またはGoogle Driveの`MyDrive`直下へ配置してNotebookを
実行します。上空風・気温はMSM予報値、QNHはPzs地形cacheを用いた`MSM推定QNH`です。
MSM推定QNHは公式飛行場気象の観測QNHではないため、利用者が原票と照合します。取得・
算出できない場合は1013.25 hPa等で補完せず、適切なQNHを確認して手入力します。
Pzs地形cacheはMSM推定QNHだけに使用し、SEA・障害物評価には使用しません。

### Drive release bundle

`release-to-drive` workflowは、Pzs地形cacheの検証と実MSM acceptance gateを通過した
versioned releaseを共有Driveへ公開します。プレビューを個別に配布する場合は、上記の
`AutoNavLog_Colab_Preview.ipynb`と自己検証型preview ZIPを組み合わせます。

1. GitHub Actionsの`release-to-drive`を手動実行します。
2. CIがAutoNavLogとMSMのwheel、Notebook、性能・空港データ、Pzs地形キャッシュ、
   SHA-256付き`release-manifest.json`を共有Google Driveへ配置します。
3. 利用者は配布Notebookを自分のDriveへコピーし、「すべて実行」を押します。
4. Notebookは指定版wheelだけをインストールし、新版がある場合は通知だけを表示します。

必要なActions secrets:

| Secret | 内容 |
|---|---|
| `MSM_REPO_TOKEN` | `Yuto-24/jma-msm-wind-kyushu`のread権限 |
| `GDRIVE_SERVICE_ACCOUNT_JSON` | DriveへアップロードするService Account JSON |
| `GDRIVE_RELEASE_FOLDER_ID` | 共有Driveのリリース親フォルダ |
| `GDRIVE_TERRAIN_FILE_ID` | 検証済みPzs `terrain.npz` |

1 Projectを複数Notebookから同時編集しないでください。revisionが競合した場合、
AutoNavLogは既存ファイルを上書きせず`project-conflict-*.json`を保存します。

## ディレクトリ

- `src/autonavlog/domain`: Project、計算結果、Snapshot、気象契約
- `src/autonavlog/nav`: 測地線、PA、TAS/CAS、風、燃料、丸め
- `src/autonavlog/performance`: 性能CSV検証、上昇補間、巡航セル選択
- `src/autonavlog/weather`: FakeとMSM v0.2.1アダプター
- `src/autonavlog/storage`: Local/Google Drive保存とrevision管理
- `src/autonavlog/web`: FastAPI、Web façade、Docker build時に配置されるReact asset
- `web`: React/TypeScript/Vite UI、追跡外 `web/dist`、Playwright試験
- `src/autonavlog/presentation`: 旧Colab UIとA4横の非公式転記補助表
- `notebooks/AutoNavLog.ipynb`: 旧Colab互換・移行確認用Notebook

詳細は[アーキテクチャ](docs/architecture.md)、
[計算規則](docs/calculation_rules.md)、
[データ来歴](docs/data_provenance.md)、
[一次資料監査](docs/primary_source_audit.md)を参照してください。
