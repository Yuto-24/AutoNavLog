# Codex Worktree 開発環境

モデル選択と独立レビューの運用は [agent_workflow.md](agent_workflow.md) を参照してください。
この文書は、モデルに依存しないローカル実行環境の設定を扱います。

Codex の Worktree で複数の変更を並列に進める場合、通常の AutoNavLog runtime と別 Worktree の runtime を共有しません。
Worktree ごとに Compose project、Docker image、host port、named volume を分離します。

この仕組みは Codex のローカル環境向けです。通常 checkout の `autonavlog` project、`autonavlog:local` image、8123番 port、既存の `autonavlog-data` は変更しません。

## Codex のローカル環境設定

Codex の Settings > Environments で AutoNavLog の Linux 設定を次のようにします。
アプリが生成する `.codex/environments/environment.toml` は、この画面で管理します。
手編集やモデルの設定場所としての利用は避けてください。

セットアップスクリプト:

```bash
bash scripts/codex/setup_worktree.sh
```

クリーンアップスクリプト:

```bash
bash scripts/codex/cleanup_worktree.sh
```

Codex 側の環境変数は不要です。macOS / Windows 固有の設定も、現在の WSL/Linux 運用では不要です。

セットアップには稼働中の Docker daemon、`docker compose`、`python3` が必要です。
ホストに `node` と `npm` がある場合は `npm --prefix web ci` を実行します。
ない場合は Dockerfile と同じ `node:22-bookworm-slim` で実行するため、ホストへの Node.js のインストールは不要です。
生成ファイルは実行ユーザーの UID/GID で作成します。Python runtime/test dependencies は既存の Docker test stage を使用します。
Worktree の CPU 上限は Docker daemon の CPU 数と通常の上限 10 の小さい方に設定します。
通常 checkout の Compose 設定は変更しません。

ホストに Node.js がない場合の型チェックは、リポジトリのルートで次のように実行できます。

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$PWD,target=/work" --workdir /work \
  node:22-bookworm-slim npm --prefix web run typecheck
