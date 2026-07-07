import logging
import time
from typing import Optional

from open_webui.internal.db import Base, JSONField, get_db
from open_webui.models.users import Users
from open_webui.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# Functions DB Schema
####################


class Function(Base):
    __tablename__ = "function"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    name = Column(Text)
    type = Column(Text)
    # llens(#43): description を top-level 列に昇格(旧 meta.description)。全エンティティ
    # {id, name, description} に統一。#45 で manifest が消え meta は description だけ
    # だったので meta 列ごと撤去した。
    description = Column(Text)
    content = Column(Text)
    valves = Column(JSONField)
    # llens(#46): is_active / is_global は撤去。function の適用制御はモデルの
    # filterIds / actionIds 割り当て一本(pipe は存在すればモデルとして出る)。
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)


class FunctionModel(BaseModel):
    id: str
    user_id: str
    name: str
    type: str
    description: Optional[str] = None
    content: str
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class FunctionResponse(BaseModel):
    id: str
    user_id: str
    type: str
    name: str
    description: Optional[str] = None
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch


class FunctionForm(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    content: str


class FunctionValves(BaseModel):
    valves: Optional[dict] = None


class FunctionsTable:
    def insert_new_function(
        self, user_id: str, type: str, form_data: FunctionForm
    ) -> Optional[FunctionModel]:
        function = FunctionModel(
            **{
                **form_data.model_dump(),
                "user_id": user_id,
                "type": type,
                "updated_at": int(time.time()),
                "created_at": int(time.time()),
            }
        )

        try:
            with get_db() as db:
                result = Function(**function.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return FunctionModel.model_validate(result)
                else:
                    return None
        except Exception as e:
            log.exception(f"Error creating a new function: {e}")
            return None

    def get_function_by_id(self, id: str) -> Optional[FunctionModel]:
        try:
            with get_db() as db:
                function = db.get(Function, id)
                return FunctionModel.model_validate(function)
        except Exception:
            return None

    def get_functions(self) -> list[FunctionModel]:
        with get_db() as db:
            return [
                FunctionModel.model_validate(function)
                for function in db.query(Function).all()
            ]

    def get_functions_by_type(self, type: str) -> list[FunctionModel]:
        with get_db() as db:
            return [
                FunctionModel.model_validate(function)
                for function in db.query(Function).filter_by(type=type).all()
            ]

    def get_function_valves_by_id(self, id: str) -> Optional[dict]:
        with get_db() as db:
            try:
                function = db.get(Function, id)
                return function.valves if function.valves else {}
            except Exception as e:
                log.exception(f"Error getting function valves by id {id}: {e}")
                return None

    def update_function_valves_by_id(
        self, id: str, valves: dict
    ) -> Optional[FunctionValves]:
        with get_db() as db:
            try:
                function = db.get(Function, id)
                function.valves = valves
                function.updated_at = int(time.time())
                db.commit()
                db.refresh(function)
                return self.get_function_by_id(id)
            except Exception:
                return None

    def get_user_valves_by_id_and_user_id(
        self, id: str, user_id: str
    ) -> Optional[dict]:
        try:
            user = Users.get_user_by_id(user_id)
            user_settings = user.settings.model_dump() if user.settings else {}

            # Check if user has "functions" and "valves" settings
            if "functions" not in user_settings:
                user_settings["functions"] = {}
            if "valves" not in user_settings["functions"]:
                user_settings["functions"]["valves"] = {}

            return user_settings["functions"]["valves"].get(id, {})
        except Exception as e:
            log.exception(
                f"Error getting user values by id {id} and user id {user_id}: {e}"
            )
            return None

    def update_user_valves_by_id_and_user_id(
        self, id: str, user_id: str, valves: dict
    ) -> Optional[dict]:
        try:
            user = Users.get_user_by_id(user_id)
            user_settings = user.settings.model_dump() if user.settings else {}

            # Check if user has "functions" and "valves" settings
            if "functions" not in user_settings:
                user_settings["functions"] = {}
            if "valves" not in user_settings["functions"]:
                user_settings["functions"]["valves"] = {}

            user_settings["functions"]["valves"][id] = valves

            # Update the user settings in the database
            Users.update_user_by_id(user_id, {"settings": user_settings})

            return user_settings["functions"]["valves"][id]
        except Exception as e:
            log.exception(
                f"Error updating user valves by id {id} and user_id {user_id}: {e}"
            )
            return None

    def update_function_by_id(self, id: str, updated: dict) -> Optional[FunctionModel]:
        with get_db() as db:
            try:
                db.query(Function).filter_by(id=id).update(
                    {
                        **updated,
                        "updated_at": int(time.time()),
                    }
                )
                db.commit()
                return self.get_function_by_id(id)
            except Exception:
                return None

    def delete_function_by_id(self, id: str) -> bool:
        with get_db() as db:
            try:
                db.query(Function).filter_by(id=id).delete()
                db.commit()

                return True
            except Exception:
                return False


Functions = FunctionsTable()
