"""Exercise the real serving sequence with one exact, independently saved insertion."""

import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts.static_ops import app_release as app

OBSERVED = (
    Path(__file__).parents[1] / "fixtures/static-root-analytics/observed-root.html"
).read_bytes()
# Offline capture offsets, not a runtime extraction rule or a candidate original.
INSERTION = OBSERVED[639:1006]
ORIGINAL = (
    b'<!doctype html>\n<html><head><script src="/assets/app.js"></script>'
    b'<link href="/assets/app.css" rel="stylesheet"></head>\n'
    b'<body><div id="root"></div>\n  </body>\n</html>\n'
)
INSERTED = ORIGINAL.replace(b"</body>", INSERTION + b"</body>")


def serving(tmp_path, monkeypatch, payload, *, etag=None, status=200, mime="text/html",
            cache="no-cache", manifest_change=None, missing=404, expected=None):
    """Keep all validation real; replace only HTTP transport with bounded byte streams."""
    dist = tmp_path / "upload/dist-static"
    dist.mkdir(parents=True)
    manifest = b'{"package":"original"}\n'
    candidate = {
        "commit": "b" * 40,
        "files": {
            "index.html": expected or {"bytes": len(ORIGINAL), "sha256": app.digest(ORIGINAL)},
            "local/manifest.json": {"bytes": len(manifest), "sha256": app.digest(manifest)},
        },
    }
    (dist / "release.json").write_text(json.dumps(candidate))
    calls = []

    class Opener:
        def open(self, request, **kwargs):
            calls.append(request.full_url)
            assert request.get_method() == "GET" and request.data is None
            assert dict(request.header_items()) == {
                "Cache-control": "no-cache", "User-agent": "AutoNavLog-StaticOperations/1.0"
            }
            headers = Message()
            if request.full_url.endswith("/"):
                raw, code = payload, status
                headers["Content-Type"] = mime
                headers["Cache-Control"] = cache
                if etag is not None:
                    headers["ETag"] = etag
            elif request.full_url.endswith("/local/manifest.json"):
                raw, code = manifest, 200
                headers["Content-Type"] = "application/json"
                headers["Cache-Control"] = "no-cache"
                if manifest_change != "etag":
                    headers["ETag"] = '"manifest"'
                if manifest_change == "content":
                    raw = raw.replace(b"original", b"tampered")
                if manifest_change == "length":
                    raw += b"\n"
            else:
                assert request.full_url.endswith("/local/" + "b" * 40 + ".missing.whl")
                raw, code = b"missing", missing
            if code >= 400:
                raise HTTPError(request.full_url, code, "error", headers, io.BytesIO(raw))
            if 300 <= code < 400:
                app.NoRedirect().redirect_request(
                    request, None, code, "redirect", {}, "https://other.example/"
                )
            response = io.BytesIO(raw)
            response.status, response.headers = code, headers
            return response

    def build(handler):
        assert handler is app.NoRedirect
        return Opener()

    monkeypatch.setattr(app, "build_opener", build)
    return lambda: app.Operator().verify_serving(tmp_path), calls


@pytest.mark.parametrize("payload,mode", [(ORIGINAL, "exact"), (INSERTED, "approved-insertion")])
@pytest.mark.parametrize("etag", [None, '"root"'])
def test_root_matches_and_reaches_manifest_and_missing(tmp_path, monkeypatch, payload, mode, etag):
    verify, calls = serving(tmp_path, monkeypatch, payload, etag=etag)
    verify()
    assert len(calls) == 3
    evidence = json.loads((tmp_path / "serving-root.json").read_text())
    assert (tmp_path / "serving-root.body").read_bytes() == payload
    assert (tmp_path / "serving-root.body").stat().st_mode & 0o777 == 0o600
    assert evidence["result"] == "OK" and evidence["match"] == mode
    assert evidence["raw"] == {"bytes": len(payload), "sha256": app.digest(payload)}
    assert evidence["raw_complete"] is True
    assert evidence["etag_present"] is (etag is not None) and evidence["etag"] == etag
    assert evidence["compared"] == {"bytes": len(ORIGINAL), "sha256": app.digest(ORIGINAL)}
    assert evidence["insertion_sha256"] == (app.digest(INSERTION) if mode != "exact" else None)


def test_saved_response_matches_recorded_inventory_not_original(tmp_path, monkeypatch):
    assert app.digest(OBSERVED) == (
        "44e6e333e078407e1ee5f582d6fb0905fb2fda0fddfc41e0343001449135353f"
    )
    assert len(INSERTION) == 367 and app.ROOT_ANALYTICS_INSERTION == INSERTION
    verify, calls = serving(tmp_path, monkeypatch, OBSERVED, expected={
        "bytes": 655, "sha256": "5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1"
    })
    verify()
    assert len(calls) == 3


