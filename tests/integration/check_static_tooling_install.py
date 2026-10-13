"""Install the real workflow command in a clean venv, without generating MSM data."""

import json
import os
import shlex
import subprocess
import tempfile
import venv
from pathlib import Path
from urllib.parse import unquote, urlsplit

APP_SHA = "a4de2a226dfa205b8be42fb62a710a1b06271ed8"


def check_install(workspace: Path) -> None:
    """Exercise the Actions cwd with separate current tooling and fixed old app checkouts."""
    tooling = workspace / "tooling"
    app = workspace / "app"
    app_head = subprocess.check_output(
        ["git", "-C", str(app), "rev-parse", "HEAD"], text=True
    ).strip()
    assert app_head == APP_SHA
    assert (app / "VERSION").read_text().strip() == "1.20.1"
    workflow = (tooling / ".github/workflows/static-production.yml").read_text()
    step = workflow.split(
        "      - name: Install trusted producer dependencies\n", 1
    )[1].split("      - name:", 1)[0]
    commands = [line.strip() for line in step.splitlines()
                if line.strip().startswith("python -m pip install ")]
    assert len(commands) == 1
    command = shlex.split(commands[0])
    # Reject the known accidental PyPI name before downloading any unrelated package.
    assert "tooling[test]" not in command
    with tempfile.TemporaryDirectory(prefix="static-tooling-install-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        env = {**os.environ, "PATH": f"{environment / 'bin'}:{os.environ['PATH']}"}
        env.pop("PYTHONPATH", None)
        python = str(environment / "bin/python")
        subprocess.run([python, "-c", (
            "import importlib.util; "
            "assert importlib.util.find_spec('autonavlog') is None; "
            "assert importlib.util.find_spec('pydantic') is None"
        )], cwd=workspace, env=env, check=True)
        subprocess.run(command, cwd=workspace, env=env, check=True)
        # Import the installed package without PYTHONPATH, and verify pip's PEP 610 origin.
        result = subprocess.check_output([python, "-c", (
            "import json, pydantic, autonavlog; from importlib.metadata import distribution; "
            "d = distribution('autonavlog'); "
            "print(json.dumps({'version': d.version, 'origin': "
            "json.loads(d.read_text('direct_url.json'))['url']}))"
        )], cwd=workspace, env=env, text=True)
        installed = json.loads(result)
        assert installed["version"] == (tooling / "VERSION").read_text().strip()
        origin = urlsplit(installed["origin"])
        assert origin.scheme == "file" and not origin.netloc
        assert Path(unquote(origin.path)).resolve() == tooling.resolve()
        # Match the producer's actual source import path; --help never calls produce().
        env["PYTHONPATH"] = str(tooling / "src")
        for script in ("scripts/prepare_msm_feed.py", "scripts/static_ops/validate_feed.py",
                       "scripts/static_ops/preflight_feed.py"):
            subprocess.run([python, str(tooling / script), "--help"],
                           cwd=workspace, env=env, check=True)
        print(json.dumps({"status": "OK", "tooling_version": installed["version"],
                          "app_sha": app_head, "app_version": "1.20.1",
                          "local_checkout_origin": True, "producer_imports": "OK"}))


if __name__ == "__main__":
    check_install(Path.cwd())
