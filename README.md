# NavMate

**NavMate** は、航空大学校の宮崎課程 NAV2 における航法 LOG の地上準備を支援する、SR22 G6 向け Web アプリです。地図上で経路を作成するほか、Google Earth の KML/KMZ を取り込み、飛行計画と気象データから NAV LOG を計算します。

**[NavMate を開く](https://navmate.yuto24.com)**

NavMate の表示内容は非公式の地上準備資料です。運航資料、完成帳票、別添 8-1 の原本としては使えません。航空大学校の公式様式や計算規則への準拠を保証するものではなく、航空情報・気象・性能データ・警告は利用者が確認してください。

- [現在のバージョン](VERSION) / [変更履歴](CHANGELOG.md) / [既知の不具合](KNOWN_ISSUES.md)
- [計算規則](docs/calculation_rules.md) / [一次資料の確認状況](docs/primary_source_audit.md)

## 主な機能

- **経路作成・取込**：MAP 上で空港・経由地点を選択。KML/KMZ のファイル選択、ドラッグ＆ドロップ、「Google Earth からペースト」に対応
- **NAV LOG 計算**：SR22 G6 の性能、飛行計画、MSM の風・気温を用いた計算。利用可能範囲に応じて GSM 日本域へ切替
- **気象・計算結果の確認**：採用した気象モデルと Forecast Run、目的地 TAF の参考風、計算結果と警告を表示
- **宮崎周辺の経路補助**：RJFM 発着の一部経路で UMK・有田などの区間を補完し、MAP 上に関連する空域情報を表示
- **Project 保存・同期**：ブラウザ内の自動保存、明示保存、計算済み NAV LOG の復元。Google アカウントで別端末との同期に対応
- **計算結果の持ち出し**：表示中の NAV LOG を JSON でダウンロード、またはクリップボードにコピー

機能の詳細と適用条件は [計算規則](docs/calculation_rules.md)、[MAP での経路作成](docs/map_route_builder.md)、[気象選択](docs/forecast_selection.md) を参照してください。JSON の計算結果出力は Project のバックアップ形式ではありません。

SEA・DEM を利用する地形高度の評価は、現行の NAV LOG 計算には含めていません。気象・地形・空域表示は飛行可否や経路の安全性を保証しません。

## 利用とデータ保存

本番 URL は **https://navmate.yuto24.com** です。Cloudflare Pages で配信する Static Web 版が正式な利用経路です。通常の画面操作・Project 保存・NAV LOG 計算はブラウザ内で行い、アプリケーションの実行に FastAPI や Docker サーバは必要ありません。

Project の作業内容と最後に正常完了した計算結果は、ブラウザの IndexedDB に保存されます。通常の編集は自動保存され、明示保存は名前付きのチェックポイントを作成します。同じタブの再読み込みでは作業状態を復元できますが、新しいタブや端末では保存済み Project を明示的に開いてください。

Google ログインを利用すると、対応する Project を Firestore 経由で同期できます。未ログインでもブラウザ内で作業できます。同期が失敗してもローカル保存を優先しますが、他端末へ反映済みとは限りません。競合が発生した場合は利用者による選択が必要です。

**データ保護上の注意**：ブラウザのサイトデータ削除、ストレージの消去・容量制限、別ブラウザへの切替などにより、未同期の Project を失うことがあります。Google ログインだけで未同期データの保全は保証されません。また、初回起動や外部気象データの取得にはネットワークが必要です。完全オフラインでの起動は保証しません。

詳しくは [Local Persistence](docs/local_persistence.md)、[Google ログイン](docs/authentication.md)、[Account Sync](docs/account_sync.md)、[Legacy からの移行](docs/legacy_migration.md) を参照してください。

## 経路の作成と計算

1. 初期 MAP で出発空港と経由地点・到着空港を選択するか、KML/KMZ を取り込みます。
2. 経路候補が複数ある場合は対象を選び、MAP と地点の順序を確認して経路を確定します。
3. Planning で飛行計画・性能・高度などを確認し、NAV LOG を計算します。
4. 計算結果・警告・参照した Forecast Run を確認します。入力変更後は必要に応じて再計算します。

KML はファイル選択、ドラッグ＆ドロップ、クリップボードからの貼り付けに対応します。複数 `LineString` の連結条件、Point Placemark の扱い、経路候補の選択規則は [MAP Route creation](docs/map_route_builder.md) と [計算規則](docs/calculation_rules.md) を参照してください。

気象の更新や通信障害時には、過去の正常な計算結果が残る場合があります。**表示中の計算結果が現在の入力と一致するか**、使用したモデル・Run とともに確認してください。気象データの取得方式と更新判断は [Forecast selection](docs/forecast_selection.md) に記載しています。

## 開発環境

本番は **React / TypeScript / Vite** の Static Web です。計算コアは **Python** で実装されており、ブラウザでは **Pyodide** を使って実行します。Project の永続化には IndexedDB、Google ログインには Firebase Authentication、アカウント同期には Firestore を使用します。気象データは静的配信と独立した TAF Proxy などから取得します。

ソースを取得した後は、次の文書を参照してください。

| 目的 | 参照先 |
| --- | --- |
| アーキテクチャと実装境界 | [Architecture](docs/architecture.md)、[Application Contract](docs/application_contract.md) |
| Static Web のビルド・検証 | [Static production](docs/static_production.md) |
| ローカル計算の構成・実行 | [Local Calculation](docs/local_calculation_poc.md) |
| 気象データの生成・運用 | [Local Weather](docs/local_weather.md)、[Static operations](docs/static_operations.md) |
| テストの使い分け | [Testing](docs/testing.md) |
| Agent による開発・レビュー | [AGENTS.md](AGENTS.md)、[Agent workflow](docs/agent_workflow.md) |

### 基本的な検証コマンド

依存関係を導入した開発環境で、変更箇所に応じた検証を実行します。Static Web の本番用 artifact は設定値と実気象データの準備が必要なため、以下の一般的な Frontend 検査だけでは本番受入になりません。

```bash
npm --prefix web run typecheck
npm --prefix web run test:application
npm --prefix web run test:e2e
pytest
ruff check .
```

Static Web の build、artifact 検証、Browser E2E、公開手順は [Static production](docs/static_production.md) にまとめています。CI・release の運用は [Static operations](docs/static_operations.md) と [Release lines](docs/release_lines.md) を参照してください。

## Legacy 版（Docker / FastAPI）

従来の FastAPI / Docker Compose 版は、移行・検証用の Legacy 環境として残っています。**現行本番の利用開始に Docker は必要ありません。** Legacy 版の Project 保存、Cloudflare Access identity、Server storage、Docker volume と接続・更新方法は Static Web とは別の仕様です。

- [Local Docker 起動](docs/local_static_docker.md)
- [Cloudflare Tunnel による旧環境の公開](docs/cloudflare_tunnel.md)
- [Legacy からのデータ移行](docs/legacy_migration.md)
- [サーバ廃止の準備](docs/server_retirement.md)

Legacy のサーバ保存・復元手順を Static Web のデータ保護策として利用しないでください。特に Docker volume を扱う際は、[Legacy 運用手順](docs/cloudflare_tunnel.md)に従い保存データを削除しないでください。

## ライセンスと問い合わせ

`pyproject.toml` では `LicenseRef-Proprietary` を指定しています。利用・再配布条件はリポジトリ所有者に確認してください。不具合や変更要望は [GitHub Issues](https://github.com/Yuto-24/AutoNavLog/issues) へ登録してください。
