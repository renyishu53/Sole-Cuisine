from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    TokenValidationError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import RefreshSession, User
from app.repositories import IdentityRepository
from app.schemas import AuthSession, UserSummary


class AuthenticationError(ValueError):
    """Raised for invalid credentials or refresh tokens."""


class RegistrationConflictError(ValueError):
    """Raised when a phone number is already registered."""


class AuthService:
    """Handles user registration, login, session management and password changes."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = IdentityRepository(session)

    async def register(
        self, *, phone: str, password: str, display_name: str
    ) -> AuthSession:
        """Register a new user (SoloChef 单用户，无家庭空间)。"""
        if await self._repository.get_user_by_phone(phone) is not None:
            raise RegistrationConflictError("该手机号已注册")
        try:
            user = await self._repository.create_user(
                phone=phone,
                display_name=display_name,
                password_hash=hash_password(password),
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise RegistrationConflictError("该手机号已注册") from exc
        return await self._issue_session(user)

    async def login(self, phone: str, password: str) -> AuthSession:
        """Authenticate a user by phone and password, returning a session."""
        user = await self._repository.get_user_by_phone(phone)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise AuthenticationError("手机号或密码错误")
        return await self._issue_session(user)

    async def refresh(self, refresh_token: str) -> AuthSession:
        """Rotate a refresh token and issue a new session."""
        try:
            claims = decode_token(refresh_token, self._settings, "refresh")
        except TokenValidationError as exc:
            raise AuthenticationError(str(exc)) from exc
        user = await self._repository.get_user(claims.user_id)
        if user is None or not user.is_active or user.token_version != claims.token_version:
            raise AuthenticationError("刷新令牌已失效")
        refresh_session = await self._repository.get_refresh_session(claims.jti)
        if refresh_session is None or refresh_session.revoked_at is not None:
            raise AuthenticationError("刷新令牌已撤销")
        await self._repository.revoke_refresh_session(claims.jti)
        return await self._issue_session(user)

    async def logout(self, refresh_token: str) -> None:
        """Revoke a single refresh token session."""
        try:
            claims = decode_token(refresh_token, self._settings, "refresh")
        except TokenValidationError as exc:
            raise AuthenticationError(str(exc)) from exc
        await self._repository.revoke_refresh_session(claims.jti)

    async def logout_all(self, user_id: int) -> None:
        """Revoke all refresh token sessions for a user."""
        user = await self._repository.get_user(user_id)
        if user is None:
            raise AuthenticationError("账号不存在")
        await self._repository.revoke_all_sessions(user)

    async def change_password(self, user_id: int, current: str, new: str) -> None:
        """Change user password and revoke all existing sessions."""
        user = await self._repository.get_user(user_id)
        if user is None or not verify_password(current, user.password_hash):
            raise AuthenticationError("当前密码不正确")
        user.password_hash = hash_password(new)
        await self._repository.revoke_all_sessions(user)

    async def reset_password(self, phone: str, new_password: str) -> None:
        """Reset password after SMS verification and revoke all existing sessions."""
        user = await self._repository.get_user_by_phone(phone)
        if user is None:
            raise AuthenticationError("该手机号尚未注册")
        user.password_hash = hash_password(new_password)
        await self._repository.revoke_all_sessions(user)

    async def issue_session(self, user: User) -> AuthSession:
        """Public entry point for issuing an auth session."""
        return await self._issue_session(user)

    async def _issue_session(self, user: User) -> AuthSession:
        """Create token pair and persist refresh session."""
        refresh_token = create_token(
            settings=self._settings,
            user_id=user.id,
            token_version=user.token_version,
            token_type="refresh",
        )
        claims = decode_token(refresh_token, self._settings, "refresh")
        await self._repository.save_refresh_session(
            RefreshSession(
                id=claims.jti,
                user_id=user.id,
                expires_at=claims.expires_at,
            )
        )
        return AuthSession(
            access_token=create_token(
                settings=self._settings,
                user_id=user.id,
                token_version=user.token_version,
                token_type="access",
            ),
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_minutes * 60,
            user=UserSummary(id=user.id, phone=user.phone, display_name=user.display_name),
        )
