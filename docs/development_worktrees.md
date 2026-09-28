# Codex Worktree 開発環境

Codex の Worktree で複数の変更を並列に進める場合、通常の AutoNavLog runtime と別 Worktree の runtime を共有しません。
Worktree ごとに Compose project、Docker image、host port、named volume を分離します。

この仕組みは Codex のローカル環境向けです。通常 checkout の `autonavlog` project、`autonavlog:local` image、8123番 port、既存の `autonavlog-data` は変更しません。

## Codex のローカル環境設定

Codex の Settings > Environments で AutoNavLog の Linux 設定を次のようにします。

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

Docker daemon が停止している場合、cleanup は Worktree のローカル生成ファイルだけを削除し、Docker resource が残ったことを警告します。

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
