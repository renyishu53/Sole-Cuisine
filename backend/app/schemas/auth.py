from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class RegisterRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    verification_code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    password: str = Field(min_length=8, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class LogoutRequest(RefreshRequest):
    pass


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)

    @model_validator(mode="after")
    def different_password(self) -> "ChangePasswordRequest":
        if self.current_password == self.new_password:
            raise ValueError("新密码不能与当前密码相同")
        return self


class UserSummary(BaseModel):
    id: int
    phone: str
    display_name: str


class AuthSession(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserSummary


class CurrentSession(BaseModel):
    user: UserSummary
    jwt_development_secret: bool


# ── SMS verification ────────────────────────────────────────────────

class SendSMSCodeRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    scene: str = Field(
        default="login", pattern=r"^(login|reset_password|change_phone|bind_phone|verify_phone)$"
    )


class SendSMSCodeResponse(BaseModel):
    message: str
    expire_minutes: int
    retry_after_seconds: int


class SMSLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    code: str = Field(pattern=r"^\d{6}$")
    display_name: str | None = Field(default=None, min_length=1, max_length=80)


class ResetPasswordRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=72)


class DeviceSessionInfo(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
