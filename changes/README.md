# Change fragments とリリース準備

Issue対応や次のReleaseへ含める変更は、Versionを直接変更せず、このディレクトリにfragmentを追加します。
1ファイルにつき `追加` / `改善` / `変更` / `修正` のいずれか1sectionです。
Issueがある変更では `issue` metadataが必須です。Issueのない変更では省略できます。
ファイル名は `136-descent-cas.md` のようにIssue番号＋slugを推奨します。
Issueがなければ `information-copy.md` など安定したslugを使います。
Release時はファイル名の昇順で全fragmentを取り込みます。READMEは対象外です。

```md
<!-- section: 修正 -->
<!-- issue: 136 -->
<!-- user-visible: true -->

## Developer
- Issue #136で、降下CASの継承条件を修正しました。

## User
- 一部の経路で、降下中の速度が正しく計算されない問題を修正しました。
```

metadataは本文より前に置き、未知・重複metadataは禁止です。sectionは必須、
user-visibleは省略時trueです。Developer / Userは順序固定で、本文は1項目1行の箇条書きのみです。
利用者への影響がない場合は `user-visible: false` として `## User` を省略してください。
Userは専門用語やIT用語を避け、一般的なサービスのお知らせとして分かる文章にします。
Issue番号と実装詳細はDeveloperだけに書いてください。
複数sectionにまたがる変更はfragmentを分けます。

純粋な文書修正、テストだけの整理、コメント・文言だけの変更など、Release履歴に残す意味がない変更は不要です。
`KNOWN_ISSUES.md` だけの更新はVersion更新もfragmentも不要です。
CIはPRのbaseとの差分から明確なruntime/build変更のfragment漏れを検出します。
文言だけなど自動判定が難しい境界はレビューで確認します。pushでは形式だけを検証します。

## Releaseを確定する

```bash
docker build --target release-tools -t autonavlog:release-tools .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  autonavlog:release-tools python scripts/prepare_release.py 1.10.0 --check
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  autonavlog:release-tools python scripts/prepare_release.py 1.10.0
```

指定するのは現在より新しい `X.Y.Z` のみです。`v` prefix、同一版、過去版、fragment 0件は拒否します。
すべての入力と生成結果を検証してから書き込み、全fragmentを削除します。
`--check` は同じ検証・生成計画を実行し、ファイルを書き換えません。
未コミットの変更があっても実行でき、commit / tag / pushは行いません。
JSTの実行日時を分まで両履歴に記録します。後からbuildしても時刻は変わりません。
通常の書込エラーは元の内容へ戻します。実行中の強制終了や別プロセスからの同時編集は避けてください。

開発者向け本文はCHANGELOG、利用者向け本文はRELEASE_NOTESへ集約します。
Userが全件省略されたReleaseには、使い方に変更がない旨を「改善」へ自動追加します。
CHANGELOGの「配布」は自動生成し、GitHub Releaseも引き続きCHANGELOGから作成します。
Package・README・両履歴のVersionを同時に更新します。
`src/autonavlog/version.py` と `jma-msm-wind` のVersionは変更しません。

専用Release PRは不要です。複数IssueをひとつのPRで閉じる方法と、
IssueごとのPRでfragmentをmainに蓄積し、後のPRでまとめてReleaseする方法の両方を使えます。
Releaseを確定したPRにはfragmentを残せません。Unreleasedセクションは使用しません。

## Informationの情報源

- `RELEASE_NOTES.md`: 全版を新しい順に保持。見出しは `## X.Y.Z - YYYY-MM-DD HH:MM JST`。
  過去の時刻不明版だけ日付のみを保持します。sectionは追加→改善→変更→修正の順で、存在するものだけ。
  版直下の自由文、配布、Issue番号、箇条書き以外の本文は禁止です。
- `KNOWN_ISSUES.md`: 現在未解決の、利用者が事前に知る必要がある重大な不具合だけを記載。
  0件ならタイトルだけです。GitHub Issuesの一覧ではありません。

```md
<!-- id: saved-plan-open-failed -->
<!-- github-issue: 123 -->

## 保存した計画を開けない場合があります

保存した計画を選んでも、内容が表示されないことがあります。

### 影響する条件
- 以前保存した計画を開く場合

### 回避方法
- ページを開き直してから、もう一度選んでください。
```

上は形式例で、現在の不具合ではありません。
`id`、タイトル、説明本文は必須です。公開済みidは変更しません。
`github-issue`、影響する条件、回避方法は任意です。GitHub APIへの問い合わせはしません。
metadataはid→github-issueの順。任意sectionの本文は箇条書きです。
コロンを含まない1行の説明用HTMLコメントは許可し、画面や通知判定には含めません。
未知metadata・見出し、重複ID、構造不正はbuildで失敗します。掲載順をそのまま画面に使います。
掲載日時・severityは持たず、解消後は項目を完全に削除して履歴だけに修正内容を残します。

Informationの通知は表示内容全体で判定します。管理用ID・Issue番号だけの変更は通知しません。
既知の不具合の追加や本文変更は黄色、削除・並び替え・通常Releaseは通常色のdotです。
開くと既読になります。ブラウザーへの保存が使えない場合も画面の操作は継続できます。
