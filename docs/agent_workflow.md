# エージェントのモデル選択と設定

このプロジェクトでは、用途に応じて GPT-6 Astra と GPT-6.1 Sol を選びます。
実装担当を一方に固定せず、そのタスクで選択したモデルが調査・実装・テストを担当します。
共通の作業・完了条件は [AGENTS.md](../AGENTS.md) を正本とします。

## モデルの切り替えと独立レビュー

| タスクの実装担当 | 独立レビュー担当 |
| --- | --- |
| GPT-6 Astra (`gpt-6-astra`) | GPT-6.1 Sol (`gpt-6.1-sol`) |
| GPT-6.1 Sol (`gpt-6.1-sol`) | GPT-6 Astra (`gpt-6-astra`) |

モデルと reasoning effort はユーザーが選択した値を引き継ぎます。
タスクの難易度だけで別モデルへ切り替えたり、調査・実装・テストを別エージェントへ
委任したりしません。ユーザーが委任を求めた場合は、その範囲に従います。
UI 文言を決める Gemini CLI の役割は、AGENTS.md の既存ルールに従います。

レビューは別の読み取り専用コンテキストで行い、次を渡します。

- ユーザーの要求、受入条件、関係する Issue / PR と設計判断
- 対象 checkout、base / HEAD または未コミット差分、変更対象ファイル
- 実行した検証と結果、未検証事項

レビュー担当は差分と関連実装を直接確認し、根拠と位置を付けて指摘します。
修正は実装担当が行い、影響する検証と指摘の再確認を行います。
PR にはレビュー担当モデルと結果を記録します。途中で両モデルが実装に参加した場合は、
同じ変更の実装に参加していないレビュー担当を別途確保し、確保できなければ未完了と報告します。
レビュー手段や相手モデルが使えない場合も、独立レビュー済みとは扱いません。

Codex の `review_model` は `/review` 用の固定上書きで、未設定時はセッションと同じ
モデルが使われます。したがって、単に `/review` を呼ぶだけではこのプロジェクトの
異なるモデルによるレビュー条件を満たしません。利用中のツールで相手モデルを明示し、
実際に選択されたモデルを確認します。自動的に反対側へ切り替わる設定とは扱いません。

## 設定の置き場所

| ファイル・設定 | 役割 |
| --- | --- |
| `AGENTS.md` | 両モデルが守るプロジェクト固有の制約と完了条件 |
| `.codex/config.toml` | 必要な場合だけ追加するプロジェクト設定。用途別選択を保つため、共通の `model` / `review_model` は固定しない |
| `~/.codex/config.toml` | 個人の既定モデル・reasoning effort・権限など。ほかのプロジェクトにも影響するため、プロジェクトの指示更新に伴って変更しない |
| `.codex/environments/environment.toml` | アプリが管理するローカル環境の setup / cleanup。モデル選択とは別の設定 |

Codex のプロジェクト設定は trusted project で読み込まれます。CLI では明示的な
引数・`--config`、プロジェクト設定、選択した profile、ユーザー設定の順に優先されます。
ローカルの設定ファイルに書かれた既定値だけから、実行中のチャットのモデルや権限を
断定しないでください。アプリ・実行環境が示す現在の選択と適用された制約を確認します。

自動生成された環境ファイルは手編集せず、アプリの環境設定で管理します。
Linux の setup / cleanup と隔離 runtime の手順は
[development_worktrees.md](development_worktrees.md) を参照してください。

## 参照先と確認範囲

調査は変更に関係する資料・既知の不具合・類似実装から始めます。
毎回すべての資料やプロジェクト全体を読み直す必要はありません。
検証は変更した契約を確かめる範囲で選び、必須チェックを終えた後の追加・再実行は、
修正・失敗・未解決の懸念がある場合に行います。

- テストの境界と実データ受入: [testing.md](testing.md)
- レイアウトと画面順序: [web-design/README.md](web-design/README.md)
- Static Web の本番公開と受入: [static_production.md](static_production.md)
- ローカル / Legacy runtime: [development_worktrees.md](development_worktrees.md)

ローカル検証、hosted CI、実機受入、本番公開の結果は、それぞれの証拠に基づいて報告します。
通常の機能追加・修正・文書変更は PR の `Release: not-required` と理由を記し、
VERSION / CHANGELOG を更新しません。次回リリース向けのメモは PR 本文に記録し、
明示的なリリース準備 PR でまとめて確定します。ブラウザ検証の適用条件と Compose・PR の完了条件は
AGENTS.md に従います。

Codex の設定仕様は変更されるため、設定を変更するときは
[公式の設定ガイド](https://learn.chatgpt.com/docs/config-file/config-basic) と
[設定リファレンス](https://learn.chatgpt.com/docs/config-file/config-reference) で確認します。
