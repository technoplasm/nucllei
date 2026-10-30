"""見えるレシピの一覧 `{{USER_RECIPES}}` と、レシピの「壊れている」の判定の I/O (2026-10-30)。

判定そのものは `utils/recipes.py` (壊れている = `assess_recipe`、警告 = `reference_warnings`)。
ここは sandbox と DB に問い合わせて材料を集める:

- sandbox `/recipes/index` 1 回 — 各層の `recipes/<名前>/` と RECIPE.md の先頭・フォルダ直下の種類
- sandbox `/files/stat_many` 1 回 — 全レシピの requires の参照先 (`read:` `recipe:`) の有無
- DB 1 回 — 読む人の所属グループ (一覧に出す層と、所属していないグループの参照・関数の警告)

一覧と `load_recipe` は同じ判定を通る (片方だけ直って片方が壊れている、を作らない)。
「壊れている」は frontmatter の不正だけで、層の規則の違反・参照先が無い・見えないは「警告」(読み込めて、通らない要素だけ
実行しない — ken 2026-10-30)。どちらも保存しない — 毎回その場で判定するので、参照先を戻せば警告は消える。

層は個人 → 所属グループ (名前順) → 全体。管理者でも所属している分だけ ({{USER_GROUPS}} と同じ規則 —
`user_group_names` のうち今のグループ名の規則を通るもの)。一覧は毎リクエスト走るので、問い合わせは
上の 2 + 1 回に限り、sandbox が遅い・古いときは一覧を空にせず 1 行で知らせる。
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

from nucllei.utils.recipes import (
    GROUPS,
    PUBLIC,
    RECIPE_MD,
    WORKSPACE,
    RecipeRefs,
    RefWarnings,
    assess_recipe,
    broken_line,
    decode_text,
    index_line,
    layer_of_place,
    name_problem,
    read_frontmatter,
    recipe_refs,
    reference_warnings,
    warning_line,
)

log = logging.getLogger(__name__)

RECIPES_OLD_SANDBOX = (
    "(レシピの一覧を取れない: sandbox が古い (レシピの一覧に対応していない))"
)
RECIPES_UNREACHABLE = "(レシピの一覧を取れない: sandbox が応答しない)"
INDEX_TIMEOUT_S = 5


def _uid_role(user) -> tuple[Optional[str], Optional[str]]:
    if isinstance(user, dict):
        return user.get("id") or None, user.get("role")
    return getattr(user, "id", None) or None, getattr(user, "role", None)


def member_groups(user) -> list[str]:
    """読む人の所属グループ (名前順)。{{USER_GROUPS}} / NUCLLEI_USER_GROUPS と同じく管理者でも所属分だけ、
    今のグループ名の規則を通るもの。"""
    from nucllei.utils.ext_acl import is_group_dir_name
    from nucllei.utils.task import user_group_names

    uid, _ = _uid_role(user)
    return sorted(
        {g for g in user_group_names(uid) if is_group_dir_name(g)},
        key=lambda g: (g.casefold(), g),
    )


def member_places(user, groups=None) -> list[str]:
    """一覧に出す層の jail のパス (個人 → 所属グループ → 全体)。"""
    if groups is None:
        groups = member_groups(user)
    return [WORKSPACE] + [f"{GROUPS}/{g}" for g in groups] + [PUBLIC]


async def stat_paths(user, paths) -> dict:
    """参照先の種類 (sandbox `/files/stat_many` 1 回)。取れなければ {} (呼び出し側は「分からない」扱い)。"""
    from nucllei.builtin_tools.sandbox import sandbox_post
    from nucllei.utils.ext_acl import sandbox_ext_mounts

    uid, _ = _uid_role(user)
    if not paths or not uid:
        return {}
    try:
        res = await sandbox_post(
            "/files/stat_many",
            {
                "user_id": uid,
                "paths": sorted(paths),
                "mounts": sandbox_ext_mounts(user),
            },
            timeout_s=INDEX_TIMEOUT_S,
        )
    except Exception as e:
        log.warning(f"[recipes] /files/stat_many failed: {e!r}")
        return {}
    results = res.get("results") if isinstance(res, dict) else None
    return results if isinstance(results, dict) else {}


def _md_of(item: dict) -> Optional[dict]:
    md = item.get("md")
    if not isinstance(md, dict):
        return None
    out = {"type": md.get("type")}
    if md.get("type") == "file":
        try:
            data = base64.b64decode(md.get("head_b64") or "")
        except Exception:
            data = b""
        r = decode_text(data)
        out["text"] = None if r is None else r[0]
    return out


async def user_recipes_text(user) -> str:
    """{{USER_RECIPES}} の中身。0 件なら空文字列。"""
    from nucllei.env import SANDBOX_URL

    if not SANDBOX_URL:
        return ""
    from nucllei.builtin_tools.sandbox import SandboxHTTPError, sandbox_post
    from nucllei.utils.ext_acl import sandbox_ext_mounts

    uid, _ = _uid_role(user)
    if not uid:
        return ""
    started = time.perf_counter()
    groups = member_groups(user)
    places = member_places(user, groups)
    try:
        res = await sandbox_post(
            "/recipes/index",
            {"user_id": uid, "places": places, "mounts": sandbox_ext_mounts(user)},
            timeout_s=INDEX_TIMEOUT_S,
        )
    except SandboxHTTPError as e:
        log.warning(f"[recipes] /recipes/index failed: {e!r}")
        return RECIPES_OLD_SANDBOX if e.status == 404 else RECIPES_UNREACHABLE
    except Exception as e:
        log.warning(f"[recipes] /recipes/index failed: {e!r}")
        return RECIPES_UNREACHABLE

    by_place = {
        p.get("place"): p
        for p in (res.get("places") if isinstance(res, dict) else None) or []
        if isinstance(p, dict)
    }
    found = []  # (folder, md, entries)
    refs = RecipeRefs()
    for place in places:
        layer = layer_of_place(place)
        entry = by_place.get(place) or {}
        if layer is None or not entry.get("exists"):
            continue
        for item in entry.get("recipes") or []:
            name = item.get("name")
            if (
                not isinstance(name, str)
                or name_problem(name)
                or item.get("type") != "dir"
            ):
                continue
            folder = f"{layer.recipes}/{name}"
            md = _md_of(item)
            entries = (
                item.get("entries") if isinstance(item.get("entries"), dict) else None
            )
            found.append((folder, md, entries))
            recipe_refs((md or {}).get("text"), folder, refs, groups)

    # stat が取れなかった参照先はキーが無い = 「分からない」で、警告しない
    # (sandbox の一時的な失敗で警告を出さない — reference_warnings)
    path_types = await stat_paths(user, refs.paths) if refs.paths else {}

    lines = []
    for folder, md, entries in found:
        reasons = assess_recipe(folder, md)
        if reasons:
            lines.append(f"- {folder} — {broken_line(reasons)}")
            continue
        fm, _ = read_frontmatter(md["text"])
        line = index_line(folder, fm.get("description"))
        warns = reference_warnings(
            folder, md["text"], entries, path_types, member_groups=groups
        )
        if warns:
            line += " " + warning_line(warns.reasons())
        lines.append(line)
    took = time.perf_counter() - started
    log.debug(f"[recipes] index for {uid}: {len(lines)} recipes in {took:.3f}s")
    return "\n".join(lines)


async def assess_loaded_recipe(
    user, folder: str, files: dict, skipped: dict
) -> tuple[list[str], RefWarnings]:
    """load_recipe の判定 (一覧と同じ `assess_recipe` / `reference_warnings`)。(壊れている理由, 警告)。
    `files` / `skipped` は `/recipes/folder` の中身。"""
    md_bytes = files.get(RECIPE_MD)
    if md_bytes is None:
        md = (
            {"type": "symlink" if skipped.get(RECIPE_MD) == "symlink" else "other"}
            if RECIPE_MD in skipped
            else None
        )
    else:
        r = decode_text(md_bytes)
        md = {"type": "file", "text": None if r is None else r[0]}
    broken = assess_recipe(folder, md)
    if broken:
        return broken, RefWarnings()
    entries = {rel: "file" for rel in files if "/" not in rel}
    for rel, why in skipped.items():
        if "/" not in rel:
            entries[rel] = "symlink" if why == "symlink" else "other"
    return [], await reference_warnings_for(user, folder, md["text"], entries)


async def reference_warnings_for(
    user, folder: str, md_text: str, entries: Optional[dict]
) -> RefWarnings:
    """読む人 `user` から見た、1 つのレシピの警告 (sandbox 1 回 + DB 1 回 — load_recipe・save / edit)。"""
    groups = member_groups(user)
    refs = RecipeRefs()
    recipe_refs(md_text, folder, refs, groups)
    path_types = await stat_paths(user, refs.paths) if refs.paths else {}
    return reference_warnings(
        folder, md_text, entries, path_types, member_groups=groups
    )