def test_new_cloudflare_script_is_not_learned_from_live_response(tmp_path, monkeypatch):
    changed = (
        Path(__file__).parents[1] / "fixtures/static-root-analytics/changed-root.html"
    ).read_bytes()
    assert app.digest(changed) == (
        "563d09a93aee94ebf8d68b0cc69eaf205f2b97e32c10c7ef3c884fdacf763340"
    )
    verify, calls = serving(tmp_path, monkeypatch, changed, expected={
        "bytes": 655, "sha256": "5f064aed2bdce84445cf9e15dc6d4cb2dee923ea8e750f9f762b7e051ff5c9d1"
    })
    with pytest.raises(app.Blocked, match="no approved insertion"):
        verify()
    assert len(calls) == 1
    assert (tmp_path / "serving-root.body").read_bytes() == changed


@pytest.mark.parametrize("payload", [
    INSERTED.replace(INSERTION, INSERTION * 2),
    INSERTED.replace(INSERTION, b'<script src="https://other.example/a.js"></script>\n'),
    INSERTED.replace(b"static.cloudflareinsights.com", b"other.cloudflareinsights.com"),
    INSERTED.replace(b"1791215815920", b"1791215815921"),
    INSERTED.replace(b"sha512-L0", b"sha512-M0"),
    INSERTED.replace(b'"token":"95', b'"token":"96'),
    INSERTED.replace(b'"r":1', b'"r":0'),
    INSERTED.replace(b'"spa":2', b'"spa":1'),
    INSERTED.replace(b'"2024.11.0"', b'"2025.11.0"'),
    INSERTED.replace(b'crossorigin="anonymous"', b'crossorigin="use-credentials"'),
    INSERTED.replace(b'<script type="module" src="https:', b'<script defer src="https:'),
    INSERTED.replace(b"data-cf-beacon=", b"nonce=\"extra\" data-cf-beacon="),
    ORIGINAL.replace(b"</head>", INSERTION + b"</head>"),
    ORIGINAL + INSERTION,
    INSERTED.replace(b"  <script", b" <script"),
    INSERTED.replace(INSERTION, INSERTION[:-1]),
    INSERTED.replace(INSERTION, INSERTION[:-1] + b"\r\n"),
    INSERTED.replace(INSERTION, INSERTION + b"\n"),
    INSERTED.replace(b"app.js", b"bad.js"),
    INSERTED.replace(b"app.css", b"bad.css"),
    INSERTED.replace(b'id="root"', b'id="evil"'),
    INSERTED.replace(b"</html>\n", b"</html>"),
    ORIGINAL.replace(b"app.js", b"bad.js"),
])
def test_changes_beyond_exact_insertion_fail(tmp_path, monkeypatch, payload):
    verify, calls = serving(tmp_path, monkeypatch, payload)
    with pytest.raises(app.Blocked):
        verify()
    assert len(calls) == 1
    evidence = json.loads((tmp_path / "serving-root.json").read_text())
    assert evidence["result"] == "BLOCKED" and evidence["match"] is None
    assert (tmp_path / "serving-root.body").read_bytes() == payload


@pytest.mark.parametrize("options", [
    {"status": 403}, {"status": 201}, {"mime": "text/plain"},
    {"cache": "public, max-age=3600"},
])
def test_root_header_failure_retains_raw(tmp_path, monkeypatch, options):
    verify, calls = serving(tmp_path, monkeypatch, INSERTED, **options)
    with pytest.raises(app.Blocked):
        verify()
    assert len(calls) == 1
    assert (tmp_path / "serving-root.body").read_bytes() == INSERTED
    assert json.loads((tmp_path / "serving-root.json").read_text())["result"] == "BLOCKED"


def test_root_byte_budget_cannot_be_bypassed_even_by_matching_inventory(tmp_path, monkeypatch):
    payload = b"x" * (app.SERVING_BYTE_LIMIT + 2)
    read_prefix = payload[:-1]
    verify, calls = serving(tmp_path, monkeypatch, payload, expected={
        "bytes": len(read_prefix), "sha256": app.digest(read_prefix)
    })
    with pytest.raises(app.Blocked, match="byte budget"):
        verify()
    assert len(calls) == 1
    assert (tmp_path / "serving-root.body").read_bytes() == read_prefix
    assert json.loads((tmp_path / "serving-root.json").read_text())["raw_complete"] is False


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_root_redirects_rejected_with_analytics(tmp_path, monkeypatch, code):
    verify, calls = serving(tmp_path, monkeypatch, INSERTED, status=code)
    with pytest.raises(ValueError, match="redirected"):
        verify()
    assert len(calls) == 1


@pytest.mark.parametrize("options,count", [
    ({"manifest_change": "content"}, 2), ({"manifest_change": "length"}, 2),
    ({"manifest_change": "etag"}, 2), ({"missing": 200}, 3), ({"missing": 403}, 3),
])
def test_approved_insertion_does_not_skip_subsequent_gates(tmp_path, monkeypatch, options, count):
    verify, calls = serving(tmp_path, monkeypatch, INSERTED, **options)
    with pytest.raises(app.Blocked):
        verify()
    assert len(calls) == count
    assert json.loads((tmp_path / "serving-root.json").read_text())["result"] == "OK"
