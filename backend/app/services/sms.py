"""Alibaba Cloud SMS verification code service."""

from __future__ import annotations

import random
from typing import Any

from loguru import logger

from app.core.config import Settings
from app.core.redis import RedisClient, get_redis


class SMSError(RuntimeError):
    """Raised when SMS sending fails."""


class SMSRateLimitError(SMSError):
    """Raised when the send interval has not elapsed."""


class SMSCodeMismatchError(ValueError):
    """Raised when the verification code does not match."""


class SMSService:
    """Sends and verifies SMS codes via Alibaba Cloud Dypnsapi (号码认证服务)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._redis: RedisClient | None = None

    async def _ensure_redis(self) -> RedisClient:
        if self._redis is None:
            self._redis = await get_redis(self._settings)
        return self._redis

    async def _ensure_client(self) -> Any:
        """Lazy-init the Alibaba Cloud SMS client."""
        if self._client is not None:
            return self._client
        if not self._settings.sms_access_key_id or not self._settings.sms_access_key_secret:
            raise SMSError(
                "阿里云短信未配置，请设置 SMS_ACCESS_KEY_ID 和 SMS_ACCESS_KEY_SECRET"
            )
        try:
            from alibabacloud_dypnsapi20170525.client import (  # type: ignore[import-untyped]
                Client as DypnsapiClient,
            )
            from alibabacloud_tea_openapi import (  # type: ignore[import-untyped]
                models as open_api_models,
            )

            config = open_api_models.Config(
                access_key_id=self._settings.sms_access_key_id,
                access_key_secret=self._settings.sms_access_key_secret,
            )
            config.endpoint = "dypnsapi.aliyuncs.com"
            self._client = DypnsapiClient(config)
            logger.info("阿里云号码认证短信客户端初始化成功")
        except ImportError:
            logger.error("alibabacloud_dypnsapi20170525 未安装，短信服务不可用")
            raise SMSError(
                "短信服务依赖未安装，请运行: uv add alibabacloud_dypnsapi20170525"
            ) from None
        except Exception as exc:
            logger.error(f"阿里云短信客户端初始化失败: {exc}")
            raise SMSError(f"短信服务初始化失败: {exc}") from exc
        return self._client

    @staticmethod
    def _generate_code() -> str:
        """Generate a 6-digit verification code."""
        return str(random.randint(100000, 999999))

    def _redis_key(self, phone: str, prefix: str = "sms:code") -> str:
        return f"{prefix}:{phone}"

    def _rate_limit_key(self, phone: str) -> str:
        return f"sms:rate:{phone}"

    async def send_code(self, phone: str, template_code: str) -> str:
        """Send a verification code to the given phone number.

        Returns the generated code (for demo/testing; in production, the code is
        only known to the user via SMS).
        """
        redis = await self._ensure_redis()

        # Rate limit check
        rate_key = self._rate_limit_key(phone)
        if await redis.exists(rate_key):
            raise SMSRateLimitError(
                f"请等待 {self._settings.sms_send_interval_seconds} 秒后重试"
            )

        code = self._generate_code()

        # Try real SMS sending
        client = await self._ensure_client()
        try:
            from alibabacloud_dypnsapi20170525 import (  # type: ignore[import-untyped]
                models as dypnsapi_models,
            )

            request = dypnsapi_models.SendSmsVerifyCodeRequest(
                phone_number=phone,
                sign_name=self._settings.sms_sign_name,
                template_code=template_code,
                template_param=f'{{"code":"{code}","min":"{self._settings.sms_code_expire_minutes}"}}',
            )
            response = client.send_sms_verify_code(request)
            if response.body.code != "OK" or not response.body.success:
                logger.error(f"短信发送失败: {response.body.message}")
                raise SMSError(f"短信发送失败: {response.body.message}")
            logger.info(f"短信验证码已发送至 {phone}")
        except SMSError:
            raise
        except Exception as exc:
            logger.error(f"短信发送异常: {exc}")
            raise SMSError(f"短信发送失败: {exc}") from exc

        # Store code in Redis for verification
        await redis.set(
            self._redis_key(phone),
            code,
            self._settings.sms_code_expire_minutes * 60,
        )
        # Set rate limit
        await redis.set(
            rate_key,
            "1",
            self._settings.sms_send_interval_seconds,
        )

        return code

    async def send_login_code(self, phone: str) -> str:
        """Send login/register verification code."""
        return await self.send_code(phone, self._settings.sms_template_login)

    async def send_reset_password_code(self, phone: str) -> str:
        """Send password reset verification code."""
        return await self.send_code(phone, self._settings.sms_template_reset_password)

    async def send_change_phone_code(self, phone: str) -> str:
        """Send phone change verification code."""
        return await self.send_code(phone, self._settings.sms_template_change_phone)

    async def send_bind_phone_code(self, phone: str) -> str:
        """Send bind phone verification code."""
        return await self.send_code(phone, self._settings.sms_template_bind_phone)

    async def send_verify_phone_code(self, phone: str) -> str:
        """Send verify phone ownership code."""
        return await self.send_code(phone, self._settings.sms_template_verify_phone)

    async def verify_code(self, phone: str, code: str) -> bool:
        """Verify a code against the stored value. Consumes the code on success."""
        redis = await self._ensure_redis()
        key = self._redis_key(phone)
        stored = await redis.get(key)
        if stored is None:
            raise SMSCodeMismatchError("验证码已过期，请重新获取")
        if stored != code:
            raise SMSCodeMismatchError("验证码错误")
        await redis.delete(key)
        return True
