"""`/ext` のグループ別 ACL(#152)— 判定はここ 1 か所。

jail のパスと URL は同じ文字列(`/ext/...`)。この module は「このユーザーにそのパスが
rw / ro / 見えない のどれか」を決め、次の全員がそれに従う:

- sandbox tool(`builtin_tools/sandbox.py`)— jail に bind する一覧(`sandbox_ext_mounts`)と、
  write_file / list_workspace / fetch_from_workspace の可否
- `/ext` ゲート(`routers/ext.py` の `serve_ext`)— 見えない物は不在と同じ 404
- 参照の resolver(`utils/image_refs.py` の `resolve_url` / `resolve_path` / 橋渡し)—
  URL 取得・feed_image・sandbox のパス ↔ URL
- ファイルブラウザ(#150、後続)

| パス | 一般ユーザー | admin |
|---|---|---|
| `/ext/groups/<グループ名>/` | メンバーは rw、それ以外は見えない | 全部 rw |
| `/ext/groups`(直下の一覧) | 自分が見えるグループだけ(`ext_visible_groups`) | 全グループ |
| `/ext/public/` | ro | rw |
| `/ext/<それ以外>/`(doc 等) | ro | ro |

- admin は role(`user.role == "admin"`)だけで決まる。permissions の項目も sandbox 専用の
  権限も作らない
- グループのディレクトリ名 = グループ名そのまま。メンバー判定は `Groups.get_groups_by_member_id`。
  名前は `utils/group_names.validate_group_name` を通る物だけを扱う(#151 より前に作られた
  `:` 入り等の名前は bind も配信もしない — warning を 1 行出して飛ばす)
- `ro` は「見える(読める)」の意味。`/ext/<それ以外>` が実際に在るかは `EXT_MOUNTS`(nucllei)/
  sandbox の `/ext` 直下(配備)が決める — ここは per-user の可否だけ
- user が分からない(None)ときは ACL の置き場は全部 `none`(fail closed)。それ以外の置き場は
  従来どおり `ro`(公開範囲は配備が決めた物で per-user ではない)

`user` はどの形でも受ける: `UserModel`(id / role 属性)、tool の `__user__` dict
(`{"id", "role", …}`)、user id の文字列(role 不明 = 一般ユーザー扱い)、None。
"""

import logging
import unicodedata
from typing import Literal

from nucllei.utils.group_names import GroupNameError, validate_group_name

log = logging.getLogger(__name__)

Access = Literal["rw", "ro", "none"]

# `/ext/<name>` のうち ACL の掛かる置き場。sandbox 側(`sandbox/app/main.py` の
# `REQUEST_MOUNT_ROOTS`)と同じ名前 — ここに挙げた名前は sandbox の自動 read-only bind から
# 外れ、リクエストごとに `mounts` で渡した物だけが jail に出る。
GROUPS = "groups"
PUBLIC = "public"
ACL_ROOTS = frozenset({GROUPS, PUBLIC})

EXT_ROOT = "/ext"


def _uid_role(user) -> tuple[str | None, str | None]:
    if user is None:
        return None, None
    if isinstance(user, str):
        return (user or None), None
    if isinstance(user, dict):
        return user.get("id") or None, user.get("role")
    return getattr(user, "id", None) or None, getattr(user, "role", None)


def is_admin(user) -> bool:
    return _uid_role(user)[1] == "admin"


def is_group_dir_name(name) -> bool:
    """`name` を `/ext/groups/<name>` のディレクトリ名として扱ってよいか。

    規則は #151 の `validate_group_name`(作成・改名時の検証)そのもの — 通らない名前と、
    検証が名前を変える(前後の空白を落とす)名前はパスにしない。"""
    if not isinstance(name, str) or not name:
        return False
    try:
        return validate_group_name(name) == name
    except GroupNameError:
        return False


def is_safe_path_segment(seg) -> bool:
    """ファイル・ディレクトリ名の 1 セグメントとして安全か(グループの置き場の中のパス用)。
    空・`.` `..`・`/` `\\`・制御文字(Cc)は不可。"""
    if not isinstance(seg, str) or seg in ("", ".", ".."):
        return False
    if "/" in seg or "\\" in seg:
        return False
    return not any(unicodedata.category(c) == "Cc" for c in seg)


