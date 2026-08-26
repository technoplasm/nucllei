import logging
import time
from typing import Optional

from nucllei.internal.db import Base, JSONField, get_db
from nucllei.models.users import Users, UserResponse
from nucllei.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text, JSON

from nucllei.utils.access_control import has_access

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# Tools DB Schema
####################


class Tool(Base):
    __tablename__ = "tool"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    name = Column(Text)
    # llens(#43): description を top-level 列に昇格(旧 meta.description)。全エンティティ
    # {id, name, description} に統一。meta は builtin 判別のみを残す。
    description = Column(Text)
    content = Column(Text)
    specs = Column(JSONField)
    meta = Column(JSONField)
    valves = Column(JSONField)

    access_control = Column(JSON, nullable=True)  # Controls data access levels.
    # Defines access control rules for this entry.
    # - `None`: Public access, available to all users with the "user" role.
    # - `{}`: Private access, restricted exclusively to the owner.
    # - Custom permissions (llens #66: ACL は read = 「使える範囲」一本):
    #   {
    #      "read": {
    #          "group_ids": ["group_id1", "group_id2"],
    #          "user_ids":  ["user_id1", "user_id2"]
    #      }
    #   }

    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)


class ToolMeta(BaseModel):
    # llens(#43): description は top-level 列へ昇格。meta は builtin 判別のみを残す。
    # llens(#42): 組み込みツール(repo コード定義・起動時 sync)の判別。UI の
    # セクション分け表示用。ロード分岐と更新保護はレジストリ(builtin_tools)の
    # id が真実源で、このフラグは見た目にしか影響しない
    builtin: Optional[bool] = None


class ToolModel(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    content: str
    specs: list[dict]
    meta: ToolMeta = ToolMeta()
    access_control: Optional[dict] = None

    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class ToolUserModel(ToolModel):
    user: Optional[UserResponse] = None
    # 一覧 UI の Valves 設定ボタンを disable するための派生情報。
    # DB には持たず /list で router がモジュールから埋める(真実源はコード)
    has_valves: Optional[bool] = None


class ToolResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    meta: ToolMeta = ToolMeta()
    access_control: Optional[dict] = None
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch


class ToolUserResponse(ToolResponse):
    user: Optional[UserResponse] = None
    # llens(#49): tool の関数一覧(specs)を picker で見せるため露出。
    # content は含めない(定義そのものは admin の /list・/id で扱う)
    specs: list[dict] = []


class ToolForm(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    content: str
    meta: ToolMeta = ToolMeta()
    access_control: Optional[dict] = None


class ToolValves(BaseModel):
    valves: Optional[dict] = None


class ToolsTable:
    def insert_new_tool(
        self, user_id: str, form_data: ToolForm, specs: list[dict]
    ) -> Optional[ToolModel]:
        with get_db() as db:
            tool = ToolModel(
                **{
                    **form_data.model_dump(),
                    "specs": specs,
                    "user_id": user_id,
                    "updated_at": int(time.time()),
                    "created_at": int(time.time()),
                }
            )

            try:
                result = Tool(**tool.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return ToolModel.model_validate(result)
                else:
                    return None
            except Exception as e:
                log.exception(f"Error creating a new tool: {e}")
                return None

    def get_tool_by_id(self, id: str) -> Optional[ToolModel]:
        try:
            with get_db() as db:
                tool = db.get(Tool, id)
                return ToolModel.model_validate(tool)
        except Exception:
            return None

    def get_tools(self) -> list[ToolUserModel]:
        with get_db() as db:
            tools = []
            for tool in db.query(Tool).order_by(Tool.updated_at.desc()).all():
                user = Users.get_user_by_id(tool.user_id)
                tools.append(
                    ToolUserModel.model_validate(
                        {
                            **ToolModel.model_validate(tool).model_dump(),
                            "user": user.model_dump() if user else None,
                        }
                    )
                )
            return tools

    def get_tools_by_user_id(
        self, user_id: str, permission: str = "read"
    ) -> list[ToolUserModel]:
        tools = self.get_tools()

        return [
            tool
            for tool in tools
            if tool.user_id == user_id
            or has_access(user_id, permission, tool.access_control)
        ]

    def get_tool_valves_by_id(self, id: str) -> Optional[dict]:
        try:
            with get_db() as db:
                tool = db.get(Tool, id)
                return tool.valves if tool.valves else {}
        except Exception as e:
            log.exception(f"Error getting tool valves by id {id}: {e}")
            return None

    def update_tool_valves_by_id(self, id: str, valves: dict) -> Optional[ToolValves]:
        try:
            with get_db() as db:
                db.query(Tool).filter_by(id=id).update(
                    {"valves": valves, "updated_at": int(time.time())}
                )
                db.commit()
                return self.get_tool_by_id(id)
        except Exception:
            return None

    def get_user_valves_by_id_and_user_id(
        self, id: str, user_id: str
    ) -> Optional[dict]:
        try:
            user = Users.get_user_by_id(user_id)
            user_settings = user.settings.model_dump() if user.settings else {}

            # Check if user has "tools" and "valves" settings
            if "tools" not in user_settings:
                user_settings["tools"] = {}
            if "valves" not in user_settings["tools"]:
                user_settings["tools"]["valves"] = {}

            return user_settings["tools"]["valves"].get(id, {})
        except Exception as e:
            log.exception(
                f"Error getting user values by id {id} and user_id {user_id}: {e}"
            )
            return None

    def update_user_valves_by_id_and_user_id(
        self, id: str, user_id: str, valves: dict
    ) -> Optional[dict]:
        try:
            user = Users.get_user_by_id(user_id)
            user_settings = user.settings.model_dump() if user.settings else {}

            # Check if user has "tools" and "valves" settings
            if "tools" not in user_settings:
                user_settings["tools"] = {}
            if "valves" not in user_settings["tools"]:
                user_settings["tools"]["valves"] = {}

            user_settings["tools"]["valves"][id] = valves

            # Update the user settings in the database
            Users.update_user_by_id(user_id, {"settings": user_settings})

            return user_settings["tools"]["valves"][id]
        except Exception as e:
            log.exception(
                f"Error updating user valves by id {id} and user_id {user_id}: {e}"
            )
            return None

    def update_tool_by_id(self, id: str, updated: dict) -> Optional[ToolModel]:
        try:
            with get_db() as db:
                db.query(Tool).filter_by(id=id).update(
                    {**updated, "updated_at": int(time.time())}
                )
                db.commit()

                tool = db.query(Tool).get(id)
                db.refresh(tool)
                return ToolModel.model_validate(tool)
        except Exception:
            return None

    def delete_tool_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Tool).filter_by(id=id).delete()
                db.commit()

                return True
        except Exception:
            return False


Tools = ToolsTable()
