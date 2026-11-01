"""Reject configurations that could replace data or change production ownership."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("legacy_compose", Path(__file__).resolve().parents[2] / "scripts/check_legacy_compose.py")
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture
def config():
    env = {key: "synthetic-private-value" for key in checker.AUTH_KEYS}
    env.update(AUTONAVLOG_NAVMATE_URL="https://navmate.yuto24.com/", AUTONAVLOG_TRUSTED_LOCAL_IDENTITY="", AUTONAVLOG_SESSION_COOKIE_SECURE="true")
    return {"name": "autonavlog", "services": {"autonavlog": {
        "image": "autonavlog:legacy", "environment": env,
        "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8123", "protocol": "tcp"}],
        "volumes": [{"type": "volume", "source": "autonavlog-data", "target": "/var/lib/autonavlog"}],
    }}, "volumes": {"autonavlog-data": {"name": checker.VOLUME, "external": True}}}


def container(config):
    return {"Config": {"Env": [f"{k}={v}" for k, v in config["services"]["autonavlog"]["environment"].items()],
            "Labels": {"com.docker.compose.project": "autonavlog", "com.docker.compose.service": "autonavlog"}},
            "State": {"Running": True}, "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8123"}]}},
            "Mounts": [{"Type": "volume", "Name": checker.VOLUME, "Destination": "/var/lib/autonavlog", "RW": True}]}


def test_canonical_config_preserves_live_settings(config):
    live = container(config)
    config["services"]["autonavlog"]["environment"]["AUTONAVLOG_NAVMATE_URL"] = "https://navmate.yuto24.com"
    checker.check_running(checker.check_config(config), live)


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(name="wrong-project"),
    lambda c: c["services"]["autonavlog"].update(image="autonavlog:local"),
    lambda c: c["services"]["autonavlog"]["ports"][0].update(host_ip="0.0.0.0"),
    lambda c: c["volumes"]["autonavlog-data"].update(external=False),
    lambda c: c["volumes"]["autonavlog-data"].update(name="empty-replacement"),
    lambda c: c["services"]["autonavlog"]["volumes"][0].update(type="bind"),
    lambda c: c["services"]["autonavlog"]["environment"].update(AUTONAVLOG_TRUSTED_LOCAL_IDENTITY="shared-user"),
    lambda c: c["services"]["autonavlog"]["environment"].update(AUTONAVLOG_SESSION_COOKIE_SECURE="false"),
    lambda c: c["services"]["autonavlog"]["environment"].update(AUTONAVLOG_FIREBASE_API_KEY=""),
    lambda c: c["services"]["autonavlog"]["environment"].update(AUTONAVLOG_NAVMATE_URL="https://wrong.example/private"),
])
def test_unsafe_target_is_rejected_without_values(config, mutation):
    mutation(config)
    with pytest.raises(checker.InvalidConfiguration) as error:
        checker.check_config(config)
    assert "synthetic-private-value" not in str(error.value)
    assert "wrong.example" not in str(error.value)


@pytest.mark.parametrize("key", checker.PRESERVED_KEYS)
def test_each_changed_live_setting_is_rejected(config, key):
    live = container(config)
    live["Config"]["Env"] = [s for s in live["Config"]["Env"] if not s.startswith(key + "=")] + [key + "=different-private-value"]
    with pytest.raises(checker.InvalidConfiguration) as error:
        checker.check_running(checker.check_config(config), live)
    assert "different-private-value" not in str(error.value)


@pytest.mark.parametrize("mutation", [
    lambda c: c["Mounts"][0].update(Name="other-volume"),
    lambda c: c["HostConfig"]["PortBindings"]["8000/tcp"][0].update(HostIp="0.0.0.0"),
    lambda c: c["State"].update(Running=False),
    lambda c: c["Config"]["Labels"].update({"com.docker.compose.project": "other"}),
])
def test_live_baseline_mismatch_stops(config, mutation):
    live = container(config)
    mutation(live)
    with pytest.raises(checker.InvalidConfiguration):
        checker.check_running(checker.check_config(config), live)


def test_command_failure_does_not_echo_resolved_secrets(monkeypatch):
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "secret-output", "secret-error"))
    with pytest.raises(checker.InvalidConfiguration) as error:
        checker.read_command(["docker", "compose", "config"])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status", [" M src/autonavlog/web/app.py", "?? src/autonavlog/unreviewed.py", "?? web/public/unreviewed.js"])
def test_unreviewed_build_inputs_fail_approved_source_check(monkeypatch, status):
    calls = []

    def read(args):
        calls.append(args)
        return "release/1.x" if "branch" in args else "a" * 40 if "rev-parse" in args else status

    monkeypatch.setattr(checker, "read_command", read)
    with pytest.raises(checker.InvalidConfiguration):
        checker.check_source("a" * 40)
    assert calls[-1] == ["git", "status", "--porcelain", "--untracked-files=all"]
