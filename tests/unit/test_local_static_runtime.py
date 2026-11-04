"""Exercise publication/scheduling against real Git commits and HTTP responses."""

import fcntl
import functools
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from scripts.static_ops.local_runtime import (
    Settings,
    StaticHandler,
    Updater,
    healthy,
    load,
)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo):
    (repo / "input.txt").write_text(str(time.time_ns()))
    git(repo, "add", "input.txt")
    git(repo, "commit", "-qm", "candidate")
    return git(repo, "rev-parse", "HEAD")


def artifact(root, sha, *, expired=False):
    root.mkdir()
    (root / "index.html").write_text(sha * 1000)
    (root / "release.json").write_text(json.dumps({"commit": sha, "dirty": False}))
    feed = root / "weather/msm"
    feed.mkdir(parents=True)
    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 6)
    (feed / "catalog.json").write_text(json.dumps({"expires_at": expiry.isoformat(),
                                                   "assets": [{"run": "20261103000000"}]}))
    return root


class ControlledUpdater(Updater):
    """Stub expensive build steps only; retain real scheduler, Git and pointer logic."""

    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []
        self.fail = set()
        self.during_build = None
        self.weather_noop = False

    def build(self, kind, sha, source, job):
        self.calls.append((kind, sha))
        if (kind, sha) in self.fail:
            raise RuntimeError("Candidate rejected")
        if self.during_build:
            callback, self.during_build = self.during_build, None
            callback()
        if kind == "weather" and self.weather_noop:
            return None
        (self.s.state / "sources" / sha).mkdir(exist_ok=True)
        return artifact(job / "artifact", sha)


@pytest.fixture
def runtime(tmp_path):
    repo = tmp_path / "git/current"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    commit(repo)
    settings = Settings(repo, tmp_path / "state", tmp_path / "published")
    return ControlledUpdater(settings)


def test_main_change_noop_and_restart(runtime):
    now = time.time()
    first = runtime.desired()[0]
    runtime.tick(now)
    assert load(runtime.s.active() / "release.json")["commit"] == first
    runtime.weather_noop = True
    runtime.tick(now)
    runtime.tick(now)
    assert runtime.calls == [("main", first), ("weather", first)]
    second = commit(runtime.s.source)
    runtime.tick(now)
    assert load(runtime.s.active() / "release.json")["commit"] == second
    assert load(runtime.s.active("previous") / "release.json")["commit"] == first
    restarted = ControlledUpdater(runtime.s)
    restarted.weather_noop = True
    restarted.tick(now)
    assert restarted.calls == [("weather", second)]
    assert load(restarted.s.active() / "release.json")["commit"] == second


def test_failed_main_does_not_block_active_weather_or_change_pointer(runtime):
    now = time.time()
    runtime.tick(now)
    active = runtime.s.active()
    old = load(active / "release.json")["commit"]
    new = commit(runtime.s.source)
    runtime.weather_noop = True
    runtime.fail.add(("main", new))
    runtime.tick(now)
    assert runtime.s.active() == active
    assert runtime.status["failed_main_sha"] == new
    runtime.weather_noop = False
    runtime.next_weather = 0
    runtime.tick(now + 1)
    assert runtime.calls[-1] == ("weather", old)
    assert runtime.s.active() != active
    assert load(runtime.s.active() / "release.json")["commit"] == old
    assert runtime.calls.count(("main", new)) == 1
    runtime.fail.clear()
    runtime.tick(runtime.status["retry_main_at"] + 1)
    assert load(runtime.s.active() / "release.json")["commit"] == new


def test_superseded_commit_never_publishes(runtime):
    now = time.time()
    runtime.tick(now)
    original = runtime.s.active()
    commit(runtime.s.source)
    runtime.next_weather = now + 3600
    runtime.during_build = lambda: commit(runtime.s.source)
    runtime.tick(now)
    assert runtime.s.active() == original
    assert runtime.status["failed_main_sha"] is None
    newest = runtime.desired()[0]
    runtime.tick(now)
    assert load(runtime.s.active() / "release.json")["commit"] == newest


