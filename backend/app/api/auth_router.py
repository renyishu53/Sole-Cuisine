from datetime import UTC, datetime
from secrets import token_urlsafe

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentContext, SessionDep
from app.core.config import get_settings
from app.repositories import IdentityRepository
from app.schemas import (
    AuthSession,
    ChangePasswordRequest,
    CurrentSession,
    DeviceSessionInfo,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendSMSCodeRequest,
    SendSMSCodeResponse,
    SMSLoginRequest,
    UserSummary,
)
from app.services.auth import AuthenticationError, AuthService, RegistrationConflictError
from app.services.sms import SMSCodeMismatchError, SMSError, SMSRateLimitError, SMSService

router = APIRouter(tags=["authentication"])
settings = get_settings()


@router.post("/auth/register", response_model=AuthSession, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, session: SessionDep) -> AuthSession:
    try:
        await SMSService(settings).verify_code(request.phone, request.verification_code)
    except SMSCodeMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        registration = request.model_dump(exclude={"verification_code"})
        return await AuthService(session, settings).register(**registration)
    except RegistrationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/auth/login", response_model=AuthSession)
async def login(request: LoginRequest, session: SessionDep) -> AuthSession:
    try:
        return await AuthService(session, settings).login(request.phone, request.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/auth/refresh", response_model=AuthSession)
async def refresh(request: RefreshRequest, session: SessionDep) -> AuthSession:
    try:
        return await AuthService(session, settings).refresh(request.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/auth/me", response_model=CurrentSession)
async def me(context: CurrentContext) -> CurrentSession:
    return CurrentSession(
        user=UserSummary(
            id=context.user_id,
            phone=context.phone,
            display_name=context.display_name,
        ),
        jwt_development_secret=settings.jwt_uses_development_secret,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: LogoutRequest, session: SessionDep) -> None:
    try:
        await AuthService(session, settings).logout(request.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(context: CurrentContext, session: SessionDep) -> None:
    await AuthService(session, settings).logout_all(context.user_id)


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: ChangePasswordRequest, context: CurrentContext, session: SessionDep
) -> None:
    try:
        await AuthService(session, settings).change_password(
            context.user_id, request.current_password, request.new_password
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ── SMS verification endpoints ──────────────────────────────────────

_SCENE_TEMPLATE_MAP: dict[str, str] = {
    "login": settings.sms_template_login,
    "reset_password": settings.sms_template_reset_password,
    "change_phone": settings.sms_template_change_phone,
    "bind_phone": settings.sms_template_bind_phone,
    "verify_phone": settings.sms_template_verify_phone,
}


@router.post("/auth/sms/send", response_model=SendSMSCodeResponse)
async def send_sms_code(request: SendSMSCodeRequest, session: SessionDep) -> SendSMSCodeResponse:
    """Send a verification code to the given phone number."""
    sms = SMSService(settings)
    template_code = _SCENE_TEMPLATE_MAP.get(request.scene, settings.sms_template_login)
    try:
        await sms.send_code(request.phone, template_code)
    except SMSRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except SMSError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return SendSMSCodeResponse(
        message="验证码已发送",
        expire_minutes=settings.sms_code_expire_minutes,
        retry_after_seconds=settings.sms_send_interval_seconds,
    )


@router.post("/auth/sms/login", response_model=AuthSession)
async def sms_login(request: SMSLoginRequest, session: SessionDep) -> AuthSession:
    """Login or register using SMS verification code."""
    sms = SMSService(settings)
    try:
        await sms.verify_code(request.phone, request.code)
    except SMSCodeMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    auth = AuthService(session, settings)
    repository = IdentityRepository(session)
    user = await repository.get_user_by_phone(request.phone)

    if user is not None:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用"
            )
        return await auth.issue_session(user)

    # New user: auto-register with a random password (SMS-only accounts)
    try:
        return await auth.register(
            phone=request.phone,
            password=token_urlsafe(16),
            display_name=request.display_name or f"用户{request.phone[-4:]}",
        )
    except RegistrationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(request: ResetPasswordRequest, session: SessionDep) -> None:
    """Reset password via SMS verification code and revoke all sessions."""
    repository = IdentityRepository(session)
    if await repository.get_user_by_phone(request.phone) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该手机号尚未注册")
    try:
        await SMSService(settings).verify_code(request.phone, request.code)
    except SMSCodeMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await AuthService(session, settings).reset_password(request.phone, request.new_password)


# ── Device sessions ─────────────────────────────────────────────────


@router.get("/auth/sessions", response_model=list[DeviceSessionInfo])
async def list_device_sessions(
    context: CurrentContext, session: SessionDep
) -> list[DeviceSessionInfo]:
    """List the current user's active (non-revoked, non-expired) login sessions."""
    records = await IdentityRepository(session).list_active_refresh_sessions(context.user_id)
    now = datetime.now(UTC)
    result: list[DeviceSessionInfo] = []
    for record in records:
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            continue
        result.append(
            DeviceSessionInfo(
                id=record.id,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )
    return result


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device_session(
    session_id: str, context: CurrentContext, session: SessionDep
) -> None:
    """Revoke one specific login session owned by the current user."""
    repository = IdentityRepository(session)
    record = await repository.get_refresh_session(session_id)
    if record is None or record.user_id != context.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    await repository.revoke_refresh_session(session_id)
