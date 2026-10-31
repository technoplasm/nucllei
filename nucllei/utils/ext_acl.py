"""`/ext` のグループ別 ACL(#152)— 判定はここ 1 か所。

jail のパスと URL は同じ文字列(`/ext/...`)。この module は「このユーザーにそのパスが
rw / ro / 見えない のどれか」を決め、次の全員がそれに従う:

- sandbox tool(`builtin_tools/sandbox.py`)— jail に bind する一覧(`sandbox_ext_mounts`)と、
  write_file / edit_file / remove_file / list_workspace / fetch_from_workspace の可否
- レシピの tool(`builtin_tools/recipe_tools.py`)— save_recipe / edit_recipe は sandbox tool の
  書き先の判定を通る。一覧・ERROR の「直す」案内は `utils/recipe_index.recipe_writable`(これを呼ぶ)
- `/ext` ゲート(`routers/ext.py` の `serve_ext`)— 見えない物は不在と同じ 404
- 参照の resolver(`utils/image_refs.py` の `resolve_url` / `resolve_path` / 橋渡し)—
  URL 取得・feed_image・sandbox のパス ↔ URL
- ファイルブラウザ(#150、`routers/sandbox_files.py`)

| パス | 一般ユーザー | admin |
|---|---|---|
| `/ext/groups/<グループ名>/` | メンバーは rw(書き込みが「指定した人だけ」のグループでは、指定された人だけ rw・他のメンバーは ro)、メンバー以外は見えない | 全部 rw |
| `/ext/groups`(直下の一覧) | 自分が見えるグループだけ(`ext_visible_groups`) | 全グループ |
| `/ext/public/` | ro | rw |
| `/ext/<それ以外>/`(doc 等) | ro | ro |

- admin は role(`user.role == "admin"`)だけで決まる。permissions の項目も sandbox 専用の
  権限も作らない
- グループのディレクトリ名 = グループ名そのまま。メンバー判定は `Groups.get_groups_by_member_id`。
  名前は `utils/group_names.validate_group_name` を通る物だけを扱う(#151 より前に作られた
  `:` 入り等の名前は bind も配信もしない — warning を 1 行出して飛ばす)
- **グループの書き込み**: 所属(`user_ids`)の意味は変えず、グループの属性
  `data.ext = {"write": "members" | "listed", "writers": [user_id, …]}` で書ける人を絞れる。
  無い・`members` はメンバー全員 rw、
  `listed` は `writers` にいるメンバーだけ rw・他のメンバーは ro。`writers` は保存のたびに
  `user_ids` との積に揃える(`normalize_group_data`)うえ、判定でも所属を確かめる — メンバーで
  ない人が `writers` に残っていても何も与えない。これは**グループの属性で、ユーザーの権限
  (permissions)ではない**。編集は admin だけ
  (グループの編集と同じ)
- `ro` は「見える(読める)」の意味。`/ext/<それ以外>` が実際に在るかは `EXT_MOUNTS`(nucllei)/
  sandbox の `/ext` 直下(配備)が決める — ここは per-user の可否だけ
- user が分からない(None)ときは ACL の置き場は全部 `none`(fail closed)。それ以外の置き場は
  従来どおり `ro`(公開範囲は配備が決めた物で per-user ではない)

`user` はどの形でも受ける: `UserModel`(id / role 属性)、tool の `__user__` dict
(`{"id", "role", …}`)、user id の文字列(role 不明 = 一般ユーザー扱い)、None。
"""

import logging
import unicodedata
from typing import Literal, Optional

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


# グループの書き込み (data.ext.write)。無い = WRITE_MEMBERS
EXT_DATA_KEY = "ext"
WRITE_MEMBERS = "members"
WRITE_LISTED = "listed"
WRITE_MODES = (WRITE_MEMBERS, WRITE_LISTED)


def _ext_setting(group) -> dict:
    data = getattr(group, "data", None)
    ext = data.get(EXT_DATA_KEY) if isinstance(data, dict) else None
    return ext if isinstance(ext, dict) else {}


def group_write_mode(group) -> str:
    """グループの書き込みの形。無い・null は `members`。知らない値は `listed`
    (書ける人を広げない側 — 保存時の検証を通らない値は DB を直接触ったときにしか入らない)。"""
    mode = _ext_setting(group).get("write")
    if mode is None or mode == WRITE_MEMBERS:
        return WRITE_MEMBERS
    if mode != WRITE_LISTED:
        log.warning(
            f"ext_acl: group {getattr(group, 'id', '?')} の data.ext.write {mode!r} は"
            " 知らない値 — 指定した人だけ書ける扱いにする"
        )
    return WRITE_LISTED


def group_writers(group) -> list[str]:
    """`listed` で書ける人 (`writers` のうち今もメンバーの人。保存された順)。"""
    raw = _ext_setting(group).get("writers")
    members = set(getattr(group, "user_ids", None) or [])
    out: list[str] = []
    for uid in raw if isinstance(raw, list) else []:
        if isinstance(uid, str) and uid in members and uid not in out:
            out.append(uid)
    return out


