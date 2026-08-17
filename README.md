# AutoNavLog

AutoNavLog は、航空大学校の宮崎課程 NAV2 で使う航法 LOG の地上準備を支援する、
SR22 G6 向けの Web アプリです。KML または KMZ の経路を読み込み、NAV LOG の計算結果と
A4 横の転記補助表を作ります。

出力は非公式の補助資料です。運航資料、完成帳票、別添 8-1 の原本としては使えません。
利用者が根拠と警告を確認し、公式様式へ手書きで転記してください。航空大学校の公式様式や
計算規則への準拠は主張していません。

- 現在のバージョン: `1.0.0`
- [変更履歴](CHANGELOG.md)
- [計算規則](docs/calculation_rules.md)
- [一次資料の確認状況](docs/primary_source_audit.md)

## できること

- KML/KMZ から経路を取り込む
- 飛行計画、性能、MSM の風と気温から NAV LOG を計算する
- 計算結果、警告、確認事項を Web 画面で見る
- Project と気象キャッシュを保存する
- 公式様式へ書き写すための A4 横 HTML を出力する

SEA、DEM、陸域マスクは現在の計算対象に含めていません。既存 Project を読み込むために
SEA 関連の項目は残していますが、計算、画面表示、出力可否の判定には使いません。

## 経路を取り込む

Web 画面では、KML/KMZ のファイル選択とドラッグ＆ドロップ、KML XML の貼り付けから
経路を取り込めます。どの方法でも同じ規則で候補を作り、KMZ では選ばれた KML にも
同じ規則を適用します。

1本の経路が複数の `LineString` に分かれている場合は、最も内側の同じ `Folder` または
`Document` にある線を KML の記載順・記載方向のまま連結します。隣り合う線の終点と始点が
すべて `0.02 NM` 以内であることが条件です。線の順序変更や向きの自動反転は行いません。

複数の連結経路があるときは、「飛行経路候補」から使う経路を選びます。候補名のほか、
Leg 数と全長を確認してから選択してください。選択前に先頭候補が採用されることはありません。
選択後は、連結された経路全体を地図で確認してから確定します。

線の接続点では、同じコンテナにあり、両側の線端点から `0.02 NM` 以内にある Point Placemark
だけを変針点の名称・座標として使います。線の途中にある C'K など、接続点ではない Point は
自動では経路点にしません。該当する Point がなければ前の線の終点を使い、線端点の差が
10 m を超える場合は警告します。差が `0.02 NM` を超える組み合わせは連結せず、個別の線と
警告を表示します。LineString候補が1件でもある間は全Pointを並べた候補を表示せず、
LineStringがないPoint-only KMLでは従来のPoint候補を利用できます。

選択した経路は合計500点までです。連結経路を採用した場合は、採用したコンテナと構成
`LineString` の名称を Project の metadata に保存します。

## 必要な環境

- Docker Engine
- Docker Compose v2
- JavaScript と Cookie を有効にした新しいブラウザ
- MSM の取得先と AviationWeather.gov へ接続できるネットワーク
- 外部公開時は Cloudflare Tunnel と Cloudflare Access

## 起動

リポジトリのルートで実行します。

```bash
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user docker compose up -d --build
docker compose ps
```

ブラウザで `http://127.0.0.1:8123` を開きます。ログの確認と停止には次のコマンドを使います。

```bash
docker compose logs -f autonavlog
docker compose down
```

### Windows から WSL 上の Docker へ接続する

WSL の mirrored networking では、Docker の公開ポートが Windows の `localhost` へ転送されない
場合があります。その場合は WSL 用 override で host network を使います。

```bash
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user \
AUTONAVLOG_SESSION_COOKIE_SECURE=false \
docker compose -f compose.yaml -f compose.wsl.yaml up -d --build

docker compose -f compose.yaml -f compose.wsl.yaml ps
```

Windows のブラウザで `http://localhost:8123` を開きます。Windows Firewall と WSL の
Hyper-V Firewall では TCP 8123 の受信を許可してください。停止時も同じ構成ファイルを指定します。

```bash
docker compose -f compose.yaml -f compose.wsl.yaml down
```

この構成はコンテナを WSL host network の `0.0.0.0:8123` で待ち受けさせます。通常構成と
同時に起動しないでください。

Project、参照データの使用中の版、気象キャッシュは Docker の named volume
`autonavlog-data` に保存されます。`docker compose down -v` は保存データも削除するため、
更新や通常の停止には使わないでください。

`AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` は接続元を識別しません。ポートへ到達した端末をすべて
`local-user` として扱います。この設定を使ったコンテナをインターネットへ公開しないでください。

## 別端末からの開発確認

同じ LAN にある別端末から開く場合は、既存環境とポート、bind address、Compose project 名を
分けます。信頼できる LAN の中だけで使い、ホスト側の firewall でも接続元を制限してください。

```bash
HOST_IP="$(hostname -I | awk '{print $1}')"

AUTONAVLOG_BIND_ADDRESS="$HOST_IP" \
AUTONAVLOG_HOST_PORT=8124 \
AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user \
AUTONAVLOG_SESSION_COOKIE_SECURE=false \
docker compose -p autonavlog-dev up -d --build
```

