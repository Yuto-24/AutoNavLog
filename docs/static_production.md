# Static production (#121)

正式production pathは **Local Static Web**。標準Vite artifactをHTTPS配信し、
FastAPI / Docker / Cloudflare FunctionsをApplicationの必須runtimeにしない。
既存Application、Calculation、Persistence、Account Sync、Weatherの仕様は変更しない。
Docker Composeは移行中のLegacy runtimeとPython Referenceのサポート構成として継続する。
旧環境の停止は#146、deployment CI / 継続監視は#123、PWAは#187の責務。

この文書のbuild / dry-runは公開を行わない。**remote deploy、preview公開、DNS変更、
Firebase Rules変更、TAF Worker変更は個別の公開承認後に行う。**
#145で確保した `navmate.pages.dev` の既存配置は#121完了の証拠ではない。

## Buildと配布物

NodeとPythonの既存開発依存を用意する（CIと同じPython 3.12 / Node 24を推奨）。
`npm ci`はlockfileを使う。Python側の準備は[testing](testing.md)を参照。
`build:static`は既存prepare / Vite buildを呼び、`web/dist-static/`を生成する。
配信時にNode / Python / Dockerは不要。標準static hostでそのまま配信できる。

公開Web設定をignored `web/.env.local`に記載する:

```dotenv
VITE_FIREBASE_API_KEY=your-public-web-api-key
VITE_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your-project
VITE_FIREBASE_APP_ID=your-web-app-id
VITE_TAF_PROXY_URL=https://autonavlog-taf.utomatsu.workers.dev/taf
# 移行期間は既存Legacy originを設定する。#186の移行契約を維持する。
VITE_LEGACY_MIGRATION_URL=https://your-legacy-host
```

API keyはFirebase Web公開設定。service account / secret / tokenを`VITE_*`へ入れない。
production buildはFirebase 4項目、HTTPS TAF URL、MSM feedを必須とする。
Migration URLは新規のみの配置では省略可能だが、既存Legacy利用者を受け入れる
NavMate本番配置では設定する。[migration](legacy_migration.md)のorigin / CORS設定も確認する。
初回migrationのstatus確認は既存Legacyへの依存が残る（#186の仕様）。
移行完了済み端末の通常Local workflowへ新たなserver依存は追加しない。
Firebase authorized domains、Firestore Rulesとindex、TAF origin allowlistは各既存docsを正本とする。

```sh
source .venv/bin/activate
npm --prefix web ci
python scripts/prepare_msm_feed.py --output /tmp/autonavlog-msm-feed --cache /tmp/autonavlog-msm-cache
AUTONAVLOG_MSM_FEED=/tmp/autonavlog-msm-feed npm --prefix web run build:static
npm --prefix web run check:static
npm --prefix web run test:static-artifact
python -m http.server 4121 --bind 127.0.0.1 --directory web/dist-static
# 別terminal
npm --prefix web run test:static
```

`release.json`はVERSION、source commit、tracked / untracked変更の有無（`.codex`以外）、公開接続設定と
全配布ファイル（自分自身を除く）のbyte数 / SHA-256を記録する。
`build:static` / `check:static`の通常検査では`release.dirty === false`を必須とし、
modified / untracked sourceを含むartifactやdirty flag欠落を拒否する。
`check:static`は全ファイル・Local manifest・weather hash・期限・Pages制限も検証する。
開発中の未commit変更でdry-runを行う場合だけ、`npm --prefix web run build:static -- --allow-dirty`と
`npm --prefix web run check:static -- --allow-dirty`を明示する。このopt-outは公開用のgateではない。
配布前にはclean commitから再buildし、opt-outなしの通常検査を通す。
検査reportには`dirty` / `allowDirty`を残し、開発用の成功とproductionの合格を区別する。
改変後にinventoryを手で書き直さず、同じsource / 設定でbuildをやり直す。
配布前はclean commitからbuildし、release.json、検査結果、artifact archiveをprivateな
release保管場所へ保存する。artifactはFirebaseの公開識別子を含む。
`--allow-expired-weather`は過去artifactの調査専用であり、通常deployの合格条件ではない。

