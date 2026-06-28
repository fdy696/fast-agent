import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from log import logger
from models import User
from repositories.file_mapping import FileMappingRepository
from schemas.base import Success

MAX_FILE_SIZE = 500 * 1024 * 1024
UPLOADS_DIR = "uploads"

ALLOWED_EXTENSIONS: set[str] = {
    ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".json", ".xml", ".csv", ".zip", ".rar", ".7z",
}
DANGEROUS_EXTENSIONS: set[str] = {".exe", ".bat", ".cmd", ".com", ".pif", ".scr", ".vbs", ".js", ".jar", ".sh", ".ps1", ".php", ".asp", ".jsp", ".py", ".pl", ".rb"}


class FileService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.file_repo = FileMappingRepository(db)
        self.logger = logger
        self.uploads_dir = Path(UPLOADS_DIR)
        self.uploads_dir.mkdir(exist_ok=True)

    async def upload_file(self, file: UploadFile, current_user: User) -> Success:
        self._validate_file_security(file)
        safe_filename = self._generate_safe_filename(file.filename or "file")
        content = await self._read_and_validate_file(file)
        file_id = str(uuid.uuid4())
        file_path = self.uploads_dir / f"{file_id}_{safe_filename}"
        file_path.write_bytes(content)
        file_type = self._determine_file_type(file.filename or "")
        await self.file_repo.create_file_mapping(
            file_id=file_id,
            original_name=file.filename or safe_filename,
            file_type=file_type,
            file_size=len(content),
            user_id=current_user.id,
            file_path=str(file_path),
        )
        return Success(
            msg="文件上传成功",
            data={
                "file_id": file_id,
                "original_filename": file.filename,
                "file_type": file_type,
                "file_size": len(content),
                "file_path": str(file_path),
            },
        )

    async def list_files(self, current_user: User, page: int = 1, page_size: int = 20) -> Success:
        items = await self.file_repo.list_by_user(current_user.id, offset=(page - 1) * page_size, limit=page_size)
        return Success(data=[item.to_dict() for item in items])

    def _validate_file_security(self, file: UploadFile) -> None:
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        file_ext = Path(file.filename).suffix.lower()
        if file_ext in DANGEROUS_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不允许上传的文件类型: {file_ext}")
        if file_ext and file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file_ext}")

    def _generate_safe_filename(self, original_filename: str) -> str:
        file_ext = Path(original_filename).suffix.lower()
        return f"{uuid.uuid4().hex}{file_ext}"

    async def _read_and_validate_file(self, file: UploadFile) -> bytes:
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制 {MAX_FILE_SIZE // (1024 * 1024)}MB")
        return content

    def _determine_file_type(self, filename: str) -> str:
        ext = filename.lower().split(".")[-1] if "." in filename else ""
        if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"}:
            return "image"
        if ext in {"mp3", "wav", "flac", "aac", "ogg", "m4a"}:
            return "audio"
        if ext in {"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm"}:
            return "video"
        return "document"
