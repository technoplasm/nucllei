import logging
import time
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text, JSON, func

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# Files DB Schema
####################


class File(Base):
    __tablename__ = "file"
    id = Column(String, primary_key=True)
    user_id = Column(String)
    hash = Column(Text, nullable=True)

    filename = Column(Text)
    path = Column(Text, nullable=True)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)

    access_control = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    # llens(#69): 最終利用時刻。content 取得・再添付フックでタッチ、一覧では更新しない。
    accessed_at = Column(BigInteger, nullable=True)


class FileModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    hash: Optional[str] = None

    filename: str
    path: Optional[str] = None

    data: Optional[dict] = None
    meta: Optional[dict] = None

    access_control: Optional[dict] = None

    created_at: Optional[int]  # timestamp in epoch
    updated_at: Optional[int]  # timestamp in epoch
    accessed_at: Optional[int] = None  # timestamp in epoch


####################
# Forms
####################


class FileMeta(BaseModel):
    name: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class FileModelResponse(BaseModel):
    id: str
    user_id: str
    hash: Optional[str] = None

    filename: str
    data: Optional[dict] = None
    meta: FileMeta

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(extra="allow")


class FileMetadataResponse(BaseModel):
    id: str
    meta: dict
    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


class FileForm(BaseModel):
    id: str
    hash: Optional[str] = None
    filename: str
    path: str
    data: dict = {}
    meta: dict = {}
    access_control: Optional[dict] = None


