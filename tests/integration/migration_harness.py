"""Test-only HTTP fixture: real migration/storage/Firestore, synthetic auth identities.

Never included in the runtime image. Production verifier signatures are covered separately.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import Request as FastAPIRequest
from test_issue_94_facade_persistence import OWNER, _new_project

import autonavlog.web.migration_remote as transport
from autonavlog.web.app import _owner_identity, create_app
from autonavlog.web.legacy_migration import LegacyMigration
from autonavlog.web.migration_remote import FirebaseTokenVerifier, GoogleIdentity
from autonavlog.web.migration_routes import install_migration_routes
from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

ROOT = Path(__file__).resolve().parents[2]
web = build_web_application(
    WebRuntimeConfig(
        data_root=ROOT / "data",
        storage_root=Path("/tmp/migration-harness"),
        trusted_local_identity=OWNER,
    )
)


class TestAccess:
    def verify_email(self, assertion):
        if not assertion.startswith("migration-"):
            from autonavlog.web.cloudflare_access import CloudflareAccessVerificationError

            raise CloudflareAccessVerificationError("fixture identity required")
        return assertion + "@example.com"


web.access_verifier = TestAccess()
app = create_app(web_application=web)
# Register API fixture routes ahead of the existing static catch-all.
frontend = app.router.routes.pop()
service = LegacyMigration(
    Path("/tmp/migration-harness/links.sqlite3"), web, "http://127.0.0.1:5179/"
)
web.owner_operation = service.legacy_operation


def identity(subject):
    import hashlib

    account = (
        "account_v1_"
        + hashlib.sha256(
            json.dumps(
                ["autonavlog.account.v1", "https://accounts.google.com", subject],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    return GoogleIdentity(subject, account)


class TestVerifier(FirebaseTokenVerifier):
    def __init__(self):
        self.project = "demo-autonavlog-sync"
        self.api_key = "test"

    def verify(self, token):
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        subject = claims["firebase"]["identities"]["google.com"][0]
        if not subject.startswith("migration-"):
            raise ValueError("only synthetic fixture identities")
        return identity(subject)


# The production adapter always uses Google's fixed URL. Redirect only in this fixture.


def emulator_json(url, body, token=None):
    if not url.startswith("https://firestore.googleapis.com/v1/"):
        raise ValueError("fixture forbids external requests")
    target = url.replace("https://firestore.googleapis.com", "http://127.0.0.1:8088", 1)
    request = Request(
        target,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


transport.request_json = emulator_json
install_migration_routes(app, service, TestVerifier(), _owner_identity)


@app.post("/fixture/seed")
def seed(request: FastAPIRequest, subject: str, count: int = 1):
    if request.client.host not in {"127.0.0.1", "testclient"}:
        raise ValueError("loopback fixture only")
    owner = subject + "@example.com"
    session, _ = _new_project(web, owner=owner)
    web.calculate(session)
    assert session.last_calculation is not None
    if not 1 <= count <= 200:
        raise ValueError("fixture count")
    for index in range(count - 1):
        saved = session.project.model_copy(deep=True)
        saved.id = uuid4()
        saved.revision = 0
        saved.name = f"Legacy saved {index + 1}"
        web.project_service.repository.save(saved, 0)
    result = service.link(owner, identity(subject))
    return {**result, "projectId": str(session.project.id)}


app.router.routes.append(frontend)
