"""User-authenticated Firebase APIs; no admin credential or ownership supplied by clients."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import jwt
from jwt import PyJWKClient

from .cloudflare_access import SigningKeyProvider


def request_json(url: str, body: Any, token: str | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    account_id: str


class FirebaseTokenVerifier:
    def __init__(self, project: str, api_key: str, keys: SigningKeyProvider | None = None) -> None:
        self.project = project
        self.api_key = api_key
        self.keys = keys or PyJWKClient(
            "https://www.googleapis.com/service_accounts/v1/jwk/"
            "securetoken@system.gserviceaccount.com",
            timeout=5,
        )

    def verify(self, token: str) -> GoogleIdentity:
        claims = jwt.decode(
            token,
            self.keys.get_signing_key_from_jwt(token).key,
            algorithms=["RS256"],
            audience=self.project,
            issuer=f"https://securetoken.google.com/{self.project}",
            options={"require": ["exp", "iat", "sub", "auth_time"]},
        )
        firebase = claims.get("firebase", {})
        subjects = firebase.get("identities", {}).get("google.com", [])
        if firebase.get("sign_in_provider") != "google.com" or len(subjects) != 1:
            raise ValueError("Google authentication required")
        subject = subjects[0]
        if not isinstance(subject, str) or not subject or "/" in subject:
            raise ValueError("Invalid Google subject")
        # Also check that the persisted Firebase account/token is still valid.
        result = request_json(
            "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key="
            + quote(self.api_key, safe=""),
            {"idToken": token},
        )
        users = result.get("users", [])
        if len(users) != 1 or users[0].get("localId") != claims["sub"] or users[0].get("disabled"):
            raise ValueError("Firebase account unavailable")
        if claims["auth_time"] < int(users[0].get("validSince", "0")):
            raise ValueError("Firebase authentication revoked")
        providers = users[0].get("providerUserInfo", [])
        if (
            len(providers) != 1
            or providers[0].get("providerId") != "google.com"
            or providers[0].get("rawId") != subject
        ):
            raise ValueError("Google identity mismatch")
        digest = hashlib.sha256(
            json.dumps(
                ["autonavlog.account.v1", "https://accounts.google.com", subject],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return GoogleIdentity(subject, "account_v1_" + digest)


def encode_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            {"nullValue": None}
            if value is None
            else {"integerValue": str(value)}
            if isinstance(value, int)
            else {"stringValue": value}
        )
        for key, value in data.items()
    }


def decode_fields(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        if "integerValue" in value:
            result[key] = int(value["integerValue"])
        elif "nullValue" in value:
            result[key] = None
        else:
            result[key] = value.get("stringValue", value.get("doubleValue"))
    return result


class FirestoreMigrationRemote:
    """The #185 envelope and Rules, with REST batch reads and CAS commits."""

    def __init__(self, project: str, identity: GoogleIdentity, token: str) -> None:
        self.root = f"projects/{project}/databases/(default)/documents"
        self.collection = f"{self.root}/googleAccounts/{identity.subject}/projects"
        self.token = token

    def read(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        rows = request_json(
            f"https://firestore.googleapis.com/v1/{self.root}:batchGet",
            {
                "documents": [f"{self.collection}/{id}" for id in ids],
                "newTransaction": {"readOnly": {}},
            },
            self.token,
        )
        result: dict[str, Any] = {}
        for row in rows:
            if "found" in row:
                document = row["found"]
                data = decode_fields(document["fields"])
                id = document["name"].rsplit("/", 1)[-1]
                if (
                    data.get("schema") != 1
                    or data.get("id") != id
                    or not isinstance(data.get("payload"), str)
                    or not isinstance(data.get("version"), str)
                    or not isinstance(data.get("revision"), int)
                    or data["revision"] < 1
                    or data.get("deletion") not in {"ACTIVE", "PENDING_DELETE", "DELETED"}
                    or (
                        data.get("latestDeviceId") is not None
                        and not isinstance(data["latestDeviceId"], str)
                    )
                ):
                    raise ValueError("Invalid Account Sync envelope")
                data["_updateTime"] = document["updateTime"]
                result[document["name"].rsplit("/", 1)[-1]] = data
        return result

    def commit(self, writes: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
        if not writes:
            return
        request_json(
            f"https://firestore.googleapis.com/v1/{self.root}:commit",
            {
                "writes": [
                    {
                        "update": {
                            "name": f"{self.collection}/{value['id']}",
                            "fields": encode_fields(value),
                        },
                        "currentDocument": (
                            {"updateTime": old["_updateTime"]} if old else {"exists": False}
                        ),
                    }
                    for value, old in writes
                ],
            },
            self.token,
        )
