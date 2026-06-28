from fastapi import APIRouter, Query

from core.dependency import AdminUser, CurrentUser, DbSession
from schemas.base import Success
from schemas.users import UserCreate, UserUpdate
from services.user_service import UserService

router = APIRouter()


@router.get("/me", summary="当前用户")
async def get_me(current_user: CurrentUser):
    return Success(data=current_user.to_dict(exclude={"password"}))


@router.get("/", summary="查看用户列表")
async def list_users(
    db: DbSession,
    current_user: AdminUser,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    username: str = Query("", description="用户名，用于搜索"),
    email: str = Query("", description="邮箱地址"),
    phone: str = Query("", description="手机号"),
    is_active: bool | None = Query(None, description="是否启用"),
):
    return await UserService(db).get_user_list(
        page=page,
        page_size=page_size,
        username=username,
        email=email,
        phone=phone,
        is_active=is_active,
    )


@router.post("/", summary="创建用户")
async def create_user(
    user_in: UserCreate,
    db: DbSession,
    current_user: AdminUser,
):
    return await UserService(db).create_user(user_in)


@router.get("/{user_id}", summary="查看用户")
async def get_user(
    user_id: int,
    db: DbSession,
    current_user: AdminUser,
):
    return await UserService(db).get_user_detail(user_id)


@router.put("/{user_id}", summary="更新用户")
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: DbSession,
    current_user: AdminUser,
):
    return await UserService(db).update_user(user_id, user_in)


@router.delete("/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: DbSession,
    current_user: AdminUser,
):
    return await UserService(db).delete_user(user_id)


@router.post("/{user_id}/reset-password", summary="重置密码")
async def reset_password(
    user_id: int,
    db: DbSession,
    current_user: AdminUser,
):
    return await UserService(db).reset_user_password(user_id)
