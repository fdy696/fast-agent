from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_password_hash
from log import logger
from repositories.user import UserRepository
from schemas.base import Fail, Success, SuccessExtra
from schemas.users import UserCreate, UserUpdate
from utils.cache import clear_user_cache


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.logger = logger

    async def get_user_list(
        self,
        page: int = 1,
        page_size: int = 10,
        username: str = "",
        email: str = "",
        phone: str = "",
        is_active: bool | None = None,
    ) -> SuccessExtra:
        total, items = await self.user_repo.list_users(
            offset=(page - 1) * page_size,
            limit=page_size,
            username=username,
            email=email,
            phone=phone,
            is_active=is_active,
        )
        data = [obj.to_dict(exclude={"password"}) for obj in items]
        return SuccessExtra(data=data, total=total, page=page, page_size=page_size)

    async def get_user_detail(self, user_id: int) -> Success:
        user_obj = await self.user_repo.get(user_id)
        if not user_obj:
            return Fail(code=404, msg="用户不存在")
        return Success(data=user_obj.to_dict(exclude={"password"}))

    async def create_user(self, user_in: UserCreate) -> Success:
        if await self.user_repo.get_by_username(user_in.username):
            return Fail(code=400, msg="用户名已存在")
        if user_in.email and await self.user_repo.get_by_email(user_in.email):
            return Fail(code=400, msg="邮箱已存在")
        if user_in.phone and await self.user_repo.get_by_phone(user_in.phone):
            return Fail(code=400, msg="手机号已存在")
        new_user = await self.user_repo.create_user(user_in)
        return Success(msg="Created Successfully", data=new_user.to_dict(exclude={"password"}))

    async def update_user(self, user_id: int, user_in: UserUpdate) -> Success:
        user = await self.user_repo.get(user_id)
        if not user:
            return Fail(code=404, msg="用户不存在")

        data = user_in.model_dump(exclude_unset=True)

        username = data.get("username")
        if username and username != user.username:
            existing = await self.user_repo.get_by_username(username)
            if existing and existing.id != user.id:
                return Fail(code=400, msg="用户名已存在")

        email = data.get("email")
        if email and email != user.email:
            existing = await self.user_repo.get_by_email(email)
            if existing and existing.id != user.id:
                return Fail(code=400, msg="邮箱已存在")

        phone = data.get("phone")
        if phone and phone != user.phone:
            existing = await self.user_repo.get_by_phone(phone)
            if existing and existing.id != user.id:
                return Fail(code=400, msg="手机号已存在")

        if data.get("password"):
            data["password"] = get_password_hash(data["password"])
        elif "password" in data:
            data.pop("password")

        for field, value in data.items():
            setattr(user, field, value)

        await self.db.commit()
        await self.db.refresh(user)
        await clear_user_cache(user.id)
        return Success(msg="Updated Successfully", data=user.to_dict(exclude={"password"}))

    async def delete_user(self, user_id: int) -> Success:
        user = await self.user_repo.get(user_id)
        if not user:
            return Fail(code=404, msg="用户不存在")
        if user.is_superuser:
            return Fail(code=403, msg="不允许删除超级管理员")
        await self.db.delete(user)
        await self.db.commit()
        await clear_user_cache(user_id)
        return Success(msg="Deleted Successfully")

    async def reset_user_password(self, user_id: int) -> Success:
        try:
            new_password = await self.user_repo.reset_password(user_id)
            return Success(msg="密码已重置", data={"password": new_password})
        except HTTPException as exc:
            return Fail(code=exc.status_code, msg=str(exc.detail))