別端末で `http://<HOST_IP>:8124` を開きます。停止時にも同じ project 名を指定します。

```bash
docker compose -p autonavlog-dev down
```

通常起動では `0.0.0.0:8123` に bind します。信頼できる開発ネットワーク内だけで使用し、
外部公開にはこの LAN 向け設定を流用せず、
[Cloudflare 公開手順](docs/cloudflare_tunnel.md)に従ってください。

## 気象と参照データ

標準コンテナは `msm-metar-trend` モードで動きます。上空の風と気温には MSM を使い、QNH は
最新 METAR と MSM MSLP の時間差から推定します。検証済み METAR を使えない場合は MSM MSLP
だけに切り替え、MSM も取得できなければ QNH の手入力を求めます。手入力値は自動値より優先します。

NAV LOGの出発地・目的地TOATには、その地点と表示時刻のMSM地上気温を使います。上空の
気圧面気温を空港標高へ外挿しません。地上気温を取得できない場合は値を補完せず「未取得」と
表示します。

推定 QNH は公式の飛行場 QNH ではありません。必ず公式の飛行場気象と照合してください。

計算後は、到着予定時刻に対応する目的地 TAF の卓越風を AviationWeather.gov から取得し、
`DESTINATION INFO` 行に参考値として表示します。NAV LOG の WCA、GS、ETE、燃料には使いません。
TAF を取得できなくても計算は続きます。

RJFMから大分方面へ北上する経路で、最初のWaypointがUMKまたはOMARUの参照座標から
1.0 NM以内なら、UMK 5,500 ftのRCA例外を自動適用します。経路表と内部Route Graphは
UMKとOMARUを保持しますが、NAV LOGでは `RJFM → OMARU` を1つの親Legとし、その中に
`UMK/RCA 5,500 ft` の境界を表示します。親LegのDIST・ETE・燃料は子区間の合計、
TC・VAR・MCはRJFMからOMARUへの直行測地線値です。

RJFMからUMKは `UMK 5,500 ft HIT / CLIMB`、UMKからOMARUは `5,500 ft / CRUISE`に
固定し、この区間にALT・Phaseの入力欄は表示しません。OMARUから先は通常どおり編集できます。
RWY09の延長旋回は左、RWY27の初期旋回と延長旋回は右です。このRWY別方向は添付資料の
転記ではなく、2026-08-17の利用者決定として参照データに記録しています。