def _group_names(groups, label: str) -> list[str]:
    names = []
    for g in groups:
        name = getattr(g, "name", None)
        if is_group_dir_name(name):
            names.append(name)
        else:
            log.warning(
                f"ext_acl: group {getattr(g, 'id', '?')} の名前 {name!r} は"
                f" /ext/groups のディレクトリ名にできないので{label}しない"
            )
    return sorted(set(names))


def member_group_names(user_id: str) -> list[str]:
    """user が所属するグループの(ディレクトリ名として使える)名前。"""
    from nucllei.models.groups import Groups

    groups = [
        g
        for g in Groups.get_groups_by_member_id(user_id)
        # get_groups_by_member_id は文字列 LIKE なので、実際の所属で確かめ直す
        if user_id in (g.user_ids or [])
    ]
    return _group_names(groups, "bind / 配信")


def all_group_names() -> list[str]:
    from nucllei.models.groups import Groups

    return _group_names(Groups.get_groups(), "bind / 配信")


def ext_visible_groups(user) -> list[str]:
    """`/ext/groups` の下でこの user に見えるグループ名(名前順)。admin は全グループ、
    一般ユーザーは所属グループ、user 不明は []。直下の一覧(ファイルブラウザ等)はこれで出す。"""
    uid, role = _uid_role(user)
    if not uid:
        return []
    if role == "admin":
        return all_group_names()
    return member_group_names(uid)


def ext_access(user, rel: str) -> Access:
    """`/ext/` 以降のパス `rel`(decode 済み。`"groups/EMR/a.md"`・`"public"`・`"doc/x"`)を
    この user が rw / ro / 見えない(none)のどれで扱えるか。

    - `groups/<g>/…`: admin → rw、`<g>` のメンバー → rw、それ以外 → none
    - `groups` そのもの(`groups` / `groups/`)→ none(直下の一覧は `ext_visible_groups`)
    - `public/…`: admin → rw、他 → ro
    - それ以外の name: ro(user 不明でも ro — 公開範囲は配備の `EXT_MOUNTS` が決める)
    - ACL の置き場で user 不明・`..` 等を含む → none

    呼び手は traversal を先に弾いていること(ここでも `..` 等は none にする)。
    """
    parts = (rel or "").lstrip("/").split("/")
    name = parts[0]
    if name not in ACL_ROOTS:
        return "ro"
    rest = [p for p in parts[1:] if p != ""]
    if any(not is_safe_path_segment(p) for p in rest):
        return "none"
    uid, role = _uid_role(user)
    if not uid:
        return "none"
    admin = role == "admin"
    if name == PUBLIC:
        return "rw" if admin else "ro"
    if not rest:
        return "none"
    group = rest[0]
    if not is_group_dir_name(group):
        log.warning(
            f"ext_acl: {group!r} はグループのディレクトリ名にできない — 見せない"
        )
        return "none"
    if admin:
        return "rw"
    return "rw" if group in member_group_names(uid) else "none"


def ext_path_access(user, path: str) -> Access | None:
    """jail / URL の絶対パス `/ext/...` 版。`/ext` 配下でなければ None。"""
    if path == EXT_ROOT:
        return None
    if not path.startswith(EXT_ROOT + "/"):
        return None
    return ext_access(user, path[len(EXT_ROOT) + 1 :])


def is_acl_rel(rel: str) -> bool:
    """`/ext/` 以降のパスが ACL の置き場(`groups` / `public`)のものか。"""
    return (rel or "").lstrip("/").split("/", 1)[0] in ACL_ROOTS


def confine_depth(name: str) -> int:
    """静的配信の閉じ込めの基準が mount の root から何段下か。`groups` は 1
    (`<root>/<グループ名>` の中に閉じる — 中の symlink で他グループへ出させない)、他は 0。"""
    return 1 if name == GROUPS else 0


def sandbox_ext_mounts(user) -> list[dict]:
    """sandbox の `mounts`(jail に bind する一覧)。`[{"path": "/ext/public", "rw": bool},
    {"path": "/ext/groups/<g>", "rw": True}, …]`。見えない物は入れない(= jail に無い)。
    user 不明は []。"""
    uid, role = _uid_role(user)
    if not uid:
        return []
    mounts = [{"path": f"{EXT_ROOT}/{PUBLIC}", "rw": role == "admin"}]
    mounts += [
        {"path": f"{EXT_ROOT}/{GROUPS}/{g}", "rw": True}
        for g in ext_visible_groups(user)
    ]
    return mounts