def can_write_group(group, user_id: str) -> bool:
    """メンバー `user_id` がグループの置き場に書けるか (admin かどうかは見ない)。
    メンバーでなければ False (`writers` に残っていても)。"""
    if not user_id or user_id not in (getattr(group, "user_ids", None) or []):
        return False
    if group_write_mode(group) == WRITE_MEMBERS:
        return True
    return user_id in group_writers(group)


def normalize_group_data(data, user_ids) -> Optional[dict]:
    """保存する `data` の `ext` を揃える: `write` は `members` / `listed` (null は `members`、知らない値と
    dict でない `ext` は `listed` — 判定の `group_write_mode` と同じく書ける人を広げない側)、
    `writers` は `user_ids` との積 (順序は保つ・重複なし・文字列だけ)。`ext` 以外のキーはそのまま。
    `ext` が無ければ触らない。"""
    if not isinstance(data, dict) or EXT_DATA_KEY not in data:
        return data
    ext = data.get(EXT_DATA_KEY)
    if not isinstance(ext, dict):
        return {**data, EXT_DATA_KEY: {"write": WRITE_LISTED, "writers": []}}
    mode = ext.get("write")
    if mode is None:
        mode = WRITE_MEMBERS
    elif mode not in WRITE_MODES:
        mode = WRITE_LISTED
    members = set(user_ids or [])
    writers: list[str] = []
    raw = ext.get("writers")
    for uid in raw if isinstance(raw, list) else []:
        if isinstance(uid, str) and uid in members and uid not in writers:
            writers.append(uid)
    return {
        **data,
        EXT_DATA_KEY: {
            "write": mode,
            "writers": writers,
        },
    }


def _valid_groups(groups, label: str) -> list:
    """ディレクトリ名にできる名前のグループだけ (できない名前は warning を 1 行出して飛ばす)。"""
    out = []
    for g in groups:
        name = getattr(g, "name", None)
        if is_group_dir_name(name):
            out.append(g)
        else:
            try:
                validate_group_name(name)
                why = "前後に空白がある"
            except GroupNameError as e:
                why = str(e)
            log.warning(
                f"ext_acl: group {getattr(g, 'id', '?')} の名前 {name!r} は"
                f" /ext/groups のディレクトリ名にできないので{label}しない"
                f"(現行のグループ名の規則に違反: {why} — 管理画面で改名すれば有効になる)"
            )
    return out


def _group_names(groups, label: str) -> list[str]:
    return sorted({g.name for g in _valid_groups(groups, label)})


def member_group_access(user_id: str) -> dict[str, Access]:
    """user が所属するグループの(ディレクトリ名として使える)名前 -> `rw` / `ro`(admin かどうかは
    見ない — 書き込みの形と `writers` だけ)。同じ名前のグループが複数あれば (#151 より前の DB)、
    どれかで書ければ rw。"""
    from nucllei.models.groups import Groups

    out: dict[str, Access] = {}
    for g in _valid_groups(
        [
            g
            for g in Groups.get_groups_by_member_id(user_id)
            # get_groups_by_member_id は文字列 LIKE なので、実際の所属で確かめ直す
            if user_id in (g.user_ids or [])
        ],
        "bind / 配信",
    ):
        if out.get(g.name) != "rw":
            out[g.name] = "rw" if can_write_group(g, user_id) else "ro"
    return dict(sorted(out.items()))


def member_group_names(user_id: str) -> list[str]:
    """user が所属するグループの(ディレクトリ名として使える)名前。"""
    return list(member_group_access(user_id))


def all_group_names() -> list[str]:
    from nucllei.models.groups import Groups

    return _group_names(Groups.get_groups(), "bind / 配信")


def group_write_mode_by_name(name: str) -> Optional[str]:
    """名前 `name` のグループの書き込みの形 (無ければ None)。同名が複数あれば (#151 より前の DB)
    どれかが `members` なら `members`。案内の文 (誰が直せるか) にだけ使う — 可否は `ext_access`。"""
    from nucllei.models.groups import Groups

    modes = {group_write_mode(g) for g in Groups.get_groups() if g.name == name}
    if not modes:
        return None
    return WRITE_MEMBERS if WRITE_MEMBERS in modes else WRITE_LISTED


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

    - `groups/<g>/…`: admin → rw、`<g>` のメンバー → rw(`listed` のグループで `writers` に
      いないメンバーは ro)、それ以外 → none
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
    return member_group_access(uid).get(group, "none")


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
    {"path": "/ext/groups/<g>", "rw": bool}, …]`。見えない物は入れない(= jail に無い)。
    グループの rw は `ext_access` と同じ(admin は全部 rw、メンバーは書き込みの形と `writers`
    次第 — ro の bind なら jail の中の run_* も書けない)。user 不明は []。"""
    uid, role = _uid_role(user)
    if not uid:
        return []
    admin = role == "admin"
    mounts = [{"path": f"{EXT_ROOT}/{PUBLIC}", "rw": admin}]
    if admin:
        groups = [(g, True) for g in all_group_names()]
    else:
        groups = [(g, a == "rw") for g, a in member_group_access(uid).items()]
    mounts += [{"path": f"{EXT_ROOT}/{GROUPS}/{g}", "rw": rw} for g, rw in groups]
    return mounts
