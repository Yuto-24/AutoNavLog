# Account Syncの2並列比較（2026-11-01）

温間条件の各2回では、`test:sync` を2 workersにすると平均143.14秒（17.1%）短縮した。各回とも同じ33件が成功し、失敗・skip・retry・flakyは0件だった。この結果に基づき、CIの`test:sync`だけを2並列にする。

CIジョブ全体の短縮率ではない。Local browserやpytestが長い場合、workflow全体の終了時刻は変わらない。

## 測定結果

| 条件 | 1回目 | 2回目 | 平均 | 各回の結果 |
| --- | ---: | ---: | ---: | --- |
| 1 worker | 843.49秒 | 834.78秒 | 839.13秒 | 33成功、失敗・skip・retry・flaky 0 |
| 2 workers | 711.75秒 | 680.23秒 | 695.99秒 | 33成功、失敗・skip・retry・flaky 0 |

条件調整用の冷間1 workerは843.36秒、33件成功。温間平均には含めない。温間の完走順序は2→1→2→1。
追加の2 workers実行は途中で計測回数を限定する指示が届いたため中断し、比較から除外した（298.64秒、exit 130、10 passed・2 interrupted・21未実行）。最後の1 workerはこの追加負荷の後に実行され、CPU・OS cacheはリセットしていない。

## 固定条件と限界

- 対象commit: `091c869be552dadcff7f996569b4966795a3c4df`。
- Ubuntu 24.04.5、Intel Core i7-1065G7、4コア/8スレッド、約34.8 GiB RAM。CPU affinity制限なし、governorはpowersave。ホスト活動・温度・周波数は統制していない。
- Node 24.21.0、Python 3.12.3、Java 21.0.13、Playwright 1.62.1、firebase-tools 15.10.1、Firestore emulator 1.20.2。
- Chromium 151.0.7922.34、iPad WebKit、iPhone WebKit（WebKit 26.5）。各11件、計33件。file/title/project識別子を全回で照合した。
- 同一checkout・lockfile・依存インストール・Localアセット。各回は新規Vite、browser process/context、新規emulatorと空DB。`CI=1`、`demo-autonavlog-sync`のみ使用し、本番データ・実アカウントは使わない。
- 温間条件は既存Vite/disk cacheを利用する。OS page cacheはflushせず、外部Pyodide/CDN取得の揺れは残る。
- 計測時間はnpmコマンドの開始から終了まで。pretestとVite起動を含む。依存導入、emulator起動・終了は含まない。
- 同時実行時には各ブラウザ単体の時間も増えた。このCPUで理想的な2倍の高速化は得られていない。
- 少数のローカル観測であり、統計的安定性やhosted CIの短縮を保証しない。migration/storage-upgradeは比較対象外。`test:firestore`の前提確認は別途4/4成功。
- benchmark、Playwright、Vite、emulatorの終了と対象ポートの解放、source/lockfile/assets不変を確認した。

## 再現方法

既存作業を保持した独立worktreeで、account-syncジョブと同じ依存導入・`AUTONAVLOG_TEST_FIXTURES=1 npm --prefix web run prepare:local`を行う。別の重い処理を同時起動しない。

各回で新規emulatorを使い、以下のコマンドだけのwall timeを`time.monotonic()`で測る。`RUN`は毎回異なる保存先、`WORKERS`は1または2。親のemulator起動時間とは分ける。

```sh
RUN="$(mktemp -d)"
WORKERS=2 # 1または2
CI=1 AUTONAVLOG_TEST_FIXTURES=1 FIREBASE_CLI_DISABLE_TELEMETRY=1 \
  npx --yes firebase-tools@15.10.1 emulators:exec --only firestore --project demo-autonavlog-sync \
  "PLAYWRIGHT_JSON_OUTPUT_FILE='$RUN/playwright.json' AUTONAVLOG_SYNC_OUTPUT='$RUN/artifacts' npm --prefix web run test:sync -- --workers=$WORKERS --reporter=line,json"
```

上記は起動形式であり、外側の`emulators:exec`全体を時間比較には使わない。実測ではemulator内からPython runnerを呼び、`subprocess.run`で内側のnpmコマンドを起動する直前と終了直後の`time.monotonic()`の差を記録した。

ログ・JSONには各回のexit code、33件の識別子、成功/失敗/skip/retryを保存する。grep、project除外、timeout緩和、assertion削減は行わない。依存バージョンは上記実測条件と照合する。

## 変更範囲

`.github/workflows/test.yml`のaccount-syncジョブ内で`test:sync -- --workers=2`を指定する。テストの対象と直列順序、Firestore・storage-upgrade・migrationのコマンドは維持する。ローカル既定の`workers: 1`は変更しない。