class FilesTable:
    def insert_new_file(self, user_id: str, form_data: FileForm) -> Optional[FileModel]:
        with get_db() as db:
            now = int(time.time())
            file = FileModel(
                **{
                    **form_data.model_dump(),
                    "user_id": user_id,
                    "created_at": now,
                    "updated_at": now,
                    "accessed_at": now,
                }
            )

            try:
                result = File(**file.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return FileModel.model_validate(result)
                else:
                    return None
            except Exception as e:
                log.exception(f"Error inserting a new file: {e}")
                return None

    def get_file_by_id(self, id: str) -> Optional[FileModel]:
        with get_db() as db:
            try:
                file = db.get(File, id)
                return FileModel.model_validate(file)
            except Exception:
                return None

    def get_file_metadata_by_id(self, id: str) -> Optional[FileMetadataResponse]:
        with get_db() as db:
            try:
                file = db.get(File, id)
                return FileMetadataResponse(
                    id=file.id,
                    meta=file.meta,
                    created_at=file.created_at,
                    updated_at=file.updated_at,
                )
            except Exception:
                return None

    def get_files(self) -> list[FileModel]:
        with get_db() as db:
            return [FileModel.model_validate(file) for file in db.query(File).all()]

    def get_files_by_ids(self, ids: list[str]) -> list[FileModel]:
        with get_db() as db:
            return [
                FileModel.model_validate(file)
                for file in db.query(File)
                .filter(File.id.in_(ids))
                .order_by(File.updated_at.desc())
                .all()
            ]

    def get_file_metadatas_by_ids(self, ids: list[str]) -> list[FileMetadataResponse]:
        with get_db() as db:
            return [
                FileMetadataResponse(
                    id=file.id,
                    meta=file.meta,
                    created_at=file.created_at,
                    updated_at=file.updated_at,
                )
                for file in db.query(File)
                .filter(File.id.in_(ids))
                .order_by(File.updated_at.desc())
                .all()
            ]

    def get_files_by_user_id(self, user_id: str) -> list[FileModel]:
        with get_db() as db:
            return [
                FileModel.model_validate(file)
                for file in db.query(File).filter_by(user_id=user_id).all()
            ]

    def get_files_page(
        self,
        user_ids: Optional[list[str]] = None,
        filename: Optional[str] = None,
        order_by: str = "created_at",
        direction: str = "desc",
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[FileModel], int]:
        """llens(#69): ファイル管理ページ用のページング一覧。

        order/limit/offset は SQL 側で効かせる(全件ロードして python で切らない)。
        user_ids=None は全ユーザー(admin 用)、指定時はそのユーザー群に絞る。
        戻り値は (items, total)。total は filter 適用後・ページング前の件数。
        """
        with get_db() as db:
            query = db.query(File)
            if user_ids is not None:
                query = query.filter(File.user_id.in_(user_ids))
            if filename:
                query = query.filter(File.filename.ilike(f"%{filename}%"))

            total = query.count()

            if order_by == "filename":
                # 名前順は大文字小文字を無視(表示名は meta.name だが実体は filename と同値)
                order_column = func.lower(File.filename)
            elif order_by == "accessed_at":
                order_column = File.accessed_at
            else:
                order_column = File.created_at
            query = query.order_by(
                order_column.asc() if direction == "asc" else order_column.desc()
            )
            files = query.offset(offset).limit(limit).all()
            return [FileModel.model_validate(file) for file in files], total

    def get_uploaders(self) -> list[dict]:
        """llens(#69): file を 1 件以上持つユーザーの一覧(admin のフィルタ UI 用)。"""
        # 循環 import 回避のため遅延 import(access_control.py と同じ流儀)
        from open_webui.models.users import User

        with get_db() as db:
            rows = (
                db.query(
                    User.id,
                    User.name,
                    User.email,
                    func.count(File.id).label("file_count"),
                )
                .join(File, File.user_id == User.id)
                .group_by(User.id, User.name, User.email)
                .order_by(func.count(File.id).desc())
                .all()
            )
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "file_count": row.file_count,
                }
                for row in rows
            ]

    def touch_file_accessed_at(self, id: str) -> None:
        """llens(#69): accessed_at を現在時刻に更新する。

        利用時刻の記録は本処理(content 配信等)の副作用でしかないため、
        失敗しても例外を伝播させない(本処理を止めない)。
        """
        try:
            with get_db() as db:
                db.query(File).filter_by(id=id).update(
                    {"accessed_at": int(time.time())}
                )
                db.commit()
        except Exception as e:
            log.debug(f"Error touching accessed_at for file {id}: {e}")

    def update_file_hash_by_id(self, id: str, hash: str) -> Optional[FileModel]:
        with get_db() as db:
            try:
                file = db.query(File).filter_by(id=id).first()
                file.hash = hash
                db.commit()

                return FileModel.model_validate(file)
            except Exception:
                return None

    def update_file_data_by_id(self, id: str, data: dict) -> Optional[FileModel]:
        with get_db() as db:
            try:
                file = db.query(File).filter_by(id=id).first()
                file.data = {**(file.data if file.data else {}), **data}
                db.commit()
                return FileModel.model_validate(file)
            except Exception:

                return None

    def update_file_access_control_by_id(
        self, id: str, access_control: Optional[dict]
    ) -> Optional[FileModel]:
        with get_db() as db:
            try:
                file = db.query(File).filter_by(id=id).first()
                file.access_control = access_control
                file.updated_at = int(time.time())
                db.commit()
                return FileModel.model_validate(file)
            except Exception:
                return None

    def update_file_metadata_by_id(self, id: str, meta: dict) -> Optional[FileModel]:
        with get_db() as db:
            try:
                file = db.query(File).filter_by(id=id).first()
                file.meta = {**(file.meta if file.meta else {}), **meta}
                db.commit()
                return FileModel.model_validate(file)
            except Exception:
                return None

    def delete_file_by_id(self, id: str) -> bool:
        with get_db() as db:
            try:
                db.query(File).filter_by(id=id).delete()
                db.commit()

                return True
            except Exception:
                return False

    def delete_all_files(self) -> bool:
        with get_db() as db:
            try:
                db.query(File).delete()
                db.commit()

                return True
            except Exception:
                return False


Files = FilesTable()
