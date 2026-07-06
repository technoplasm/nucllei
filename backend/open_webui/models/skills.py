import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.users import Users, UserResponse

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text, JSON

from open_webui.utils.access_control import has_access

####################
# Skills DB Schema
####################
#
# Skill は Prompt の後継(issue #35)。旧 prompt を統合し、サーバーサイドの
# `/command` 実行と(step2 で)tool 遅延評価の基盤となる。移行方針は
# `_memos/skill-design.md` を参照。


class Skill(Base):
    __tablename__ = "skill"

    id = Column(String, primary_key=True)
    # command は先頭 `/` なしで正規化して格納(旧 prompt は `/` 込みだったため移行時に strip)。
    command = Column(String, unique=True)
    user_id = Column(String)
    title = Column(Text)
    # description はモデル向け manifest 用(#35 step2)。空ならモデルには露出させない。
    description = Column(Text, nullable=True)
    content = Column(Text)
    # tool_ids は紐づく tool。カラムは存在するが runtime では未消費(#35 step2 で遅延評価)。
    tool_ids = Column(JSON, nullable=True)

    access_control = Column(JSON, nullable=True)  # Controls data access levels.
    # Defines access control rules for this entry.
    # - `None`: Public access, available to all users with the "user" role.
    # - `{}`: Private access, restricted exclusively to the owner.
    # - Custom permissions: Specific access control for reading and writing;
    #   Can specify group or user-level restrictions:
    #   {
    #      "read": {
    #          "group_ids": ["group_id1", "group_id2"],
    #          "user_ids":  ["user_id1", "user_id2"]
    #      },
    #      "write": {
    #          "group_ids": ["group_id1", "group_id2"],
    #          "user_ids":  ["user_id1", "user_id2"]
    #      }
    #   }

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class SkillModel(BaseModel):
    id: str
    command: str
    user_id: str
    title: str
    description: Optional[str] = None
    content: str
    tool_ids: list[str] = []

    access_control: Optional[dict] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class SkillUserResponse(SkillModel):
    user: Optional[UserResponse] = None


class SkillForm(BaseModel):
    command: str
    title: str
    description: Optional[str] = None
    content: str
    tool_ids: list[str] = []
    access_control: Optional[dict] = None


def normalize_command(command: str) -> str:
    """command を先頭 `/` なしに正規化する。"""
    return command.lstrip("/").strip()


class SkillsTable:
    def insert_new_skill(
        self, user_id: str, form_data: SkillForm
    ) -> Optional[SkillModel]:
        skill = SkillModel(
            **{
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                **form_data.model_dump(),
                "command": normalize_command(form_data.command),
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
                skill = db.get(Skill, id)
                return SkillModel.model_validate(skill)
        except Exception:
            return None

    def get_skill_by_command(self, command: str) -> Optional[SkillModel]:
        try:
            with get_db() as db:
                skill = (
                    db.query(Skill)
                    .filter_by(command=normalize_command(command))
                    .first()
                )
                return SkillModel.model_validate(skill)
        except Exception:
            return None

    def get_skills(self) -> list[SkillUserResponse]:
        with get_db() as db:
            skills = []

            for skill in db.query(Skill).order_by(Skill.updated_at.desc()).all():
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
        self, user_id: str, permission: str = "write"
    ) -> list[SkillUserResponse]:
        skills = self.get_skills()

        return [
            skill
            for skill in skills
            if skill.user_id == user_id
            or has_access(user_id, permission, skill.access_control)
        ]

    def update_skill_by_command(
        self, command: str, form_data: SkillForm
    ) -> Optional[SkillModel]:
        try:
            with get_db() as db:
                skill = (
                    db.query(Skill)
                    .filter_by(command=normalize_command(command))
                    .first()
                )
                skill.title = form_data.title
                skill.description = form_data.description
                skill.content = form_data.content
                skill.tool_ids = form_data.tool_ids
                skill.access_control = form_data.access_control
                skill.updated_at = int(time.time())
                db.commit()
                return SkillModel.model_validate(skill)
        except Exception:
            return None

    def delete_skill_by_command(self, command: str) -> bool:
        try:
            with get_db() as db:
                db.query(Skill).filter_by(command=normalize_command(command)).delete()
                db.commit()

                return True
        except Exception:
            return False


Skills = SkillsTable()
