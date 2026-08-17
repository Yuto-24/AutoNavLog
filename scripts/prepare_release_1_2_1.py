from __future__ import annotations

import json
from pathlib import Path

VERSION = "1.2.1"
DATE = "2026-08-17"


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(
            f"unexpected occurrence count in {path}: {old!r} -> {text.count(old)}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "pyproject.toml",
    'version = "1.2.0"',
    f'version = "{VERSION}"',
)

package_path = Path("web/package.json")
package = json.loads(package_path.read_text(encoding="utf-8"))
package["version"] = VERSION
package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

lock_path = Path("web/package-lock.json")
lock = json.loads(lock_path.read_text(encoding="utf-8"))
lock["version"] = VERSION
lock["packages"][""]["version"] = VERSION
lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

replace_once(
    "README.md",
    "- 現在のバージョン: `1.2.0`",
    f"- 現在のバージョン: `{VERSION}`",
)

release_notes = f"""## {VERSION} - {DATE}

### 修正

- 実行時の版番号をインストール済みPackage Metadataから取得するようにし、`/healthz` とWeb画面左上の版表示が `pyproject.toml` と一致するようにしました。
- 飛行計画入力で `FUEL gal` と `TGL`、`RUN UP あり` と `A/C ON` をそれぞれ横並びにし、チェックボックスとラベルを同一行へ整理しました。
- NAV LOGの親行 `ZONE / CUM` DIST・ETEを、表示済みの子区間を0.5 NM・0.5分単位で合算する表示へ修正しました。計算・監査用の未丸め値は変更していません。

"""
replace_once(
    "CHANGELOG.md",
    "## Unreleased\n\n",
    "## Unreleased\n\n" + release_notes,
)
