from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from autonavlog.web.cloudflare_access import (
    CloudflareAccessVerificationError,
    CloudflareAccessVerifier,
)

TEAM_DOMAIN = "https://test.cloudflareaccess.com"
AUDIENCE = "test-application-audience"


class StaticSigningKey:
    def __init__(self, key: object) -> None:
        self.key = key


class StaticSigningKeyProvider:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, _token: str) -> StaticSigningKey:
        return StaticSigningKey(self.key)


def _assertion(
    private_key: object,
    *,
    audience: str = AUDIENCE,
    issuer: str = TEAM_DOMAIN,
    email: str | None = "Pilot@Example.com",
    expires_in: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": [audience],
        "iat": now,
        "exp": now + expires_in,
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _verifier(public_key: object, *, audience: str = AUDIENCE) -> CloudflareAccessVerifier:
    return CloudflareAccessVerifier(
        team_domain=TEAM_DOMAIN,
        audience=audience,
        signing_key_provider=StaticSigningKeyProvider(public_key),
    )


def test_verifier_accepts_valid_rs256_assertion_and_returns_email() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assertion = _assertion(private_key)

    assert _verifier(private_key.public_key()).verify_email(assertion) == "Pilot@Example.com"


@pytest.mark.parametrize(
    "assertion_kwargs",
    [
        {"audience": "wrong-audience"},
        {"issuer": "https://other.cloudflareaccess.com"},
        {"expires_in": timedelta(seconds=-1)},
        {"email": None},
    ],
)
def test_verifier_rejects_invalid_claims(assertion_kwargs: dict[str, object]) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assertion = _assertion(private_key, **assertion_kwargs)

    with pytest.raises(CloudflareAccessVerificationError):
        _verifier(private_key.public_key()).verify_email(assertion)


@pytest.mark.parametrize(
    "team_domain",
    [
        "http://test.cloudflareaccess.com",
        "https://test.cloudflareaccess.com/path",
        "https://test.cloudflareaccess.com:443",
        "https://example.com",
    ],
)
def test_verifier_rejects_unsafe_team_domain(team_domain: str) -> None:
    with pytest.raises(ValueError, match="cloudflareaccess"):
        CloudflareAccessVerifier(team_domain=team_domain, audience=AUDIENCE)
