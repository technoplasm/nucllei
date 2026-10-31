"""レシピの一覧 (入力欄の `/` の補完が読む)。

レシピの読み書きは tool (load_recipe / save_recipe / edit_recipe) だけで、ここは見えるレシピの一覧を
返すだけ。一覧は load_recipe の説明・{{USER_RECIPES}} と同じ物 (utils/recipe_index.user_recipe_listing —
個人・所属グループ・全体。管理者でも所属分だけ)。
"""

from fastapi import APIRouter, Depends

from nucllei.models.skills import Skills
from nucllei.utils.auth import get_verified_user
from nucllei.utils.recipe_index import user_recipe_listing

router = APIRouter()


@router.get("/")
async def get_recipes(user=Depends(get_verified_user)):
    """見えるレシピの一覧。

    - `available`: sandbox のある配備か (無ければレシピは使えない — `recipes` は空)
    - `error`: 一覧を取れなかったとき "old" (sandbox が古い) / "unreachable" (応答しない)。取れれば null
    - `recipes`: 個人 → 所属グループ (名前順) → 全体、各置き場の中は名前の順。要素は
      {name, folder, place ("personal" | "group" | "public"), group, title, description,
      broken (壊れている理由 — 空なら壊れていない), warnings (警告の理由)}
    - `skill_ids`: この利用者が `/` で発火できる skill (持ち主か read 可視 — 管理者でも ACL で見る。
      utils/skills.is_skill_visible と同じ)。どのモデルに紐づくかは呼び手が meta.skillIds で絞る。
      同じ名前のレシピが使われないかの判定 (skill が先) に使う

    呼ぶたびに sandbox に聞く (`/recipes/index` 1 回 + 参照先の stat 1 回 + 所属の DB 1 回)。
    能力「レシピ」はモデルごとなので、どのモデルで使えるかは呼び手 (選んでいるモデルの
    meta.features.recipes) が見る。
    """
    listing = await user_recipe_listing(user)
    return {
        "available": listing.status != "none",
        "error": listing.status if listing.status in ("old", "unreachable") else None,
        "recipes": [entry.as_dict() for entry in listing.entries],
        "skill_ids": [s.id for s in Skills.get_skills_by_user_id(user.id, "read")],
    }