def test_weather_failure_retries_without_renewing_catalog(runtime):
    runtime.tick(time.time())
    active = runtime.s.active()
    before = (active / "weather/msm/catalog.json").read_bytes()
    sha = runtime.desired()[0]
    runtime.fail.add(("weather", sha))
    runtime.tick(time.time())
    assert runtime.s.active() == active
    assert (active / "weather/msm/catalog.json").read_bytes() == before
    assert runtime.next_weather > time.time() + 890


def test_atomic_http_publication_and_no_directory_or_private_access(runtime):
    runtime.tick(time.time())
    first = runtime.desired()[0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        StaticHandler, settings=runtime.s))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    seen = []

    def reader():
        for _ in range(25):
            with urlopen(base, timeout=3) as response:
                seen.append(response.read().decode())
                assert response.headers["Cache-Control"] == "no-cache"

    second = commit(runtime.s.source)
    runtime.next_weather = time.time() + 3600
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            responses = pool.submit(reader)
            runtime.tick(time.time())
            responses.result(timeout=10)
        with urlopen(base) as response:
            seen.append(response.read().decode())
        assert set(seen) <= {first * 1000, second * 1000}
        assert second * 1000 in seen
        for path in ("/weather/", "/status.json", "/api/session"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_health_separates_web_from_expired_forecast(runtime):
    runtime.tick(time.time())
    path = runtime.s.active() / "weather/msm/catalog.json"
    catalog = load(path)
    catalog["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(catalog))
    assert not healthy(runtime.s, "health-updater")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        StaticHandler, settings=runtime.s))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert healthy(replace(runtime.s, port=server.server_port), "health-web")
        with urlopen(f"http://127.0.0.1:{server.server_port}") as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_job_timeout_terminates_process(runtime):
    runtime.deadline = time.monotonic() + 0.1
    runtime.env = {"PATH": "/usr/bin:/bin"}
    with pytest.raises(TimeoutError):
        runtime.run([sys.executable, "-c", "import time; time.sleep(30)"], runtime.s.state)


def test_gc_only_removes_generated_releases_and_sources(runtime):
    runtime.tick(time.time())
    unrelated = runtime.s.published / "releases/operator-backup"
    unrelated.mkdir()
    first = runtime.s.active()
    commit(runtime.s.source)
    runtime.next_weather = time.time() + 3600
    runtime.tick(time.time())
    commit(runtime.s.source)
    runtime.tick(time.time())
    assert not first.exists()
    assert unrelated.exists()
    assert len(list((runtime.s.state / "sources").iterdir())) == 2


def test_source_cannot_escape_git_volume(runtime, tmp_path):
    runtime.s.source.rename(tmp_path / "elsewhere")
    runtime.s.source.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="escaped"):
        runtime.desired()


def test_failed_jobs_keep_only_ten_generated_logs(runtime):
    sha = runtime.desired()[0]
    runtime.fail.add(("main", sha))
    unrelated = runtime.s.state / "logs/job-operator.log"
    unrelated.write_text("Keep operator evidence")
    for _ in range(12):
        assert not runtime.attempt("main", sha, runtime.s.source)
    assert len(list((runtime.s.state / "logs").glob("*.log"))) == 11
    assert unrelated.exists()


def test_clone_detached_worktree_is_independent(runtime, tmp_path):
    sha = runtime.desired()[0]
    worktree = runtime.s.source.parent / "detached"
    git(runtime.s.source, "worktree", "add", "--detach", str(worktree), sha)
    runtime.deadline = time.monotonic() + 10
    home = runtime.s.state / "work/clone-test/home"
    home.mkdir(parents=True)
    runtime.env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    copy = tmp_path / "independent"
    runtime.clone(worktree, copy, sha)
    git(runtime.s.source, "worktree", "remove", str(worktree))
    assert git(copy, "rev-parse", "HEAD") == sha
    assert git(copy, "status", "--porcelain") == ""


def test_second_updater_cannot_start_a_concurrent_job(runtime):
    with (runtime.s.state / "updater.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            runtime.serve()
    assert runtime.calls == []
