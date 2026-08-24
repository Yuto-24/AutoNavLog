# 実MSM受入検査

`scripts/validate_real_msm_release.py`は、決定論的テストで使う
`FakeWeatherProvider`とは別の、実MSM専用の手動受入検査です。
`FakeWeatherProvider`、ラッパー、派生クラスは実MSMとして受理しません。

既定はネットワークへ接続しないオフライン事前検査です。次をすべて検査し、不足や不整合を
`SKIP`にせず終了コード1の`FAIL`にします。

- インストール済みdistribution、import済みmodule、adapterがすべて
  `jma-msm-wind==0.2.1`

```bash
python scripts/validate_real_msm_release.py \
  --cache-dir /tmp/autonavlog-msm-cache
```

ライブ試験は明示的な`--live`指定時だけRISHへ接続し、GRIBをキャッシュします。実行時刻を
再現可能にするため、対象時刻も必須です。

```bash
python scripts/validate_real_msm_release.py \
  --cache-dir /tmp/autonavlog-msm-cache \
  --live \
  --valid-time 2026-07-30T03:00:00Z \
  --output /tmp/real-msm-acceptance.json
```

ライブ試験は同じ時刻について、RJFM/RJFOそれぞれの5000 ft MSL上空風・気温と
MSM地上気温を問い合わせます。4結果がすべて`AVAILABLE`であることに加え、
次を検査します。

- `resolve_run`、同一Runのcoverage確認、`prepare_run`、`query_batch`が成功
- 元GRIB URLと、URLをキーにした64桁SHA-256の集合が一致
- Forecast Run、補間方式、格子・気圧面等のtraceが存在
- 地上気温が`tmp_surface`のK値から℃へ変換され、水平・時間補間のtraceを保持

オフライン事前検査の`PASS`はライブ取得成功を意味しません。実MSMのライブ受入に成功した
証拠は、`mode: LIVE`、`live_executed: true`、`status: PASS`を持つJSONだけです。
ここで使うRJFM/RJFO座標は実行経路を通すための受入プローブであり、アプリ本体の検証済み
空港データを代替しません。