```

## 生成される設定

セットアップは Git 管理外の `.env` と `.autonavlog-worktree/compose.override.yaml` を生成します。
既存の `.env` がある場合は上書きせず停止します。

`.env` には次の値が入ります。

- `COMPOSE_PROJECT_NAME=autonavlog-wt-<id>`
- `AUTONAVLOG_IMAGE=autonavlog:wt-<id>`
- `AUTONAVLOG_HOST_PORT=<20000-59999 の空き port>`
- `AUTONAVLOG_BIND_ADDRESS=127.0.0.1`
- ローカル開発用 identity / cookie 設定
- 使用する Compose file 一式

`<id>` は Worktree の path から生成します。port はその hash を起点に 20000-59999 を探索し、セットアップ時点で使用されていない番号を保存します。生成後は同じ Worktree で同じ `.env` を使い続けます。

通常 Linux では既存 `compose.yaml` の `AUTONAVLOG_HOST_PORT` を使います。
WSL では既存 `compose.wsl.yaml` の後に生成 override を読み込み、host network 上の application port と healthcheck を Worktree 固有 port に置き換えます。

Compose project 名が異なるため、`autonavlog-data` named volume も Worktree ごとに別 resource になります。生成 override は Docker image tag も Worktree ごとに分けるため、別 Worktree の rebuild で image を差し替えません。

## 起動と停止

セットアップ後は通常の Compose command をそのまま使用します。

```bash
docker compose up -d --build
docker compose ps
```

接続先はセットアップ時に表示されます。後から確認する場合は次のようにします。

```bash
. ./.env
printf 'http://127.0.0.1:%s\n' "$AUTONAVLOG_HOST_PORT"
```

通常停止では volume を残します。

```bash
docker compose down --remove-orphans
```

## Worktree のクリーンアップ

Codex が Worktree を破棄するときだけ `cleanup_worktree.sh` を使います。
この script は `.env` が AutoNavLog の Worktree setup で生成されたことと、project/image 名が `autonavlog-wt-<8 hex>` 形式であることを確認してから、その Worktree の container と volume を削除します。Worktree 固有 image も best-effort で削除します。

通常の `autonavlog` runtime に対して `docker compose down -v` を実行してはいけません。Worktree の volume 削除は cleanup script に限定します。

Docker daemon が停止している場合、または Compose の削除が失敗した場合は、cleanup は失敗を返し、再試行に必要な `.env` と生成 override を保持します。Docker 復旧後に再実行してください。Codex が失敗した cleanup を自動再試行するとは仮定しません。

## Codex Actions の例

頻繁に使う場合は次を Codex Action に登録できます。

Start:

```bash
docker compose up -d --build
docker compose ps
. ./.env
printf 'AutoNavLog: http://127.0.0.1:%s\n' "$AUTONAVLOG_HOST_PORT"
```

Stop:

```bash
docker compose down --remove-orphans
```

Typecheck（ホストに `node` と `npm` がある場合）:

```bash
npm --prefix web run typecheck
```


## MSM キャッシュと並列運用

通常の worktree は実 MSM provider を維持しますが、48時間分の自動 prewarm を起動しません。
生成 override は `AUTONAVLOG_MSM_PREWARM=false` と `restart: "no"` を指定します。
必要な気象データは計算時にオンデマンドで取得するため、初回計算には取得時間がかかり、
ネットワークと有効な MSM 予報が必要です。計算を行えばキャッシュは増えます。
これは自動生成の抑止であり、worktree 全体の容量上限を強制する機能ではありません。

実 MSM の事前準備・受入検証が必要なときだけ明示的に有効にします。

```bash
AUTONAVLOG_MSM_PREWARM=true docker compose up -d --force-recreate
```

通常運用に戻す場合は、shell や `.env` の明示設定も解除します。

```bash
AUTONAVLOG_MSM_PREWARM=false docker compose up -d --force-recreate
```

環境変数は true/false（1/0、yes/no、on/off も可）を厳密に解釈し、誤記では起動を停止します。
有効時は従来どおり48時間・15分周期です。通常 checkout / Legacy の既定値は true のままです。
キャッシュ共有は行わず、Project と気象データの worktree 間の分離を維持します。

既存 worktree は最新スクリプトで setup を再実行してから `docker compose up -d --build`
すると設定が反映されます。setup は既存の生成 `.env`（project / port）を保持して override を再生成します。
**古いアプリ image は新しい環境変数を解釈しないため、override だけ変更しても prewarm は止まりません。**
古い branch では必要な変更を取り込んで再ビルドするか、使用しないコンテナを停止してください。
生成設定を更新しても、既存コンテナは再作成するまで変わりません。

```bash
docker compose config
docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$(docker compose ps -q autonavlog)"
docker compose logs --tail 50
```

## 使用量の診断とキャッシュだけの整理

```bash
python3 scripts/codex/worktree_usage.py > /tmp/autonavlog-worktree-usage.json
docker system df
df -h /
```

診断は `autonavlog-wt-<8 hex>_autonavlog-data` だけを列挙し、Compose labels、全 container の
状態 / restart policy / worktree path、未コミット変更、Volume 直下のディレクトリ別割当量を JSON で出力します。
`msm-cache` と `projects` / `reference` を分け、全 worktree の合計を表示します。
ローカルの `autonavlog:local` image 内の Python を読み取り専用・ネットワークなしで使用し、pull はしません。
別の既存 runtime image を使う場合は `--image <tag>` を渡してください。
測定失敗は `usage_bytes: null`、合計は `complete: false` として失敗終了し、不明量をゼロとは扱いません。
起動中の測定はその時点の参考値であり、削除可否を判定する機能ではありません。

整理は次の順で実施します。Volume 全体の cleanup とキャッシュ整理は別の操作です。

1. Volume / Compose labels / 生成 `.env` / Git worktree の対応を照合する。
2. Codex の実行中タスク、worktree を使用するプロセス、未コミット変更、全 mount 利用者を確認する。
   不明、作業中、未コミット変更がある環境は保留する。コンテナが停止中というだけで不要とは判断しない。
3. 未使用と確認できた環境だけ停止し、すべての Volume 利用者が停止済みであることを再確認する。
   旧 image の再起動で再生成しないよう、restart policy も no にする。
4. 対象 Volume を明示して `msm-cache` だけを削除する。symlink や想定外の mount があれば中止する。
   `projects`、`reference` などキャッシュ以外の一覧と hash を前後で照合する。
5. 使用量を再測定し、実施対象・保留理由・回収量を記録する。

主環境 `autonavlog_autonavlog-data` はこの整理の対象にしません。
`docker volume prune` や、キャッシュ整理のための `cleanup_worktree.sh` は使用しません。
cleanup は worktree の廃棄時に永続データごと削除する操作です。
Git worktree 本体の削除をキャッシュ整理と一緒に行わないでください。

## WSL の容量が戻らない場合

Issue #257 では、分離した Volume ごとに48時間分の MSM raw / normalized が生成され、
per-cache の20GiB・7日削除では並列環境の合計量を抑制できませんでした。
`unless-stopped` による再起動と、廃棄されず残った worktree runtime が継続生成の要因になります。
ディレクトリ内容による原因分析であり、別 Volume 間で全ファイルが同一 byte であることは確認していません。

Linux 内でファイルを削除すると `df` の使用量は減りますが、Windows の VHDX ファイルの長さや
C ドライブの空き容量は直ちに同じ量だけ戻るとは限りません。
Windows 側の圧縮は、全 WSL 作業を終了した後に Windows PowerShell で別途実施します。
WSL 内の実行中タスクから `wsl --shutdown` を呼ばないでください。
環境に応じた手順と今回の測定結果は [Issue #257 の検証記録](evidence/issue-257-worktree-cache.md) を参照してください。
