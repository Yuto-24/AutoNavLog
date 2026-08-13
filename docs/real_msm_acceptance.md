# 実MSMリリース受入ゲート

`scripts/validate_real_msm_release.py`は、決定論的テストで使う
`FakeWeatherProvider`とは別の、実MSM専用リリースゲートです。
`FakeWeatherProvider`、ラッパー、派生クラスは実MSMとして受理しません。

既定はネットワークへ接続しないオフライン事前検査です。次をすべて検査し、不足や不整合を
`SKIP`にせず終了コード1の`FAIL`にします。

- インストール済みdistribution、import済みmodule、adapterがすべて
  `jma-msm-wind==0.2.1`
- 指定したPzs由来`terrain.npz`が存在し、読込可能
- 地形元データのSHA-256が記録済み
- RJFM/RJFOの両地点でモデル地形が有限値
- 検査した地形と`MsmWeatherProvider`へ設定した地形が同一

```bash
python scripts/validate_real_msm_release.py \
  --terrain /content/terrain.npz \
  --cache-dir /content/msm-cache
```

ライブ試験は明示的な`--live`指定時だけRISHへ接続し、GRIBをキャッシュします。実行時刻を
再現可能にするため、対象時刻も必須です。

```bash
python scripts/validate_real_msm_release.py \
  --terrain /content/terrain.npz \
  --cache-dir /content/msm-cache \
  --live \
  --valid-time 2026-07-30T03:00:00Z \
  --output /content/real-msm-acceptance.json
```

ライブ試験は同じ時刻・5000 ft MSLについてRJFM/RJFOそれぞれの上空風・気温と
MSM推定QNHを問い合わせます。4結果がすべて`AVAILABLE`であることに加え、次を検査します。

- `resolve_run`、同一Runのcoverage確認、`prepare_run`、`query_batch`が成功
- 元GRIB URLと、URLをキーにした64桁SHA-256の集合が一致
- Forecast Run、補間方式、格子・気圧面等のtraceが存在
- QNHのPzs元データSHA-256が事前検査した地形と一致
- QNHが`MSM推定QNH`と表示され、廃止した重複警告コードを含まない

オフライン事前検査の`PASS`はライブ取得成功を意味しません。実MSMをリリース済みと判定
する証拠は、`mode: LIVE`、`live_executed: true`、`status: PASS`を持つJSONだけです。
ここで使うRJFM/RJFO座標は配布経路を通すための受入プローブであり、アプリ本体の検証済み
空港データを代替しません。

## Driveリリースworkflow

`.github/workflows/release-to-drive.yml`の手動実行では、
`msm_valid_time_utc`を`YYYY-MM-DDTHH:MM:SSZ`形式で必ず指定します。暗黙の現在時刻へ
フォールバックしないため、同じ入力による再実行は同じForecast valid timeを検査します。
将来scheduled triggerを追加する場合も、version管理された固定UTC値を供給するまでは
fail-closedとし、実行時の時計から値を生成してはいけません。

workflowはrelease treeを組み立てて2つのwheelを新規venvへインストールした後、次の
証跡を`release/acceptance/`へ保存します。

- `runtime-data-acceptance.json`: 配布tree内の空港・性能データに対する厳格検査
- `real-msm-acceptance.json`: 指定時刻に対する実MSMライブ受入検査

片方が失敗しても他方を実行して両方の診断を収集し、JSONをGitHub Actions artifactへ
退避してからjobを失敗させます。両方が成功した場合だけ証跡を含む
`release-manifest.json`を作成し、Driveへアップロードします。
