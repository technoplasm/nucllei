import time
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.models.users import Users, UserResponse

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import BigInteger, Column, String, Text, JSON

from nucllei.utils.access_control import has_access

####################
# Skills DB Schema
####################
#
# Skill は Prompt の後継(issue #35)。サーバーサイドの `/command` 実行と
# tool 遅延評価の基盤。移行方針は `_memos/skill-design.md` を参照。
#
# llens(#43): id を唯一の可読キー兼発火キーに一本化(旧 command 列は撤去)。
# 表示名は name(旧 title)。全 workspace エンティティを {id, name, description} に統一。
# id は作成時に確定(人が付ける `/command` 文字列)、以後不変。改名は削除→再作成。


class Skill(Base):
    __tablename__ = "skill"

    # id = `/command` 発火キー兼 PK(先頭 `/` なしで正規化して格納)。作成時確定・不変。
    id = Column(String, primary_key=True)
    user_id = Column(String)
    name = Column(Text)
    # description はモデル向け manifest 用(#35 step2)。空ならモデルには露出させない。
    description = Column(Text, nullable=True)
    content = Column(Text)
    # tool_ids は紐づく tool。#35 step2 の遅延評価で消費。
    tool_ids = Column(JSON, nullable=True)
    # 自動ロード条件(入口 3): content_type の前方一致リスト(例 ["audio/"])。
    # 条件に合う添付が会話にあれば `/command` と同じ経路で発火する
    # (utils/skills.py apply_skill_auto_load)。空 = 自動ロードしない。
    auto_load_content_types = Column(JSON, nullable=True)

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

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class SkillModel(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    content: str
    tool_ids: list[str] = []
    auto_load_content_types: list[str] = []

    access_control: Optional[dict] = None

    @field_validator("auto_load_content_types", mode="before")
    @classmethod
    def _none_as_empty(cls, value):
        # 列追加より前からある行は NULL — モデル上は常にリスト
        return [] if value is None else value

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class SkillUserResponse(SkillModel):
    user: Optional[UserResponse] = None


class SkillForm(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    content: str
    tool_ids: list[str] = []
    auto_load_content_types: list[str] = []
    access_control: Optional[dict] = None

    @field_validator("auto_load_content_types")
    @classmethod
    def _normalize_content_types(cls, value):
        return normalize_content_type_prefixes(value)


def normalize_content_type_prefixes(prefixes: list[str]) -> list[str]:
    """自動ロード条件(content_type の前方一致リスト)を正規化する。

    前後空白を落として小文字化し、空要素と重複を除く(順序は保つ)。空文字を残すと
    「どの添付にも一致」になるので必ず落とす。
    """
    normalized = []
    for prefix in prefixes or []:
        prefix = prefix.strip().lower()
        if prefix and prefix not in normalized:
            normalized.append(prefix)
    return normalized


def normalize_command(command: str) -> str:
    """`/command`(= skill id)を先頭 `/` なしに正規化する。"""
    return command.lstrip("/").strip()


class SkillsTable:
    def insert_new_skill(
        self, user_id: str, form_data: SkillForm
    ) -> Optional[SkillModel]:
        skill = SkillModel(
            **{
                **form_data.model_dump(),
                "id": normalize_command(form_data.id),
                "user_id": user_id,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
        )

        try:
            with get_db() as db:
                result = Skill(**skill.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return SkillModel.model_validate(result)
                else:
                    return None
        except Exception:
            return None

    def get_skill_by_id(self, id: str) -> Optional[SkillModel]:
        try:
            with get_db() as db:
                skill = db.get(Skill, normalize_command(id))
                return SkillModel.model_validate(skill)
        except Exception:
            return None

    def get_skills(self) -> list[SkillUserResponse]:
        with get_db() as db:
            skills = []

            for skill in db.query(Skill).order_by(Skill.id).all():
                user = Users.get_user_by_id(skill.user_id)
                skills.append(
                    SkillUserResponse.model_validate(
                        {
                            **SkillModel.model_validate(skill).model_dump(),
                            "user": user.model_dump() if user else None,
                        }
                    )
                )

            return skills

    def get_skills_by_user_id(
        self, user_id: str, permission: str = "read"
    ) -> list[SkillUserResponse]:
        skills = self.get_skills()

        return [
            skill
            for skill in skills
            if skill.user_id == user_id
            or has_access(user_id, permission, skill.access_control)
        ]

    def update_skill_by_id(self, id: str, form_data: SkillForm) -> Optional[SkillModel]:
        try:
            with get_db() as db:
                skill = db.get(Skill, normalize_command(id))
                skill.name = form_data.name
                skill.description = form_data.description
                skill.content = form_data.content
                skill.tool_ids = form_data.tool_ids
                skill.auto_load_content_types = form_data.auto_load_content_types
                skill.access_control = form_data.access_control
                skill.updated_at = int(time.time())
                db.commit()
                return SkillModel.model_validate(skill)
        except Exception:
            return None

    def delete_skill_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Skill).filter_by(id=normalize_command(id)).delete()
                db.commit()

                return True
        except Exception:
            return False


Skills = SkillsTable()
