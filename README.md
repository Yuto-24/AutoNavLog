# AutoNavLog

AutoNavLogは、航空大学校 宮崎課程 NAV2の航法LOG作成を支援する、SR22 G6向けの
**地上準備専用**ツールです。計算結果は運航資料や完成帳票ではありません。利用者が
根拠と警告を確認し、別添8-1へ手書きで清書することを前提にしています。

## 現在の検証状態

アプリケーション、計算Policy、MSM連携、保存、KML/KMZ取込、Colab UI、テスト基盤を
実装しています。ただし、`data/performance/manifest.json`は`UNVERIFIED`です。
承認版飛行規程から二重照合した性能CSVとGolden NAV2 LOGが追加されるまで、性能を必要と
する実計算は明示的にBlockerとなり、結果を「検証済み」と表示しません。

空の性能CSVや空港CSVを仮の値で埋めないでください。

## 開発

Python 3.12を主対象とし、3.10〜3.12をサポートします。MSM実連携は
`jma-msm-wind` v0.2.1へ固定しています。

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

## Colab配布

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
- `src/autonavlog/presentation`: Colab UIとスマートフォン清書ビュー
- `notebooks/AutoNavLog.ipynb`: 利用者向けの薄い起動Notebook

詳細は[アーキテクチャ](docs/architecture.md)、
[計算規則](docs/calculation_rules.md)、
[データ来歴](docs/data_provenance.md)を参照してください。
