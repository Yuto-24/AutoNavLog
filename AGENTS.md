# AutoNavLog project rules

## Responsive workflow order

- At 1,240 px and narrower, keep the primary workflow in one vertical direction: input, route and map, readiness, NAV LOG, then contextual guidance associated with the NAV LOG.
- Do not reorder a later workflow step above an earlier one at intermediate widths; the user must not need to scroll back up to continue after checking the route or map.
- Keep wide layouts above 1,240 px aligned as a three-column input, route, and readiness workspace unless a feature explicitly requires another layout.
- Every layout change must include browser regression coverage at an intermediate width around 1,100 px and a wide width above 1,240 px. Assert the relative vertical or horizontal positions of the workflow regions, not only their visibility.

## プラン実施時のルール

5.6 Sol はオーケストレーターとして振舞い、原則として実装はしないでください。
実装・調査・テストには必要に応じて Sub Agent / Sub Thread を使用し、Terra、Luna、5.4、5.3-spark を適切に割り当ててください。

Review は実装を担当したモデルとは独立したモデルが実施してください。
同一モデルによる自己 Review のみで完了としてはいけません。

## 過去知識の再利用

新しい作業に着手する前に、Repository 内の AGENTS.md、docs、Issue、PR、過去の設計判断、既知の失敗、類似実装など、利用可能な既存知識を確認してください。

既に解決済みの問題について、不要な再調査・再実装・同じ失敗を繰り返さないでください。
可能な限り、過去に到達した地点を今回の作業の開始地点としてください。

過去の知識は無条件に適用せず、現在のコード・仕様・依存関係との差分を確認してから利用してください。

## 作業から得た知識の保存

作業中に、将来の類似タスクにおける判断を変えうる知見を得た場合は、その場限りで失わず、適切な場所へ記録してください。

特に以下は再利用可能な知識として扱ってください。

- 非自明な設計判断とその理由
- Root cause とその根拠
- 失敗したアプローチと失敗理由
- 有効だった修正方法
- 再発防止策
- Repository 固有の制約・慣習
- 複数箇所・複数タスクで再利用可能な実装パターン
- 今後の調査や判断を短縮できる検証結果

Raw log、単なる作業履歴、コードから容易に読み取れる内容、既存情報の重複は原則として知識として保存しないでください。

## 知識の一般化

一度だけ観測された事象を、直ちに一般ルールとして扱わないでください。

まず案件固有の Observation / Learning として保持し、別の箇所や別のタスクでも同じ構造が確認された場合に Pattern として一般化してください。

十分な再現性と根拠が確認されたもののみ、Repository 全体または今後の作業に適用する Rule へ昇格してください。

既存ルールと矛盾する新しい証拠が得られた場合は、古いルールをそのまま維持せず、根拠を確認して更新してください。

## コンテキスト管理

AGENTS.md や常時読み込む指示には、すべての知識を詰め込まないでください。

常時必要なルールは小さく保ち、詳細な設計判断、過去の失敗、検証結果、類似事例などは外部のドキュメントとして保持し、必要になった時だけ参照してください。

新しい情報を常時ルールへ追加する前に、「毎回の作業で本当に必要か」を判断してください。
