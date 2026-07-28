import logging
import time
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS
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

    # llens(#94): soft delete。監査証憑の保全のため行は消さず、削除時刻(epoch 秒)と
    # 実行者を立てる。ユーザー向けクエリは deleted_at IS NULL で除外し、admin 一覧では見える。
    deleted_at = Column(BigInteger, nullable=True)
    deleted_by = Column(Text, nullable=True)
    # llens(#94): purge(実体削除)。Storage の実体を消し filename/path/data/access_control を
    # 落とした後に立てる。行と id/hash/meta.size は監査用に残る。
    purged_at = Column(BigInteger, nullable=True)
    purged_by = Column(Text, nullable=True)


class FileModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    hash: Optional[str] = None

    # purge 済み行は filename も落ちる(#94)
    filename: Optional[str] = None
    path: Optional[str] = None

    data: Optional[dict] = None
    meta: Optional[dict] = None

    access_control: Optional[dict] = None

    created_at: Optional[int]  # timestamp in epoch
    updated_at: Optional[int]  # timestamp in epoch
    accessed_at: Optional[int] = None  # timestamp in epoch

    deleted_at: Optional[int] = None  # timestamp in epoch
    deleted_by: Optional[str] = None
    purged_at: Optional[int] = None  # timestamp in epoch
    purged_by: Optional[str] = None


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

    def get_file_by_id(
        self, id: str, include_deleted: bool = False
    ) -> Optional[FileModel]:
        # llens(#94): 既定で deleted(purged 含む)を除外する。tool 経路(sandbox 等)は
        # 本メソッドを直呼びしており、既定除外で削除済み file への到達を一括で断つ。
        # admin 経路・restore/purge・削除済み表示だけ include_deleted=True を使う。
        with get_db() as db:
            try:
                file = db.get(File, id)
                if file is None:
                    return None
                if not include_deleted and file.deleted_at is not None:
                    return None
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
            return [
                FileModel.model_validate(file)
                for file in db.query(File).filter(File.deleted_at == None).all()
            ]

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
                for file in db.query(File)
                .filter_by(user_id=user_id)
                .filter(File.deleted_at == None)
                .all()
            ]

    def get_files_page(
        self,
        user_ids: Optional[list[str]] = None,
        filename: Optional[str] = None,
        deleted: str = "exclude",
        order_by: str = "created_at",
        direction: str = "desc",
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[FileModel], int]:
        """llens(#69): ファイル管理ページ用のページング一覧。

        order/limit/offset は SQL 側で効かせる(全件ロードして python で切らない)。
        user_ids=None は全ユーザー(admin 用)、指定時はそのユーザー群に絞る。
        deleted は soft delete 済み(#94)の扱い: exclude(既定)/ only / include。
        戻り値は (items, total)。total は filter 適用後・ページング前の件数。
        """
        with get_db() as db:
            query = db.query(File)
            if user_ids is not None:
                query = query.filter(File.user_id.in_(user_ids))
            if deleted == "exclude":
                query = query.filter(File.deleted_at == None)
            elif deleted == "only":
                query = query.filter(File.deleted_at != None)
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
        from nucllei.models.users import User

        with get_db() as db:
            rows = (
                db.query(
                    User.id,
                    User.name,
                    User.email,
                    func.count(File.id).label("file_count"),
                )
                .join(File, File.user_id == User.id)
                .filter(File.deleted_at == None)
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

    # llens(#94): 削除系はすべて soft delete(deleted_at/deleted_by を立てて非表示化)。
    # 監査証憑の保全のため行は消さない。実体の破棄は admin 明示操作の purge のみで、
    # その場合も行と id/hash/meta.size は残す(行の物理削除経路は持たない)。

    def soft_delete_file_by_id(self, id: str, deleted_by: str) -> bool:
        """未削除の行にのみ deleted_at/deleted_by を立てる。

        既に deleted/purged の行は対象にしない(元の deleted_at/deleted_by を
        上書きしない)— 再実行は no-op として True を返す(冪等)。
        """
        try:
            with get_db() as db:
                db.query(File).filter_by(id=id, deleted_at=None).update(
                    {"deleted_at": int(time.time()), "deleted_by": deleted_by}
                )
                db.commit()

                return True
        except Exception:
            return False

    def restore_file_by_id(self, id: str) -> bool:
        """deleted(未 purge)の行を active に戻す(deleted_at/deleted_by を NULL)。

        active・purged・不存在の行は対象外(状態を事前取得してガードし False を返す)。
        """
        try:
            with get_db() as db:
                file = db.get(File, id)
                if (
                    file is None
                    or file.deleted_at is None
                    or file.purged_at is not None
                ):
                    return False
                file.deleted_at = None
                file.deleted_by = None
                db.commit()

                return True
        except Exception:
            return False

    def purge_file_by_id(self, id: str, purged_by: str) -> Optional[FileModel]:
        """deleted(未 purge)の行から実体由来のフィールドを落とし purged_at/purged_by を立てる。

        Storage の実体削除は呼び出し側(router)が先に済ませる。data も落とすのが仕様 —
        抽出テキストは実体由来で、残すと purge 後もモデル注入できてしまう。
        meta は監査用に size のみ残す。active・既 purge・不存在は None(事前ガード)。
        """
        try:
            with get_db() as db:
                file = db.get(File, id)
                if (
                    file is None
                    or file.deleted_at is None
                    or file.purged_at is not None
                ):
                    return None
                file.purged_at = int(time.time())
                file.purged_by = purged_by
                file.filename = None
                file.path = None
                file.data = None
                file.access_control = None
                size = (file.meta or {}).get("size")
                file.meta = {"size": size} if size is not None else {}
                db.commit()

                return FileModel.model_validate(file)
        except Exception:
            return None


Files = FilesTable()
