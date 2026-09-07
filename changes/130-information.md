<!-- section: 追加 -->
<!-- issue: 130 -->
<!-- user-visible: true -->

## Developer
- Issue #130で、InformationにRELEASE_NOTESとKNOWN_ISSUESを表示し、可視内容のhashで既読を管理します。Known Issueの追加・本文更新は通常更新と区別して通知します。
- Headerを明示的な最大2行レイアウトにし、Information・保存・新規を狭い幅でアイコン表示にします。
- change fragment、厳格な情報ソース検証、Docker release-toolsとprepare_release.pyによる一括リリース準備を導入します。

## User
- 画面上部のInformationアイコンから、お知らせと過去の更新内容を確認できるようになりました。
- 新しいお知らせがあるとアイコンに印が付きます。利用前に知っておきたい不具合の追加や内容変更は、黄色の印でお知らせします。
- 画面が狭いときも、プロジェクト名と保存・読込などの操作を2行以内にまとめて表示します。
