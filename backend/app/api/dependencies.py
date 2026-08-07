from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenValidationError, decode_token
from app.db import get_db
from app.repositories import IdentityRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
SessionDep = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Immutable authentication context for request-scoped user identity."""

    user_id: int
    phone: str
    display_name: str


async def get_current_context(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep
) -> AuthContext:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        claims = decode_token(token, get_settings(), "access")
    except TokenValidationError as exc:
        raise unauthorized from exc
    user = await IdentityRepository(session).get_user(claims.user_id)
    if (
        user is None
        or not user.is_active
        or user.token_version != claims.token_version
    ):
        raise unauthorized
    return AuthContext(
        user_id=user.id,
        phone=user.phone,
        display_name=user.display_name,
    )


CurrentContext = Annotated[AuthContext, Depends(get_current_context)]


def require_roles(
    *allowed_roles: str,
) -> Callable[[CurrentContext], Coroutine[Any, Any, AuthContext]]:
    """SoloChef 单用户无角色体系；保留符号以兼容旧路由，恒放行。"""

    async def dependency(context: CurrentContext) -> AuthContext:
        return context

    return dependency


OwnerContext = CurrentContext
