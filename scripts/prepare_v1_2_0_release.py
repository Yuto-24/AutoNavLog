from __future__ import annotations

import json
from pathlib import Path

VERSION = "1.2.0"
PREVIOUS = "1.1.0"


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != 1:
        raise SystemExit(f"unexpected occurrence count in {path}: {old!r} -> {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "pyproject.toml",
    f'version = "{PREVIOUS}"',
    f'version = "{VERSION}"',
)
replace_once(
    "README.md",
    f"- 現在のバージョン: `{PREVIOUS}`",
    f"- 現在のバージョン: `{VERSION}`",
)

package_path = Path("web/package.json")
package = json.loads(package_path.read_text(encoding="utf-8"))
if package.get("version") not in {PREVIOUS, VERSION}:
    raise SystemExit(f"unexpected web/package.json version: {package.get('version')}")
package["version"] = VERSION
package_path.write_text(
    json.dumps(package, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

lock_path = Path("web/package-lock.json")
lock = json.loads(lock_path.read_text(encoding="utf-8"))
root_version = lock.get("packages", {}).get("", {}).get("version")
if lock.get("version") not in {PREVIOUS, VERSION} or root_version not in {PREVIOUS, VERSION}:
    raise SystemExit("unexpected web/package-lock.json root version")
lock["version"] = VERSION
lock["packages"][""]["version"] = VERSION
lock_path.write_text(
    json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

workflow_path = Path(".github/workflows/release-to-drive.yml")
workflow = workflow_path.read_text(encoding="utf-8")
for old, new in (
    (
        f"release/wheels/autonavlog-{PREVIOUS}-py3-none-any.whl",
        f"release/wheels/autonavlog-{VERSION}-py3-none-any.whl",
    ),
    (f"--version {PREVIOUS}", f"--version {VERSION}"),
    (f'--folder-name "{PREVIOUS}"', f'--folder-name "{VERSION}"'),
):
    if old in workflow:
        if workflow.count(old) != 1:
            raise SystemExit(f"unexpected release workflow occurrence: {old!r}")
        workflow = workflow.replace(old, new, 1)
    elif new not in workflow:
        raise SystemExit(f"release workflow contains neither expected old nor new value: {old!r}")
workflow_path.write_text(workflow, encoding="utf-8")

changelog_path = Path("CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
if f"## {VERSION} - 2026-08-17" not in changelog:
    anchor = "## Unreleased\n"
    if changelog.count(anchor) != 1:
        raise SystemExit("CHANGELOG Unreleased anchor is not unique")
    entry = f"""## Unreleased

## {VERSION} - 2026-08-17

### 追加

- KML候補の始点・終点から5 NM以内の空港をFROM/TOとして自動決定し、Webでは読取専用で表示するようにしました。
- RUN UPの有無とA/C ON/OFFを計画入力へ追加しました。RUN UPありは10分・1.5 galとして燃料計画へ反映します。
- FUEL表へBOF Fuelを追加しました。BOFは `CLIMB + CRUISE + DESCENT + TGL + ADDITIONAL` で、RUN UPとRESERVEは含みません。

### 変更

- QNHの入力・取得・推定・表示・帳票出力を廃止し、標準気象モードをMSMへ統一しました。
- POH巡航表から自動採用したKTASへ、ノーズフェアリングなしの `-10 KTAS` と、A/C ON時の追加 `-2 KTAS` を適用するようにしました。手入力TAS/GPHには補正を重ねません。
- 風向入力を001〜360の整数に統一し、内部では北を0°へ正規化しつつ画面・帳票では360として表示します。
- 経路表のROLE列を削除しました。内部roleと地図上の表現は維持しています。
- 1,240 px以下の画面を「入力 → 経路・MAP → 準備状況 → NAV LOG → ガイダンス」の縦順へ統一しました。

### 互換性

- Project schemaをv2へ更新しました。旧Project/SnapshotのQNH fieldは読込時に破棄し、RUN UP・A/Cは既定ONとして移行します。
- 旧データの風向0°は北360°として読み込み、再保存時は新しい入力契約へ揃えます。

"""
    changelog = changelog.replace(anchor, entry, 1)
    changelog_path.write_text(changelog, encoding="utf-8")
