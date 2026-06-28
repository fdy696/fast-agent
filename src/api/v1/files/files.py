from fastapi import APIRouter, File, Query, UploadFile

from core.dependency import CurrentUser, DbSession
from services.file_service import FileService

router = APIRouter()


@router.post("/upload", summary="上传文件")
async def upload_file(
    db: DbSession,
    current_user: CurrentUser,
    file: UploadFile = File(..., description="要上传的文件"),
):
    return await FileService(db).upload_file(file, current_user)


@router.get("/", summary="文件列表")
async def list_files(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await FileService(db).list_files(current_user, page=page, page_size=page_size)
