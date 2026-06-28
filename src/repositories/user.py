import secrets
import string
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.crud import CRUDBase
from core.security import get_password_hash, verify_password
from models import User
from schemas.login import CredentialsSchema
from schemas.users import UserCreate, UserUpdate


class UserRepository(CRUDBase[User, UserCreate, UserUpdate]):
    def __init__(self, db: AsyncSession):
        super().__init__(model=User, db=db)

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str | None) -> User | None:
        if not email:
            return None
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str | None) -> User | None:
        if not phone:
            return None
        result = await self.db.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        username: str = "",
        email: str = "",
        phone: str = "",
        is_active: bool | None = None,
    ) -> tuple[int, list[User]]:
        conditions = []
        if username:
            conditions.append(User.username.ilike(f"%{username}%"))
        if email:
            conditions.append(User.email.ilike(f"%{email}%"))
        if phone:
            conditions.append(User.phone.ilike(f"%{phone}%"))
        if is_active is not None:
            conditions.append(User.is_active == is_active)

        stmt = select(User)
        count_stmt = select(func.count()).select_from(User)
        for condition in conditions:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total_result = await self.db.execute(count_stmt)
        total = int(total_result.scalar_one())
        result = await self.db.execute(stmt.order_by(User.created_at.desc()).offset(offset).limit(limit))
        return total, list(result.scalars().all())

    async def create_user(self, obj_in: UserCreate) -> User:
        data = obj_in.model_dump(exclude_unset=True)
        data["password"] = get_password_hash(data["password"])
        db_obj = User(**data)
        self.db.add(db_obj)
        await self.db.commit()
        await self.db.refresh(db_obj)
        return db_obj

    async def authenticate(self, credentials: CredentialsSchema) -> User:
        identifier = credentials.username
        stmt = select(User).where(
            or_(User.username == identifier, User.email == identifier, User.phone == identifier)
        )
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=400, detail="无效的用户名")
        if not verify_password(credentials.password, user.password):
            raise HTTPException(status_code=400, detail="密码错误")
        if not user.is_active:
            raise HTTPException(status_code=400, detail="用户已被禁用")
        return user

    async def update_last_login(self, user: User | int) -> None:
        user_obj = await self.get(user) if isinstance(user, int) else user
        if user_obj:
            user_obj.last_login = datetime.now(timezone.utc)
            await self.db.commit()

    async def reset_password(self, user_id: int) -> str:
        user_obj = await self.get(user_id)
        if not user_obj:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user_obj.is_superuser:
            raise HTTPException(status_code=403, detail="不允许重置超级管理员密码")
        new_password = self._generate_secure_password()
        user_obj.password = get_password_hash(new_password)
        await self.db.commit()
        return new_password

    def _generate_secure_password(self, length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    # ── 长期记忆 ──

    async def get_preference(self, user_id: int) -> dict | None:
        user = await self.get(user_id)
        return user.preference if user else None

    async def update_preference(self, user_id: int, preference: dict) -> None:
        user = await self.get(user_id)
        if user:
            user.preference = preference
            await self.db.commit()