`AUTONAVLOG_TEST_FIXTURES=1`はproductionでは拒否する。`build:local`はfixture試験 /
開発用に残し、正式配布には使わない。`npm run build`は移行中のLegacy用。
どのbuildもApplicationのLocal失敗をLegacyへ自動fallbackしない。

## MSM feedの手動更新

[既存MSM producer / 運用契約](local_weather.md)をそのまま使う。
3時間ごとの生成を目安とし、同じproducer output directory / cacheを継続利用して
7日以内の旧Run assetを保持する。catalog lifetimeは6時間。
producerにProjectを送らず、static catalog / NPZだけを公開する。
Pagesは全artifact単位の配置なので、**現在配信中の承認済みsource commitと設定**から
新feedを同梱してbuild・checkし、全体を手動uploadする。app更新とfeed更新を
混ぜないため、未承認branchからweatherだけを配布しない。
定期ジョブ / CI / 継続監視は#123で自動化する。それまではoperatorがこの手順を実施する。

Pagesはatomicなdeploymentでcatalogとpayloadを一緒に切り替える。
他hostでin-place更新する場合はpayloadを先に置き、catalogを最後に置換する。
credentials不要、redirectなしの同一origin `/weather/msm/`を維持する。
feedが失効・停止しても旧予報を成功扱いせず、FORECASTだけを明示的に停止する。
保存済みProject / Last Calculationは保持し、FTDは利用できる。
fresh feedが用意できないときは通常リリースgateを止める。

## Runtime / version / cache policy

