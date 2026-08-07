"""去家庭化后的身份数据访问（user 维度）。

原家庭/成员/邀请相关方法已移除，仅保留用户与刷新会话管理。
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshSession, User


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_phone(self, phone: str) -> User | None:
        return await self._session.scalar(select(User).where(User.phone == phone))

    async def get_user(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def create_user(
        self, *, phone: str, display_name: str, password_hash: str
    ) -> User:
        user = User(phone=phone, display_name=display_name, password_hash=password_hash)
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def save_refresh_session(self, record: RefreshSession) -> None:
        self._session.add(record)
        await self._session.commit()

    async def get_refresh_session(self, session_id: str) -> RefreshSession | None:
        return await self._session.get(RefreshSession, session_id)

    async def list_active_refresh_sessions(self, user_id: int) -> list[RefreshSession]:
        statement = (
            select(RefreshSession)
            .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
            .order_by(RefreshSession.created_at.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def revoke_refresh_session(self, session_id: str) -> bool:
        record = await self.get_refresh_session(session_id)
        if record is None or record.revoked_at is not None:
            return False
        record.revoked_at = datetime.now(UTC)
        await self._session.commit()
        return True

    async def revoke_all_sessions(self, user: User) -> None:
        user.token_version += 1
        records = list(
            (
                await self._session.scalars(
                    select(RefreshSession).where(
                        RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)
                    )
                )
            ).all()
        )
        now = datetime.now(UTC)
        for record in records:
            record.revoked_at = now
        await self._session.commit()
