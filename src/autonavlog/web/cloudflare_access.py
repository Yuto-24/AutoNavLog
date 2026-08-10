from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError


class CloudflareAccessVerificationError(ValueError):
    """Raised when an Access assertion cannot be authenticated."""


class _SigningKey(Protocol):
    key: Any


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey: ...


class AccessTokenVerifier(Protocol):
    def verify_email(self, assertion: str) -> str: ...


class CloudflareAccessVerifier:
    """Validate Cloudflare Access application JWTs against rotating JWKS."""

    def __init__(
        self,
        *,
        team_domain: str,
        audience: str,
        signing_key_provider: SigningKeyProvider | None = None,
    ) -> None:
        self.team_domain = self._normalize_team_domain(team_domain)
        self.audience = audience.strip()
        if not self.audience:
            raise ValueError("Cloudflare Access audience must not be empty")
        self._signing_key_provider = signing_key_provider or PyJWKClient(
            f"{self.team_domain}/cdn-cgi/access/certs",
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    @staticmethod
    def _normalize_team_domain(value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not hostname.endswith(".cloudflareaccess.com")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Cloudflare team domain must be an https://*.cloudflareaccess.com origin"
            )
        return normalized

    def verify_email(self, assertion: str) -> str:
        token = assertion.strip()
        if not token:
            raise CloudflareAccessVerificationError("Access assertion is empty")
        try:
            signing_key = self._signing_key_provider.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.team_domain,
                options={"require": ["exp", "iss", "aud", "email"]},
            )
        except (OSError, PyJWTError, ValueError) as error:
            raise CloudflareAccessVerificationError(
                "Cloudflare Access assertion verification failed"
            ) from error
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise CloudflareAccessVerificationError(
                "Cloudflare Access assertion has no email identity"
            )
        return email