- Pyodide **0.27.7**の公式案内先jsDelivr CDNを継続利用する。
  versionはweb/package.jsonの既存exact pinからmanifestとWorkerへ渡し、loaderと揃える。
  full runtimeのself-hostや分割・独自loaderは導入しない。
  Python packageは既存のPyodide lock（pydantic / micropip / tzdata / numpy）と
  exact pin（defusedxml 0.7.1 / geographiclib 2.1）を使う。
  [公式配布方法](https://pyodide.org/en/stable/usage/downloading-and-deploying.html)
- root VERSIONが唯一のapp version。prepare時にwheel、reference / performance data.zip、
  manifestを同時生成する。ViteはVERSION / Pyodide pin不一致ならbuildを拒否する。
  JS Workerへmanifest全体のSHA-256を埋め込み、取得時にその値と照合する。
  各wheel / data.zipも既存SHA-256検査で照合してから使用する。
  同じversion番号の異なるbuildも混在させない。これは整合性検査であり署名ではない。
- 全same-origin responseは`Cache-Control: no-cache`とし、browserは再利用前にrevalidateする。
  PagesのETag / 圧縮を使い、独自Cache RuleやCache Everythingを追加しない。
  JS/CSSはViteのhash付きfilename。Local assetsは固定pathでもhash不一致を拒否する。
  CDNの固定version runtimeはCDNの通常cacheを使う。
- 既に起動済みWorkerは同じメモリ上のwheel/dataを使い続ける。再起動時に新旧が混在したら
  既存の再読み込みエラーで停止する。reloadで新artifactを取得する。
  Projectを消去したり、raw inputを修正したり、強制reloadしたりしない。
- Pagesの暗黙SPA fallbackで欠落wheelがHTMLになるのを防ぐためroot `404.html`を同梱する。
  Applicationの入口は`/`。queryによる認証・既存flowは維持する。
- Service Workerを登録しない。完全offline起動を保証しない。runtime CDN / PyPIが
  利用不能ならcold起動はできない。Storage永続化とoffline shellは別条件。
  本番hostを変えるとorigin storageも別になるため、移転前にGoogle同期完了を確認する。
  Project schema / Applicationは変えず、同じartifactと配信headerを新hostへ移す。

[Pages serving / cache](https://developers.cloudflare.com/pages/configuration/serving-pages/)、
[headers](https://developers.cloudflare.com/pages/configuration/headers/)をrelease時に再確認する。
別hostでも`_headers`の値をそのhostの標準設定へ移す。
MIMEはJS=JavaScript、JSON=application/json、wheel/zip/NPZ=binary。
HTTPSとWorker / IndexedDB / WebAssembly / Cache APIが使えるbrowserを対象とする。
独自COOP/COEP設定でGoogle popupを遮断しない。

## Pages dry-runと手動deploy

既存の固定Wranglerを使う（TAF Workerをdeployするコマンドは使わない）。

```sh
npm --prefix services/taf-proxy ci
# webの標準wrangler.jsoncを使い、TAF Worker設定と分離する
(cd web && ../services/taf-proxy/node_modules/.bin/wrangler pages dev --ip 127.0.0.1 --port 4122)
curl -I http://127.0.0.1:4122/
curl -I http://127.0.0.1:4122/local/manifest.json
curl -I http://127.0.0.1:4122/local/missing.whl
AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4122 npm --prefix web run test:static
```

Pages uploadに安全な`--dry-run`を仮定しない。上記artifact検査とローカルPages配信を
deploy dry-runとし、remote deploymentは発生させない。
`web/wrangler.jsonc`は配布設定のみ。Functions / bindings / application runtimeは含まない。

**公開承認後だけ**:

1. Cloudflareの対象account / project `navmate`、Free plan、production branchを確認する。
   #123まではautomatic production / preview deploymentを無効のまま維持する。
   Pages project作成、preview配置も無断では行わない。
2. Firebase authorized domains / Firestore Rules、TAF allowlist、Migration originを確認する。
   新originの場合はそれぞれの変更承認・手順を先に実施する。
3. 現行production deployment ID、commit、artifact、設定を記録し、rollback先を確保する。
4. clean release commitでbuildし、`npm --prefix web run check:static`を再実行する。
   確認済みaccountを環境変数`CLOUDFLARE_ACCOUNT_ID`で明示し、認証済みWranglerで:
   `(cd web && ../services/taf-proxy/node_modules/.bin/wrangler pages deploy dist-static --project-name navmate --branch main)`
5. 新deployment IDとURL、`/release.json`のversion・hash、HTML / manifest header、
   欠落assetの404を記録する。CDN cacheを含む実挙動はlocal emulatorだけで合格扱いしない。
6. 下記production gateを完了後、#121と#116のチェック・依存関係を更新する。

[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)と
[Git integrationの手動upload](https://developers.cloudflare.com/pages/configuration/git-integration/)を参照。
Dashboard drag-and-dropのfile上限はWranglerと異なるため、この手順ではWranglerを使う。

## Rollback

承認済みのrollbackはPages dashboardの対象project → Deploymentsで、記録済みの
**成功したproduction deployment**を選び「Rollback to this deployment」を実行する。
previewをproduction rollback先にしない。
[公式rollback](https://developers.cloudflare.com/pages/configuration/rollbacks/)

同じoriginで切り替え、browserをreloadし、versionとLocal保存済みProject /
Last Calculationを確認する。IndexedDBを削除しない。app rollbackはデータや
Firestore Rules / TAF Workerを巻き戻さない。将来schemaを変更した版は、
旧appが新schemaを読めることを先に検証し、互換性がなければforward fixを選ぶ。

旧artifactに含まれるMSM catalogが失効していたらFORECASTはfail closedする。
復旧後、旧版のsource / 設定とfresh feedでbuild・check・再配置する。
過去のNAV LOG閲覧とFTDまでをrollback初期確認に含め、期限切れMSMを成功扱いしない。
Pages上の切替操作と実originのcache / auth確認は公開承認後のgate。
localでは保存済みデータを残したreload、新旧manifest混在の拒否、
artifactの整合性を検証する。

## 無料枠の運用条件

公式情報確認日: **2026-09-19**。永続的な上限として扱わず、release時に再確認する。

- [Pages Free limits](https://developers.cloudflare.com/pages/platform/limits/):
  20,000 files、1 file 25 MiB、500 builds/月、同時build 1、build timeout 20分。
  全配布ファイルを検査するため、将来MSM保持数が増えても上限超過をupload前に止める。
  [static asset requestsは無料・無制限](https://developers.cloudflare.com/pages/functions/pricing/)。
  Pages Functionsを使わず、R2 / Paid planを通常運用の条件にしない。
- MSM更新を3時間ごとに行う場合は31日で248回。app更新20回を加えて268回という
  保守的な月間配置計画とし、account共有枠もoperatorが確認する。
  これは実際の利用量でもDirect Uploadの課金保証でもない。
  producerは既存マシン・無料runnerで実行し、有料runnerを必須にしない。
- [Workers Free](https://developers.cloudflare.com/workers/platform/pricing/):
  TAFは100,000 requests/日、CPU 10 ms/invocation。
  [TAF運用条件](taf_proxy.md)の100人×20回/日=2,000 requests/日を継続採用。
  cache・拒否requestと他Workerの利用も合算する。Freeのまま使い、枠超過はTAFのみ停止。
- [Firebase Spark](https://firebase.google.com/pricing/):
  payment method不要。既存Google認証 / Firestoreを維持しBlazeへ自動移行しない。
  [Firestore Free quota](https://firebase.google.com/docs/firestore/quotas)は1 GiB、
  read 50,000/日、write 20,000/日、delete 20,000/日、outbound 10 GiB/月。
  Rules依存read、listener再接続、transaction retryも消費する。
  個人・少人数利用の例として10人×100保存/日=1,000 writes/日を基礎とし、
  group / transaction / 複数端末分に5倍の余裕を見て5,000 writes/日。
  実利用はoperatorがFirebase usageで確認し、枠が足りなければSync停止を許容する。
  Local outbox / Project / Last Calculationは消さない。既存[Account Sync](account_sync.md)参照。
- jsDelivr、PyPI、OSM/GSIとJMA/RISHは外部の無料配信経路。SLA / 無制限利用を仮定しない。
  既存cache・取得失敗の挙動を維持し、自動課金serviceへfallbackしない。

## 保存と利用者への案内

Information内にbrowser storageの制約を表示する。
site data削除、storage eviction、private browsing終了でLocalデータを失う場合がある。
永続化要求は保持保証ではない。同期済みだけが別端末へ復元でき、
未同期outboxは端末消失時に復元できない。NAV LOG JSONはProject backup形式ではない。
reloadはsame-tab recovery、browser再起動は保存済み一覧から明示openする既存仕様。
Sync / Weather障害時の保持は既存テストとproduction artifact試験で確認する。

## Release acceptance gate

公開前に実行可能なgate:

- production build / artifact inventory・サイズ・hash・fresh feed検証
- 通常static serverとPages local emulatorで起動、header・404確認
- FTD / KMZ / 実feed FORECAST、保存・reload・browser process restart
- 新旧manifest混在時の拒否・再読み込み、Service Workerなし、storage案内
- 既存Weather障害・Account Sync障害回帰、Python Reference・Legacy gate
- Independent Reviewと再検証、PRとhosted CI（課金block時はlocal再現を明示）

**production実環境だけに残すgate（公開承認待ち）**:

- 承認済みartifactをPagesへ配置、実originのHTTPS / ETag / cache / 404 / version照合
- Google loginとAccount Sync、migration済みAccount、新規browserからの復元
- 同originから最新MSM / 実TAF、正常保存・restart、外部停止時のLocal継続
- 旧タブ→新配置のreload、実Pages rollbackと同origin storage保持
- Pages / Workers FreeとFirebase Spark、共有quota・初期利用量の確認
- #121 / #116の完了更新（承認前は完了checkboxを付けない）

実装・local検証・PR・merge・production acceptanceを別の状態として記録する。