RJFM案内カードは、現在の計算結果に限りNAV LOG主表とFUEL表の下に表示します。
North Up上の滑走路出発方位に合わせてRWY27を左、RWY09を右へ並べ、各カードには
旋回開始高度とNAV LOG直線Legとの差だけを表示します。この差は、例外候補のUMK到達時間から、
NAV LOGが採用するRJFM→UMK/RCA直線距離を同じCLIMB GSで飛行した時間を引いた値です。
MAP内の経路線と凡例は残ります。画面幅1,240 px以下では「入力 → 経路・MAP → 準備状況 →
NAV LOG → RJFM案内」の1列順となり、上下に往復せず確認できます。適用条件と制限は
[計算規則](docs/calculation_rules.md#rjfm大分方面のumkrca例外)を参照してください。

RJFMのMAPには宮崎特別管制区（PCA）の水平境界と9 km中心除外円を同梱参照値から描画します。
民間訓練試験空域KS4は、国土交通省が案内する国土地理院GeoJSONを表示時に取得します。
ライブGeoJSON本文は参照パックのSHA-256対象外で、NAV LOG計算、出発経路ソルバ、PCA判定には
使いません。境界付近ではMAPだけで判断せず、空域を管轄する機関へ確認してください。

同梱している RJFM/RJFO の場周経路高度は、一次資料による出典確認が終わっていないため
`UNVERIFIED` です。経路の取込、入力確認、下書き保存はできますが、
`PATTERN_ALTITUDE_REQUIRED` が転記補助表の出力を止めます。

性能データの収録範囲と検証結果は[データ来歴](docs/data_provenance.md)、計算時の丸めや補間は
[計算規則](docs/calculation_rules.md)に記録しています。

## 更新

Project と気象キャッシュは named volume にあるため、次の手順では削除されません。

```bash
git pull --ff-only
docker compose build --pull --no-cache autonavlog
docker compose up -d --force-recreate autonavlog
docker compose ps
curl --fail --silent http://127.0.0.1:8123/healthz
```

更新後は次を確認します。

1. `/healthz` の `version` が更新後の版になっている
2. 画面左上の `vX.Y.Z` が同じ版になっている
3. KML を読み込み、NAV LOG を1回計算できる
4. ブラウザの開発者ツールにエラーが出ていない

HTML には `Cache-Control: no-cache`、API には `Cache-Control: no-store` を付けています。
版番号が古いままなら、接続先のポートと Compose project 名を確認してからハード再読み込みします。

### 開発環境の更新

別ポートで動かしている開発環境には、起動時と同じ値を渡します。

```bash
git pull --ff-only
docker compose -p autonavlog-dev build --pull --no-cache autonavlog
env \
  AUTONAVLOG_BIND_ADDRESS=0.0.0.0 \
  AUTONAVLOG_HOST_PORT=8124 \
  AUTONAVLOG_TRUSTED_LOCAL_IDENTITY=local-user \
  AUTONAVLOG_SESSION_COOKIE_SECURE=false \
  docker compose -p autonavlog-dev up -d --force-recreate autonavlog
curl --fail --silent "http://127.0.0.1:8124/healthz"
```

## Cloudflare 公開

公開 hostname の Service URL は `http://localhost:8123` にします。アプリは loopback bind のまま
動かし、Cloudflare Access の self-hosted application と Allow policy を設定してください。
`AUTONAVLOG_TRUSTED_LOCAL_IDENTITY` は設定しません。

アプリは `Cf-Access-Authenticated-User-Email` を Project 所有者の識別に使います。
session token は `HttpOnly; Secure; SameSite=Strict` Cookie で送り、JavaScript や
Web Storage には保存しません。

Quick Tunnel は一時的な確認に限ります。正式公開には使わないでください。

```bash
cloudflared tunnel --url http://localhost:8123
```

詳しい設定は[Cloudflare 公開手順](docs/cloudflare_tunnel.md)にあります。

## 開発

Python 3.12 を主対象とし、3.10 から 3.12 までをサポートします。`jma-msm-wind` は
`0.2.1` に固定しています。Web サービスの通常起動に Python の開発環境は不要です。

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
npm --prefix web run build
npm --prefix web run test:e2e
```

リリース時は `1.0.0` が次の場所で一致していることを確認します。

- `pyproject.toml` と `src/autonavlog/version.py`
- `web/package.json` と `web/package-lock.json`
- `scripts/colab_e2e_assert.py` と `notebooks/AutoNavLog.ipynb`
- 配布 workflow と関連テスト

古い版の検索には `rg -n '0\.3\.1'` を使います。`jma-msm-wind==0.2.1` は別製品の版なので
変更しません。旧 Colab Preview は検証済み ZIP と SHA-256 の組を保つため `0.3.1` に固定しており、
検索結果から除外します。

## 旧 Colab 配布

Colab 版は互換確認と移行確認のために残しています。新規運用には Web 版を使ってください。
Preview 配布は検証済み bundle と一致する `0.3.1` のまま凍結しています。

プレビュー ZIP は次のコマンドで作ります。

```bash
python scripts/build_colab_preview_bundle.py \
  dist/autonavlog-0.3.1-py3-none-any.whl \
  /path/to/jma_msm_wind-0.2.1-py3-none-any.whl \
  dist/autonavlog-colab-preview-0.3.1.zip \
  --terrain /path/to/verified/terrain.npz
```

ZIP を Colab VM の `/content` または Google Drive の `MyDrive` 直下へ置き、
`notebooks/AutoNavLog_Colab_Preview.ipynb` を実行します。Notebook は ZIP 内の各ファイルについて
size と SHA-256 を検査します。

`release-to-drive` workflow を使う場合は、次の Actions secrets が必要です。

| Secret | 内容 |
| --- | --- |
| `MSM_REPO_TOKEN` | `Yuto-24/jma-msm-wind-kyushu` の read 権限 |
| `GDRIVE_SERVICE_ACCOUNT_JSON` | Drive へアップロードする Service Account JSON |
| `GDRIVE_RELEASE_FOLDER_ID` | 共有 Drive のリリース親フォルダー |
| `GDRIVE_TERRAIN_FILE_ID` | 検証済み Pzs `terrain.npz` |

1つの Project を複数の Notebook から同時に編集しないでください。revision が競合すると、
AutoNavLog は既存ファイルを残し、`project-conflict-*.json` を保存します。

## リポジトリ

| パス | 内容 |
| --- | --- |
| `src/autonavlog/domain` | Project、計算結果、Snapshot、気象のデータ契約 |
| `src/autonavlog/nav` | 測地線、PA、TAS/CAS、風、燃料、丸め |
| `src/autonavlog/performance` | 性能 CSV の検査、上昇補間、巡航セル選択 |
| `src/autonavlog/weather` | MSM、METAR、TAF の取得と変換 |
| `src/autonavlog/storage` | ローカル保存と Google Drive 保存 |
| `src/autonavlog/web` | FastAPI と Web API |
| `web` | React、TypeScript、Vite、Playwright |
| `src/autonavlog/presentation` | 旧 Colab UI と A4 横の転記補助表 |
| `notebooks` | 旧 Colab 互換用 Notebook |

設計の全体像は[アーキテクチャ](docs/architecture.md)を参照してください。実 MSM のリリース検査は
[実 MSM リリース受入ゲート](docs/real_msm_acceptance.md)にあります。

## ライセンスと問い合わせ

`pyproject.toml` では `LicenseRef-Proprietary` を指定しています。利用と再配布の条件は
リポジトリ所有者へ確認してください。不具合や変更要望は
[GitHub Issues](https://github.com/Yuto-24/AutoNavLog/issues)へ登録してください。
