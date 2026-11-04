"""Local static delivery: serialized main/feed builds and an atomic publication pointer.

This is a container entrypoint, not a deployment CLI. Only generated volumes are
writable; no Project store, host checkout, Docker socket or provider token is used.
"""

from __future__ import annotations

import argparse
import fcntl
import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

PUBLIC_KEYS = (
    "VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID",
    "VITE_FIREBASE_APP_ID", "VITE_TAF_PROXY_URL", "VITE_LEGACY_MIGRATION_URL",
)
SHA = re.compile(r"[a-f0-9]{40}")
RELEASE = re.compile(r"r-[a-f0-9]{32}")


def utc() -> str:
    return datetime.now(UTC).isoformat()


def event(name: str, **values) -> None:
    print(json.dumps({"at": utc(), "event": name, **values}), flush=True)


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def pointer(root: Path, name: str, target: Path) -> None:
    temporary = root / (".pointer-" + uuid.uuid4().hex)
    temporary.symlink_to(target.relative_to(root))
    temporary.replace(root / name)


@dataclass(frozen=True)
class Settings:
    source: Path
    state: Path
    published: Path
    tooling: Path = Path("/opt/runtime")
    port: int = 8123
    retry: int = 900
    weather_interval: int = 3600
    job_timeout: int = 2700

    @classmethod
    def environment(cls) -> Settings:
        """Read container paths and port; build credentials are not part of these settings."""
        return cls(Path(os.getenv("LOCAL_SOURCE", "/git/repo/current")),
                   Path(os.getenv("LOCAL_STATE", "/state")),
                   Path(os.getenv("LOCAL_PUBLISHED", "/published")),
                   Path(os.getenv("LOCAL_TOOLING", "/opt/runtime")),
                   int(os.getenv("LOCAL_PORT", "8123")))

    def active(self, name: str = "current") -> Path | None:
        """Resolve only generated release directories, independently of private worker state."""
        path = self.published / name
        if not path.is_symlink():
            return None
        target = path.resolve(strict=True)
        if target.parent != self.published.resolve() / "releases" or not RELEASE.fullmatch(
            target.name
        ):
            raise ValueError("Invalid publication pointer")
        return target


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


class StaticHandler(QuietHandler):
    def __init__(self, *args, settings: Settings, **kwargs):
        """Snapshot one root per connection so a switch cannot mix bytes within a response."""
        self.settings = settings
        self.root = settings.active()
        super().__init__(*args, directory=str(self.root or settings.published / "absent"),
                         **kwargs)

    def do_GET(self):
        """Distinguish first-start preparation from a missing file in a published artifact."""
        if self.root is None:
            self.send_error(503)
        else:
            super().do_GET()

    def do_HEAD(self):
        """Apply the same first-start readiness rule to header-only requests."""
        if self.root is None:
            self.send_error(503)
        else:
            super().do_HEAD()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

@contextmanager
def local_server(directory: Path):
    """Loopback only, ephemeral port; candidate tests never use the real browser origin."""
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class Superseded(Exception):
    """A completed main build no longer matches the desired source."""


class Stopped(Exception):
    """Container shutdown, not an update failure."""


