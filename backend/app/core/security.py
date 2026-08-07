from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

import bcrypt
import jwt
from jwt import InvalidTokenError

from app.core.config import Settings

TokenType = Literal["access", "refresh"]


class TokenValidationError(ValueError):
    """Raised when a JWT cannot be trusted or has the wrong token type."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: int
    token_version: int
    token_type: TokenType
    jti: str
    expires_at: datetime


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(
    *,
    settings: Settings,
    user_id: int,
    token_version: int,
    token_type: TokenType,
) -> str:
    now = datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    payload = {
        "iss": settings.jwt_issuer,
        "sub": str(user_id),
        "ver": token_version,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings, expected_type: TokenType) -> TokenClaims:
    try:
        raw = cast(
            dict[str, Any],
            jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
                issuer=settings.jwt_issuer,
                options={"require": ["exp", "iat", "iss", "sub", "type", "ver"]},
            ),
        )
        token_type = raw["type"]
        if token_type != expected_type:
            raise TokenValidationError("令牌类型不正确")
        return TokenClaims(
            user_id=int(raw["sub"]),
            token_version=int(raw["ver"]),
            token_type=expected_type,
            jti=str(raw["jti"]),
            expires_at=datetime.fromtimestamp(float(raw["exp"]), UTC),
        )
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, TokenValidationError):
            raise
        raise TokenValidationError("无效或已过期的令牌") from exc
