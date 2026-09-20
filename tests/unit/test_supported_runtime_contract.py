import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_python_runtime_contract_is_312() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"]["requires-python"] == ">=3.12,<3.13"
    assert config["tool"]["ruff"]["target-version"] == "py312"
    assert config["tool"]["mypy"]["python_version"] == "3.12"
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm AS python-base" in dockerfile
    assert "FROM python-base AS test" in dockerfile
    assert "FROM python-base AS runtime" in dockerfile
    assert dockerfile.index("FROM python-base AS test") < dockerfile.index(
        "FROM python-base AS runtime"
    )


def test_backend_ci_runs_only_python_312() -> None:
    workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "docker build --target test --tag autonavlog:test ." in workflow
    assert "docker run --rm autonavlog:test" in workflow
    assert "docker build --target runtime --tag autonavlog:runtime ." in workflow
    assert "Read expected version from the production image source" in workflow
    assert "set -eu" in workflow
    assert '"status": "ok"' in workflow
    assert '"version": os.environ["EXPECTED_VERSION"]' in workflow
    assert "matrix:" not in workflow
    assert '"3.10"' not in workflow
    assert '"3.11"' not in workflow


def test_docker_test_stage_contains_ci_contract_inputs() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY tests ./tests" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert (
        "COPY VERSION CHANGELOG.md KNOWN_ISSUES.md Dockerfile .dockerignore ./"
        in dockerfile
    )
    assert "COPY web/package.json web/package-lock.json ./web/" in dockerfile
    assert "COPY .github/workflows/test.yml ./.github/workflows/test.yml" in dockerfile
    assert "chmod 755 scripts/run_ci_checks.sh" in dockerfile
    assert "rm -rf build dist src/autonavlog.egg-info" in dockerfile
    assert 'CMD ["/opt/autonavlog/scripts/run_ci_checks.sh"]' in dockerfile
    assert ".github/*" in dockerignore
    assert "!.github/workflows/" in dockerignore
    assert ".github/workflows/*" in dockerignore
    assert "!.github/workflows/test.yml" in dockerignore


def test_readme_distinguishes_static_production_from_legacy_runtime() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Legacy serverのサポート対象runtimeは Docker / Docker Compose" in readme
    assert "docs/static_production.md" in readme
    assert "FastAPI / Dockerは本番配信に不要" in readme
    assert "host上での直接実行" in readme
    assert "サポート対象ではありません" in readme