class Updater:
    def __init__(self, settings: Settings):
        """Resume persisted main backoff while scheduling a fresh weather check on startup."""
        self.s = settings
        for path in (settings.state, settings.published, settings.published / "releases"):
            path.mkdir(parents=True, exist_ok=True)
        for name in ("work", "sources", "logs", "cache"):
            (settings.state / name).mkdir(exist_ok=True, mode=0o700)
        self.status = load(settings.state / "status.json") if (
            settings.state / "status.json").exists() else {}
        self.stop = threading.Event()
        self.deadline = 0.0
        self.env = {}
        self.log = None
        self.mutex = threading.Lock()
        # Restart always rechecks the feed, without retrying a known failed main too soon.
        self.next_weather = 0.0

    def save_status(self, **values) -> None:
        """Serialize heartbeat and job writes to private, atomically replaced status."""
        with self.mutex:
            self.status.update(values)
            self.status["heartbeat"] = time.time()
            atomic_json(self.s.state / "status.json", self.status)

    def desired(self) -> tuple[str, Path]:
        """Pin Git-sync's current worktree and verify its commit inside the source volume."""
        source = self.s.source.resolve(strict=True)
        if not source.is_relative_to(self.s.source.parent.resolve()):
            raise ValueError("Git synchronization escaped its volume")
        sha = subprocess.check_output(
            ["git", "-c", f"safe.directory={source}", "-C", str(source), "rev-parse", "HEAD"],
            text=True, timeout=10).strip()
        if not SHA.fullmatch(sha):
            raise ValueError("Invalid synchronized commit")
        return sha, source

    def run(self, args: list[str], cwd: Path, *, capture: bool = False) -> str:
        """Bound the entire job and kill descendants on timeout or container shutdown."""
        if self.stop.is_set():
            raise Stopped()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Update exceeded 45 minutes")
        with subprocess.Popen(args, cwd=cwd, env=self.env, start_new_session=True,
                              stdout=subprocess.PIPE if capture else self.log,
                              stderr=self.log) as process:
            while True:
                try:
                    output, _ = process.communicate(timeout=min(2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    remaining = self.deadline - time.monotonic()
                    if self.stop.is_set() or remaining <= 0:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                        if self.stop.is_set():
                            raise Stopped() from None
                        raise TimeoutError("Update exceeded 45 minutes") from None
            if process.returncode:
                raise RuntimeError(f"Step failed ({process.returncode}): {' '.join(args[:5])}")
        return output.decode() if capture else ""

    def clone(self, source: Path, destination: Path, sha: str) -> None:
        """Copy objects independently, trusting foreign ownership only in the job's HOME."""
        # Local clone's upload-pack does not inherit command-line safe.directory.
        # Its protected config lives ONLY in this job's disposable HOME, never on the host.
        home = Path(self.env["HOME"])
        if not home.is_relative_to(self.s.state / "work"):
            raise ValueError("Git configuration must stay in the isolated job HOME")
        git_dir = self.run(["git", "-c", f"safe.directory={source}", "-C", str(source),
                            "rev-parse", "--absolute-git-dir"], self.s.state,
                           capture=True).strip()
        for safe in (str(source), git_dir):
            self.run(["git", "config", "--global", "--add", "safe.directory", safe], self.s.state)
        self.run(["git", "-c", f"safe.directory={source}", "-c",
                  f"safe.directory={git_dir}",
                  "clone", "--no-hardlinks", "--no-checkout", str(source),
                  str(destination)], self.s.state)
        self.run(["git", "checkout", "--detach", sha], destination)

    def configuration(self) -> dict:
        """Pass only public Web identifiers, excluding inherited provider or Git tokens."""
        return {key: os.environ[key] for key in PUBLIC_KEYS if os.environ.get(key)}

    def build(self, kind: str, sha: str, source: Path, job: Path) -> Path | None:
        """Reuse repository tools; always build app+feed together from one fixed SHA."""
        app, feed = job / "app", job / "feed"
        home = job / "home"
        home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(home),
                    "PLAYWRIGHT_BROWSERS_PATH": os.getenv("PLAYWRIGHT_BROWSERS_PATH",
                                                          "/opt/browsers"),
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "PIP_CACHE_DIR": str(self.s.state / "cache/pip"),
                    "npm_config_cache": str(self.s.state / "cache/npm"),
                    **self.configuration()}
        current = self.s.active()
        expected_run = None
        if kind == "weather" and current:
            raw = self.run([sys.executable, str(self.s.tooling /
                            "scripts/static_ops/preflight_feed.py"), "--catalog",
                            str(current / "weather/msm/catalog.json"),
                            "--github-output", str(job / "preflight-output")],
                           self.s.tooling, capture=True)
            preflight = json.loads(raw)
            if not preflight["proceed"]:
                event("weather_unchanged", sha=sha)
                return None
            expected_run = preflight["upstream_run"]
        self.clone(source, app, sha)
        self.run([sys.executable, "-m", "venv", "--system-site-packages", str(job / "venv")],
                 self.s.state)
        python = str(job / "venv/bin/python")
        self.env["PATH"] = str(job / "venv/bin") + os.pathsep + self.env["PATH"]
        self.env["AUTONAVLOG_REFERENCE_PYTHON"] = python
        self.env["AUTONAVLOG_MSM_FEED"] = str(feed)
        self.run([python, "-m", "pip", "install", "setuptools>=69", "wheel",
                  "vendor/jma_gpv_weather-0.6.0-py3-none-any.whl", ".[test]"], app)
        self.run(["npm", "--prefix", "web", "ci"], app)
        if current:
            shutil.copytree(current / "weather/msm", feed)
        else:
            feed.mkdir()
        producer = app / "scripts/prepare_msm_feed.py"
        if kind == "weather":
            report = self.run([python, str(self.s.tooling / "scripts/static_ops/refresh_feed.py"),
                               "--producer", str(producer), "--output", str(feed), "--cache",
                               str(self.s.state / "cache/msm"), "--expected-run", expected_run],
                              app, capture=True)
        else:
            report = self.run([python, str(producer), "--output", str(feed), "--cache",
                               str(self.s.state / "cache/msm"), "--hours", "25", "--full-run"],
                              app, capture=True)
        report_file = job / "feed-report.json"
        report_file.write_text(report)
        validator = [sys.executable, str(self.s.tooling / "scripts/static_ops/validate_feed.py"),
                     str(feed), str(report_file)]
        self.run(validator, self.s.tooling)
        for args in ([python, "scripts/validate_release.py"],
                     [python, "scripts/validate_performance_data.py", "data/performance"],
                     [python, "scripts/validate_runtime_data.py", "data"],
                     [python, "scripts/export_schemas.py", "--check"],
                     [python, "-m", "pytest", "tests/unit/test_version.py"],
                     ["npm", "--prefix", "web", "run", "typecheck"],
                     ["npm", "--prefix", "web", "run", "build:static"],
                     ["npm", "--prefix", "web", "run", "test:static-artifact"]):
            self.run(args, app)
        dist = app / "web/dist-static"
        self.run(["npm", "--prefix", "web", "exec", "--", "playwright", "install", "chromium"],
                 app)
        with local_server(dist) as origin:
            self.env["AUTONAVLOG_LOCAL_URL"] = origin
            self.run(["npm", "--prefix", "web", "run", "test:static"], app)
        # Browser checks can cross a forecast-hour boundary. Revalidate at publication time.
        self.run(validator, self.s.tooling)
        self.run(["npm", "--prefix", str(self.s.tooling / "web"), "run", "check:static",
                  "--", str(dist)], self.s.tooling)
        release = load(dist / "release.json")
        if (release["commit"] != sha or release["dirty"] is not False
                or release["configuration"] != self.configuration()):
            raise ValueError("Candidate identity/configuration mismatch")
        if kind == "main" and self.desired()[0] != sha:
            raise Superseded()
        archive = self.s.state / "sources" / sha
        if not archive.exists():
            temporary = job / "source-archive"
            self.clone(app, temporary, sha)
            temporary.replace(archive)
        return dist

    def promote(self, dist: Path, sha: str, kind: str) -> None:
        """Recheck candidate identity before switching the complete publication pointer."""
        old = self.s.active()
        if kind == "main" and self.desired()[0] != sha:
            raise Superseded()
        if kind == "weather" and (old is None or load(old / "release.json")["commit"] != sha):
            raise Superseded()
        target = self.s.published / "releases" / ("r-" + uuid.uuid4().hex)
        shutil.copytree(dist, target)
        for path in [target, *target.rglob("*")]:
            if path.is_symlink():
                raise ValueError("Symlink in publication")
            path.chmod(0o755 if path.is_dir() else 0o644)
        if kind == "main" and self.desired()[0] != sha:
            shutil.rmtree(target)
            raise Superseded()
        if old:
            pointer(self.s.published, "previous", old)
        pointer(self.s.published, "current", target)
        catalog = load(target / "weather/msm/catalog.json")
        self.save_status(active_sha=sha, last_success=utc(), expires_at=catalog["expires_at"],
                         last_error=None)
        event("published", kind=kind, sha=sha, release=target.name,
              expires_at=catalog["expires_at"], run=max(a["run"] for a in catalog["assets"]))
        self.collect()

    def collect(self) -> None:
        """Bound generated histories and leave unrelated operator files untouched."""
        keep = {p for name in ("current", "previous") if (p := self.s.active(name))}
        for path in (self.s.published / "releases").iterdir():
            if RELEASE.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                if path not in keep:
                    shutil.rmtree(path)
        commits = {load(path / "release.json")["commit"] for path in keep}
        for path in (self.s.state / "sources").iterdir():
            if SHA.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                if path.name not in commits:
                    shutil.rmtree(path)
        logs = sorted((p for p in (self.s.state / "logs").iterdir()
                       if re.fullmatch(r"job-[a-f0-9]{32}\.log", p.name)
                       and p.is_file() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
        for path in logs[:-10]:
            path.unlink()

    def attempt(self, kind: str, sha: str, source: Path) -> bool:
        """Isolate failures and enforce retention after success, failure and supersession."""
        name = "job-" + uuid.uuid4().hex
        job = self.s.state / "work" / name
        job.mkdir(mode=0o700)
        self.deadline = time.monotonic() + self.s.job_timeout
        event("update_started", kind=kind, sha=sha)
        self.save_status(running={"kind": kind, "sha": sha, "started_at": utc()})
        try:
            with (self.s.state / "logs" / (name + ".log")).open("wb") as stream:
                self.log = stream
                dist = self.build(kind, sha, source, job)
                if dist is not None:
                    self.promote(dist, sha, kind)
            return True
        except Superseded:
            event("update_superseded", kind=kind, sha=sha)
            return True
        except Stopped:
            raise
        except Exception as error:
            self.save_status(last_error={"kind": kind, "sha": sha, "at": utc(),
                                         "message": str(error), "log": name + ".log"})
            event("update_failed", kind=kind, sha=sha, error=str(error), log=name + ".log")
            return False
        finally:
            self.log = None
            shutil.rmtree(job)
            self.collect()
            self.save_status(running=None)

    def tick(self, now: float) -> None:
        """Check active-SHA weather first; a rejected main has an independent retry clock."""
        active = self.s.active()
        if active and now >= self.next_weather:
            sha = load(active / "release.json")["commit"]
            success = self.attempt("weather", sha, self.s.state / "sources" / sha)
            self.next_weather = time.time() + (self.s.weather_interval if success else self.s.retry)
        try:
            desired, source = self.desired()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.save_status(source_error=str(error))
            return
        self.save_status(desired_sha=desired, source_error=None)
        active = self.s.active()
        if active and load(active / "release.json")["commit"] == desired:
            return
        if (self.status.get("failed_main_sha") == desired
                and now < self.status.get("retry_main_at", 0)):
            return
        success = self.attempt("main", desired, source)
        self.save_status(failed_main_sha=None if success else desired,
                         retry_main_at=0 if success else time.time() + self.s.retry)

    def serve(self) -> None:
        """Hold one volume-wide writer lock and keep heartbeat alive during child builds."""
        with (self.s.state / "updater.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for abandoned in (self.s.state / "work").iterdir():
                if (re.fullmatch(r"job-[a-f0-9]{32}", abandoned.name)
                        and abandoned.is_dir() and not abandoned.is_symlink()):
                    shutil.rmtree(abandoned)

            def heartbeat():
                while not self.stop.wait(15):
                    self.save_status()

            self.save_status(running=None)
            threading.Thread(target=heartbeat, daemon=True).start()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: self.stop.set())
            try:
                while not self.stop.is_set():
                    try:
                        self.tick(time.time())
                    except Stopped:
                        break
                    except Exception as error:
                        self.save_status(last_error={"at": utc(), "message": str(error)})
                        event("updater_error", error=str(error))
                    self.stop.wait(30)
            finally:
                self.stop.set()


def healthy(settings: Settings, mode: str) -> bool:
    """Keep Web liveness independent from forecast expiry, but surface stale updater data."""
    try:
        if mode == "health-web":
            with urlopen(f"http://127.0.0.1:{settings.port}/release.json", timeout=3) as response:
                value = json.load(response)
            return value["dirty"] is False and bool(SHA.fullmatch(value["commit"]))
        status = load(settings.state / "status.json")
        active = settings.active()
        if active is None or time.time() - status["heartbeat"] > 90:
            return False
        catalog = load(active / "weather/msm/catalog.json")
        return datetime.fromisoformat(catalog["expires_at"].replace("Z", "+00:00")) > (
            datetime.now(UTC))
    except (OSError, ValueError, KeyError):
        return False


def main() -> None:
    """Select a container role or internal health probe without a host deployment CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("web", "updater", "health-web", "health-updater"))
    args = parser.parse_args()
    settings = Settings.environment()
    if args.mode.startswith("health-"):
        raise SystemExit(0 if healthy(settings, args.mode) else 1)
    if args.mode == "web":
        ThreadingHTTPServer(("0.0.0.0", settings.port), functools.partial(
            StaticHandler, settings=settings)).serve_forever()
    else:
        Updater(settings).serve()


if __name__ == "__main__":
    main()
