# AutoNavLog

AutoNavLog は、航空大学校の宮崎課程 NAV2 で使う航法 LOG の地上準備を支援する、
SR22 G6 向けの Web アプリです。KML または KMZ の経路を読み込み、NAV LOG の計算結果を
Web 画面に表示します。

表示内容は非公式の地上準備資料です。運航資料、完成帳票、別添 8-1 の原本としては使えません。
利用者が根拠と警告を確認してください。航空大学校の公式様式や計算規則への準拠は
主張していません。

- 現在のバージョン: `1.9.5`
- [変更履歴](CHANGELOG.md)
- [計算規則](docs/calculation_rules.md)
- [一次資料の確認状況](docs/primary_source_audit.md)

## できること

- KML/KMZ から経路を取り込む
- 飛行計画、性能、MSM の風と気温から NAV LOG を計算する
- 計算結果、警告、確認事項を Web 画面で見る
- Projectの入力を自動保存し、最後に正常完了したNAV LOGと気象情報を復元する
- 参照データと気象キャッシュを保存する

SEA、DEM、陸域マスクは現在の計算対象に含めていません。旧 Project に残る SEA 関連の項目は
schema v3 への読込時に破棄します。

## Projectの自動保存と復元

serverが受理したProject入力は、画面の「保存」を押さなくても最新draftとして自動保存します。
「保存」は現在のdraftとは別に、revision付きの明示checkpointを更新します。自動保存だけの
Projectはownerごとに1件だけ`Latest`として「保存済み」の一覧に表示されます。新しいProjectの
autosaveが成功すると、以前のautosave-only Projectは自動削除されます。明示保存したProjectは
指定した名前とrevisionで一覧に残り、通常のautosaveではrevisionを増やしません。

計算が最後まで正常に完了し、Blockerがない場合は、warningを含むNAV LOG、計算時点のProject、
目的地風、Forecast Run・来歴をProjectごとに1件だけ保存します。その後に入力を変更しても
直前のNAV LOGは表示したまま「再計算が必要」とし、RJFMの数値案内は現在の入力・参照資料と
一致する場合だけ表示します。失敗・中断・Blockerで終わった計算は直前の正常結果を置き換えません。

新しいsessionやCookie消失後は、Cloudflare Access identityごとに最後に明示作成・選択した
Projectをserver storageから復元します。更新日時が新しいだけの別Projectは自動選択しません。
markerが欠落・破損している場合や所有者が一致しない場合はProjectを自動選択せず、所有中の一覧から
明示的に開きます。Projectと復元結果は同じownerだけが読み込めます。

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

経路表の `PHASE` は経路確定時に自動で初期設定し、通常は非操作状態で表示します。
変更が必要な場合だけ `PHASE` 列の「変更」を押して編集します。`CLIMB` または `DESCENT` を
別 Leg に選ぶと、既存の同 Phase は自動で `CRUISE` に戻るため、上昇・降下の基準 Leg は
それぞれ1つだけです。RJFM北行き例外の固定区間と `VISUAL_ARRIVAL` は変更できません。

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

AutoNavLog のサポート対象runtimeは Docker / Docker Compose のみです。アプリケーションの
containerは Python 3.12 を使用します。hostへのPython packageのinstallやhost上での直接実行は、
動作する場合があってもサポート対象ではありません。

## 設定ファイル

- `compose.yaml`: 標準の Web + MSM 構成
- `compose.wsl.yaml`: WSL mirrored networking 用 override
- `.env`: 接続先ごとの環境変数。秘密情報を Git へ追加しないでください

操作対象を明確にするため、以降の例では checkout を変数にします。

```bash
REPO_DIR=/path/to/AutoNavLog
cd "$REPO_DIR"
```

## 起動方法

`REPO_DIR` へ移動して実行します。

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

標準コンテナは `msm` モードで動きます。上空の風と気温、出発地・目的地の地表面気温には
MSM予報値を使います。QNHは入力・取得・計算・帳票の対象にしません。

NAV LOGの出発地・目的地TOATには、その地点と表示時刻のMSM地上気温を使います。上空の
気圧面気温を空港標高へ外挿しません。地上気温を取得できない場合は値を補完せず「未取得」と
表示します。

計算後は、到着予定時刻に対応する目的地 TAF の卓越風を AviationWeather.gov から取得し、
`DESTINATION INFO` 行に参考値として表示します。NAV LOG の WCA、GS、ETE、燃料には使いません。
TAF を取得できなくても計算は続きます。

RJFMから大分方面へ北上する経路で、最初のWaypointがUMKまたはOMARUの参照座標から
1.0 NM以内なら、UMK 5,500 ftのRCA例外を自動適用します。経路表と内部Route Graphは
UMKとOMARUを保持しますが、NAV LOGでは `RJFM → OMARU` を1つの親Legとし、その中に
`UMK/RCA 5,500 ft` の境界を表示します。親LegのDIST・ETE・燃料は子区間の合計、
TC・VAR・MCはRJFMからOMARUへの直行測地線値です。

RJFMからUMKは `UMK 5,500 ft HIT / CLIMB`、UMKからOMARUは `5,500 ft / CRUISE`に
固定し、この区間にALT・Phaseの入力欄は表示しません。OMARUから先はALTを通常どおり編集でき、
PHASEは変更モードで基準Legだけを変更できます。RWY09の延長旋回は左、RWY27の初期旋回と
延長旋回は右です。このRWY別方向は添付資料の転記ではなく、2026-08-17の利用者決定として
参照データに記録しています。

