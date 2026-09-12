# Issue #117: Pyodide checkpoint

今回はPython / PyodideによるKML → 経路候補選択・確定 → FTD入力 → 計算 →
NAV LOG表示の検証用checkpointです。#117を完了・採用決定とは扱いません。

## 起動と切替

Legacyは従来どおりDocker Composeの `http://127.0.0.1:8123`。
Localはビルド時の開発設定 `VITE_CALCULATION_MODE=local` で明示選択します。
通常画面には方式選択を追加していません。Local画面のヘッダーは
`Pyodide Local PoC` と `再読込で入力を破棄` を表示します。

WSLでrepository rootから、Python 3.12の開発venv（build/setuptools/wheelを含む）を有効にします。

```bash
source .venv/bin/activate
npm --prefix web ci
npm --prefix web run dev:local
```

開発画面は `http://127.0.0.1:5174`。predevで最新Python wheelと実データを準備します。
Pythonはビルド時だけ必要で、配信後の計算はブラウザ内で実行します。

production artifactは次の手順で配信できます。静的HTTP配信にFastAPI・Docker・API proxyは不要です。

```bash
source .venv/bin/activate
npm --prefix web run build:local
python -m http.server 4174 --bind 127.0.0.1 --directory web/dist-local
```

`http://127.0.0.1:4174` を開きます。任意の静的HTTP配信へ同じartifactを置けます。
ファイルを直接開く `file://` は対象外です。本番hostingの選定・deployは行いません。

試す手順:

1. `tests/fixtures/issue_43_golden.kml` を選択またはKMLを貼り付けます。
2. 地図と候補を確認し、「地図とKML記載順を確認しました」をチェックして経路確定。
3. FTD風を地上360°/15 kt、5,000 ft 270°/30 ktにします。
4. RJFM発6,500 ft、米ノ津発7,500 ft、玉名発6,500 ftを入力。
   大牟田VREPは通常の自動計画2,500 ft、佐賀場周は同梱masterの1,000 ftです。
5. 「NAV LOGを作る」でRCA・EOC・TTL DIST・TTL TIMEと各区間を確認します。
   Goldenの日付は2026-09-11、ETDは09:00 JSTです。

## 再利用と最小境界

- `CalculationService`、KML importer、route normalization、FTD provider、
  readiness、NAV LOG display projectionをPythonのまま再利用します。
- `autonavlog.local.LocalApplication` は既存facadeの操作を呼ぶ小さな接続です。
  現行UIの操作名に `/api/` を残していますがLocal時はHTTPを実行しません。
  Application contract全体の整理は#118に残します。
- Pyodide公式npm package、`loadPyodide` / `loadPackage` / `unpackArchive`、
  micropipを使用。独自loader・archive parser・runtimeは作っていません。
- WorkerはUI threadを計算から離します。標準WorkerだけではPromiseのRPCと
  例外伝搬を提供しないため、小さな既存library Comlink 4.4.2を追加しました。
  mutationは同じWorker上で順番に実行し、例外は既存UIのエラー表示へ伝えます。
- 通常のsetuptools wheelを、Web staticの不要な同梱物だけ除いたsourceから作成します。
  PerformanceとReferenceは実データをzipで同梱し、既存repositoryで検証・読込します。
  Golden数値や専用経路は実装へ埋め込んでいません。
- 既存ProjectRepositoryはPyodide標準MEMFS上の一時directoryで再利用します。
  Browser storageにはmountせず、reloadで消えます。保存/復元の新実装はありません。
  明示保存は無効、保存済み一覧は空です。#124の永続化とは別物です。

## Runtimeと制約

