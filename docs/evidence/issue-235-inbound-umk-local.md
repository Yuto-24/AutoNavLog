# Issue #235: 新田CENTER往復経路の補完と計算契約

RJFM→小丸の往路と、小丸→RJFM・小丸→有田→RJFMの帰路で、必要な参照点を補完する。
NAV LOGは往路RJFM→小丸、帰路小丸→RJFMを親Legとし、子Legに各実区間の距離・TC/MCを表示する。
この記録は実装契約と事前ローカル検証の根拠。PR・hosted CI・mergeの状態はGitHubを正本とする。

## 確定した計算契約

- 往路は不足する実UMKを補う。RJFM→UMKとUMK→小丸の幾何を分ける。
  UMKまでのETE・燃料は既存のPOH 5,500 ft上昇値を使い、距離÷GSで置き換えない。
- 帰路は不足するUMK・有田を補う。子Legは小丸→UMK→有田→RJFM。
  UMKは4,500 ft、有田への採用到達高度は1,500 ft。
- UMK→有田の時間は高度差÷選択降下率で計算する。
  500 fpmでは360秒・1.2 gal、1000 fpmでは180秒・0.6 gal。
  燃料は既存の降下流量12 gphを使い、この有田profileにはlevel off後60秒を加えない。
- 国富方面への延長は独立した仮想案内に限る。NAV LOGの物理距離を水増ししない。
  他の最終VREPへの従来profileは維持する。
- 親子DIST・ETE・燃料の表示合計を整合させる。巡航と運用降下を含む親行でも、
  運用降下の丸め配分と他の子区間の表示値を合計する。
- 目的空港の情報・気温・TAF表示と空港標高の行を維持する。

本人の指示は、往路についてSlack `1793418475.712199`、帰路について
`1793423353.155839`・`1793423570.097529`。これらが当初のIssueの未決事項を確定した。
出典と仮想案内の制限は [RJFM inbound guidance](../rjfm_inbound_guidance.md) を参照。

## 入力・保存の保持

明示UMK・有田は名前、座標、手動入力を保つ。有田への既存手動高度なども入力として残し、
今回の帰路では採用高度1,500 ftを適用する。生成有田には既存arrival-policy参照座標を使う。
AIP VREP snapshotを捏造しない。小丸の地図クリックは既存の同梱OMARU座標を使う通常のMAP点。
繰り返しクリックは既存VREPと同じく別の訪問として残す。
小丸の注記はAD2由来のVREPと区別し、運用参照図の未検証デジタイズ・推定誤差0.35 NMを表示する。
この注記とtooltipの文言はGemini CLI（`gemini-3-flash-preview`、2026-10-31）が決定した。
生成UMKで分割する元Legの手入力距離は測地線距離比で按分し、元の合計と解除時の復元を保つ。

補完は冪等で、再適用・保存復元時に生成IDを変えない。往復では往路・帰路それぞれのUMKを保持する。
明示点への置換時は、元の隣接関係に戻った場合だけ旧区間の手入力・役割・高度等を復元する。
Check Pointは分割後の有効なabeam投影へ紐づけ、生成点解除時に元区間へ戻す。

帰路ポリシーV2を計算fingerprintに含める。V1保存データは読み込めるが、旧420秒の計算結果は
再計算待ちとする。旧結果を新しい360秒の計算済み結果として扱わない。
復元は下書きをcopyして正規化し、Last Calculationを保持する。再計算やdisk autosaveは実行しない。

## 事前ローカル検証

以下はmain取り込み前の `b515afc7` に対する結果。main取り込み後と最終HEADの結果はPRへ記録する。

| 検証 | 結果・範囲 |
| --- | --- |
| 関連Python回帰 | 142 passed。往復補完、手動入力、親子表示、CP、解除、保存・鮮度を確認 |
| Local計算・軽量Legacy HTTP | 36 passed。実参照guidanceテストは別実行 |
| 実参照guidance HTTP | 1 passed。新6分・1,500 ftで数値候補を確認。旧期待値変更後の実行は12.56秒 |
| 実Pyodide 0.27.7 | 67 passed。同じwheelで往路・帰路のunitとKML/MAP計算経路を検証 |
| 必須・静的チェック | Ruff、mypy 77 files、schema export、release validator、version 2 tests、web typecheckが成功 |
| 地図クリック | Chromium 1 passed。小丸クリック、reload、再クリック。前段の `7eba3e40` / `a486fd56` で確認 |
| 専用runtime | port 33636、health ok / 2.1.0。実HTTPで直接帰路500 fpm=360秒、有田経由1000 fpm=180秒を確認 |

帰路の16条件はKML/MAP、有田の有無、明示UMKの有無、500/1000 fpmの組合せ。
実区間の座標・距離・方位、6分/3分と燃料、親子合計、到着情報、保存復元を確認する。
明示有田・UMKが既にある旧V1結果を復元した場合の鮮度も別ケースで検証する。

検証コード:

- [帰路の計算・保存契約](../../tests/integration/test_issue_235_return_policy.py)
- [KML/MAP補完と往路POH・再計算](../../tests/integration/test_issue_235_inbound_route.py)
- [帰路の生成点・手動点・解除](../../tests/unit/test_rjfm_inbound_plan.py)
- [往路の補完・CP・往復](../../tests/unit/test_rjfm_departure_plan.py)
- [実参照guidanceとLegacy HTTP](../../tests/integration/test_rjfm_inbound_web.py)
- [小丸クリック](../../web/e2e/map-route.spec.ts)

## 独立レビューで固定した回帰

GPT-6 AstraがSol実装を別の読み取り専用contextで確認した。指摘は修正と回帰テストを追加し、
最後の再レビューは指摘なし。レビュー担当はファイルやruntimeを変更していない。

- 往復で帰路の集約が往路の集約を飛ばさない。
- 往路の生成UMKが帰路UMKの補完を抑止しない。
- 分割前の直行区間のTC/DISTを、明示点への置換後の別区間へ復元しない。
- 保存済み上昇CPの関連区間を維持する。
- 以前の有田通過によって帰路生成点IDが変わらない。
- 最終visual区間を親行に含めても目的空港情報を失わない。
- 旧V1計算をstaleにする。
- ROUTE_POINTの明示有田へ置換しても、arrival planの手動高度・理由・場周高度を失わない。

## 検証の限界と運用境界

fixtureは既存の空港発着Project内の帰路Legを検証する。始点・終点の空港要件と経路作成UIは変更しない。
本人の実KML/Projectは未取得。実端末、全ブラウザWorker flow、実飛行、本番Static Web受入を示す結果ではない。
事前検証以降の注記修正と幅別の画面順序検証は、PRの最終検証記録を参照。

Browser pluginは環境の `sandboxCwd is not a local file URI` で初期化できず、地図クリックはrepoの
Playwrightで検証した。一般的なasset 404 console出力は残ったが対象操作は成功した。
HTTPテストがsandbox内で停止した試行は中断し、host-capable実行の成功と区別した。

専用Compose projectは `autonavlog-wt-f34e8230`。volume
`autonavlog-wt-f34e8230_autonavlog-data`を保持し、primary runtimeを変更しない。
Release・本番公開・予報workflowはこの変更の範囲外。

## 実装の参照点

- `7107a36d`: 帰路の不足UMK補完と入力保持。
- `7eba3e40`: 親子Leg・TC/MC・小丸クリック。
- `a486fd56`: 往路の実UMK補完とPOH時間維持。
- `b515afc7`: 確定した有田帰路・別時計・保存鮮度。

各段階の詳細な当時の検証記録は上記commitの本ファイルに残る。