RJFM案内カードは、現在の計算結果に限りNAV LOG主表とFUEL表の下に表示します。
North Up上の滑走路出発方位に合わせてRWY27を左、RWY09を右へ並べ、各カードには
旋回開始高度、旋回開始点のMZE DME、NAV LOG直線Legとの差を表示します。DMEは案内値であり、
閾値による警告は行いません。直線Legとの差は、例外候補のUMK到達時間から、
NAV LOGが採用するRJFM→UMK/RCA直線距離を同じCLIMB GSで飛行した時間を引いた値です。
MAP内の経路線と凡例は残ります。画面幅1,240 px以下では「入力 → 経路・MAP → 準備状況 →
NAV LOG → RJFM案内」の1列順となり、上下に往復せず確認できます。適用条件と制限は
[計算規則](docs/calculation_rules.md#rjfm大分方面のumkrca例外)を参照してください。

RJFM帰路でOMARU→UMK→最終VREPの座標条件を満たす場合、OMARU→UMKは4,500 ft固定です。UMK以降の運用降下ETE・燃料は選択降下率とlevel off後60秒から求め、物理DIST/TC/MC/CUM DISTは変えません。NAV LOG下の西方延長案内は別のwarning-only診断であり、現在の計算結果だけに表示します。同梱したAIP Japan ENR 5.3-21のKS4-3水平境界と、ENR 4.1-15のMZE DMEを、固定ハッシュ・ページ・正規化手順でfail-closed検証して数値案内に使います。根拠と制限は[RJFM inbound guidance](docs/rjfm_inbound_guidance.md)を参照してください。

RJFMのMAPには宮崎特別管制区（PCA）の水平境界と9 km中心除外円を同梱参照値から描画します。
民間訓練試験空域KS4は、国土交通省が案内する国土地理院GeoJSONを表示時に取得します。
KS4 Polygonは塗りつぶし、明示されたKS4 LineStringは実境界線として検証して表示します。
ライブGeoJSON本文は参照パックのSHA-256対象外で、NAV LOG計算、出発経路ソルバ、PCA判定には
使いません。取得失敗・形式不一致時は両レイヤーを非表示にします。境界付近ではMAPだけで判断せず、
空域を管轄する機関へ確認してください。

同梱している RJFM/RJFO の場周経路高度は、一次資料による出典確認が終わっていないため
`UNVERIFIED` です。経路の取込、入力確認、下書き保存はできますが、
`PATTERN_ALTITUDE_REQUIRED` が NAV LOG の計算完了を止めます。

性能データの収録範囲と検証結果は[データ来歴](docs/data_provenance.md)、計算時の丸めや補間は
[計算規則](docs/calculation_rules.md)に記録しています。

## 更新作業手順

Project と気象キャッシュは named volume にあるため、次の手順では削除されません。

```bash
git pull --ff-only
docker compose build --pull --no-cache autonavlog
docker compose up -d --force-recreate autonavlog
docker compose ps
curl --fail --silent http://127.0.0.1:8123/healthz
```

`git pull`後のimage buildやcontainer再作成、`docker compose down`ではnamed volumeを削除しないため、
`Latest`を含むProjectの最後の編集とowner状態は保持されます。`docker compose down -v`を実行すると
named volumeと保存内容が削除されるため、通常の更新・停止では使用しないでください。

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

backend CIはDockerのPython 3.12 test stageで実行します。次のhost側コマンドは開発時の検査用であり、
host上でのアプリケーション実行をサポート対象にするものではありません。
`jma-msm-wind` は `0.2.1` に固定しています。

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[test]"
pytest
ruff check .
mypy src/autonavlog
python scripts/validate_performance_data.py data/performance
python scripts/validate_runtime_data.py data
python scripts/export_schemas.py --check
npm --prefix web run build
npm --prefix web run test:e2e
```

CIでは同じ backend 検査をDockerのtest stageで実行し、別のruntime jobで本番imageを既定の
MSM起動設定のまま起動して`/healthz`を確認します。ローカルでも次のコマンドで再現できます。

```bash
docker build --target test --tag autonavlog:test .
docker run --rm autonavlog:test
docker build --target runtime --tag autonavlog:runtime .
```

リリース時は `1.9.5` が次の場所で一致していることを確認します。

- `pyproject.toml`
- `web/package.json` と `web/package-lock.json`
- `README.md` と `CHANGELOG.md`

`src/autonavlog/version.py` は固定値を持たず、インストール済み Package Metadata から版番号を取得します。
`jma-msm-wind==0.2.1` は別製品の版なので変更しません。

## リポジトリ

| パス | 内容 |
| --- | --- |
| `src/autonavlog/domain` | Project、計算結果、気象のデータ契約 |
| `src/autonavlog/nav` | 測地線、PA、TAS/CAS、風、燃料、丸め |
| `src/autonavlog/performance` | 性能 CSV の検査、上昇補間、巡航セル選択 |
| `src/autonavlog/weather` | MSM、TAF の取得と変換 |
| `src/autonavlog/storage` | Project と参照データのローカル保存 |
| `src/autonavlog/web` | FastAPI と Web API |
| `web` | React、TypeScript、Vite、Playwright |

設計の全体像は[アーキテクチャ](docs/architecture.md)を参照してください。実 MSM の手動受入検査は
[実 MSM 受入検査](docs/real_msm_acceptance.md)にあります。

## ライセンスと問い合わせ

`pyproject.toml` では `LicenseRef-Proprietary` を指定しています。利用と再配布の条件は
リポジトリ所有者へ確認してください。不具合や変更要望は
[GitHub Issues](https://github.com/Yuto-24/AutoNavLog/issues)へ登録してください。