Pyodide 0.27.7（Python 3.12.7）を固定しました。現行Python packageの
`>=3.12,<3.13` に合わせるためで、最新runtimeへの追従とは別判断です。
[公式changelog](https://pyodide.org/en/0.27.7/project/changelog.html)、
[公式package loading](https://pyodide.org/en/0.27.7/usage/loading-custom-python-code.html)
を参照してください。

主要packageはPyodide配布のPydantic 2.10.5 / pydantic-core、tzdata 2024.1、micropip 0.9.0、
純Python wheelのdefusedxml 0.7.1 / GeographicLib 2.1です。
これらは既存Python依存またはBrowserで必要な配布方法を使っています。
Pydanticのnative extensionは通常のdesktop wheelを流用せずPyodide同梱buildを使います。

`autonavlog.web.__init__` のeager importがFastAPI・認証・MSM初期化を引き込むため、
公開entry pointを遅延importにしました。facadeの型注釈だけの認証・prewarmer依存は
TYPE_CHECKINGに移しました。FastAPI、uvicorn、PyJWT/crypto、MSMはLocal経路へ
loadしません。これはこれらの全packageがPyodide非対応という評価ではありません。

初回はjsDelivrのPyodide runtimeとPyPIの純Python wheelを取得するためnetworkが必要です。
完全offline・Service Worker・cache管理は未実装。CDNやasset取得失敗はUIへ伝播します。
Loader失敗はそのsession内で自動retryせず、再読込で再試行します。
Workerの致命的エラー・10分の処理timeoutも失敗として示します。ユーザー操作による
計算キャンセルや新しい進捗UIはありません。

FTDのみ対応し、FORECAST・KMZ・Project保存は明示的に拒否します。
地図tileは従来の外部配信を利用します。LocalでAPIへ自動fallbackしません。
既存LegacyのAPI・認証・永続化は維持します。

## 再現可能な検証

`tests/fixtures/issue_117_ftd.json` が入力です。
`scripts/local_reference.py` はLocal bindingを使わず、Legacy Python facadeへ
通常のimport/confirm/update/calculateを渡してReferenceを生成します。
`issue_117_ftd_golden.json` はその結果の意味的投影です。

`web/e2e/helpers/localGolden.ts` はPhysical Legとnode座標、Calculation Zoneの全主要値、
RCA/EOC座標、表示cell、燃料、TTL、warning/blockerとpolicy/data revisionを比較します。
ランダムUUID・時刻・fingerprintは比較対象外。表示文字列・状態・コードは完全一致、
内部数値の絶対許容差は1e-8（各fieldのnative単位）です。丸めて差を隠しません。

```bash
source .venv/bin/activate
python scripts/local_reference.py > /tmp/issue117-reference.json
python -m pytest tests/integration/test_local_calculation.py tests/unit/test_version.py
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run build:local
# 別terminalで上記static serverを起動してから:
cd web
npm run test:local
# 開発serverにも同じbrowser testsを実行できる:
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:5174 npm run test:local
```

Browser testはAPI通信を監視し、`**/api/**` をabortしたまま通常UIを完走します。
Workerの実際のComlink返信をtest側で観測し、production専用debug APIは作りません。
1,100 pxの入力→経路→準備→NAV LOG順序、1,440 pxの三列配置も座標で確認します。
asset取得失敗と未対応気象の失敗表示も回帰対象です。正常計算後にtest側で
Comlink requestへPython validation例外を注入し、エラー表示とNAV LOGの完全保持を検証します。
Workerの致命的停止後は既存draft保存の失敗表示になり、同じNAV LOGを保持します。
Python integrationはCalculationServiceの例外時にもlast-goodが保持されることを検証します。
強風200 ktのFTDケースでは、非空のWarning/Blockerを含む結果もPython Referenceと比較します。

## このcheckpoint後に残す判断

利用者の操作確認、Windows Chromium / iPhone Safariでの正式な実用性評価、
初回/再起動load・計算時間・memory・UI応答性の正式計測、MSM/TAFの接続成立性、
最終Architecture Decisionは未完了です。Rust/WASM・TypeScript PoCへは進んでいません。
#118/#119/#120/#121/#124/#125の本格移行、PWA、完全offline、KMZ、
進捗UI(#159)、キャンセル(#160)、本番配信はこのcheckpointに含めません。

## Checkpointで確認した結果

- Docker test stage: ruff、mypy、pytest 743件、Release source、Performance/Runtime data、
  schema整合、sdist/wheel buildが成功。Pythonの計算仕様は変更していません。
- Web typecheck、release-notes 6件、DockerのLegacy production buildが成功。
- 静的productionとViteのGolden・失敗・配置testsが各5件成功。
  強風Warning/Blocker差分testも追加し、静的productionとViteで成功しました。
- GoldenはTTL DIST 125.5 NM、TTL TIME 0:57。全比較項目が上記許容差内で一致。
  強風caseはWIND_TRIANGLE_FAILEDなどのBlockerとITERATION_NOT_CONVERGEDなどの
  Warningをそのまま表示し、成功へ置き換えません。
- APIをabortし、Browser context内のWorkerを含むrequestを監視して0件を確認。
- Local wheelは約249 KB、同梱data zipは10,066,340 bytes。
  このほかにPyodide runtimeとpackageのnetwork転送が必要です。
- 1,100/1,440 pxの座標検証と画面確認を実施。Linux/WSLのPlaywright Chromiumでの
  自動検証であり、Windows Chromium/iPhone Safariの正式な実機評価ではありません。
- 別モデルによる独立レビューで指摘されたLegacy favicon欠落と計算後失敗のbrowser
  coverage不足を修正し、再レビューで未解決指摘なしとなりました。

初期host検証では旧インストールmetadataのversion testが1件失敗しましたが、
既存uvで1.11.4へ更新後のversion testsは成功しています。root所有の既存web/distへ
host buildを書き込めなかったため、Legacy buildはDockerで検証しました。
これらはPyodideの不成立理由ではありません。
