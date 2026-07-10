from typing import Optional, Union, List, Dict, Any
from open_webui.models.users import Users, UserModel
from open_webui.models.groups import Groups


from open_webui.config import DEFAULT_USER_PERMISSIONS
import json


def fill_missing_permissions(
    permissions: Dict[str, Any], default_permissions: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Recursively fills in missing properties in the permissions dictionary
    using the default permissions as a template.
    """
    for key, value in default_permissions.items():
        if key not in permissions:
            permissions[key] = value
        elif isinstance(value, dict) and isinstance(
            permissions[key], dict
        ):  # Both are nested dictionaries
            permissions[key] = fill_missing_permissions(permissions[key], value)

    return permissions


def get_permissions(
    user_id: str,
    default_permissions: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Get all permissions for a user by combining the permissions of all groups the user is a member of.
    If a permission is defined in multiple groups, the most permissive value is used (True > False).
    Permissions are nested in a dict with the permission key as the key and a boolean as the value.
    """

    def combine_permissions(
        permissions: Dict[str, Any], group_permissions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Combine permissions from multiple groups by taking the most permissive value."""
        for key, value in group_permissions.items():
            if isinstance(value, dict):
                if key not in permissions:
                    permissions[key] = {}
                permissions[key] = combine_permissions(permissions[key], value)
            else:
                if key not in permissions:
                    permissions[key] = value
                else:
                    permissions[key] = (
                        permissions[key] or value
                    )  # Use the most permissive value (True > False)
        return permissions

    user_groups = Groups.get_groups_by_member_id(user_id)

    # Deep copy default permissions to avoid modifying the original dict
    permissions = json.loads(json.dumps(default_permissions))

    # Combine permissions from all user groups
    for group in user_groups:
        group_permissions = group.permissions
        permissions = combine_permissions(permissions, group_permissions)

    # Ensure all fields from default_permissions are present and filled in
    permissions = fill_missing_permissions(permissions, default_permissions)

    return permissions


def has_permission(
    user_id: str,
    permission_key: str,
    default_permissions: Dict[str, Any] = {},
) -> bool:
    """
    Check if a user has a specific permission by checking the group permissions
    and fall back to default permissions if not found in any group.

    Permission keys can be hierarchical and separated by dots ('.').
    """

    def get_permission(permissions: Dict[str, Any], keys: List[str]) -> bool:
        """Traverse permissions dict using a list of keys (from dot-split permission_key)."""
        for key in keys:
            if key not in permissions:
                return False  # If any part of the hierarchy is missing, deny access
            permissions = permissions[key]  # Traverse one level deeper

        return bool(permissions)  # Return the boolean at the final level

    permission_hierarchy = permission_key.split(".")

    # Retrieve user group permissions
    user_groups = Groups.get_groups_by_member_id(user_id)

    for group in user_groups:
        group_permissions = group.permissions
        if get_permission(group_permissions, permission_hierarchy):
            return True

    # Check default permissions afterward if the group permissions don't allow it
    default_permissions = fill_missing_permissions(
        default_permissions, DEFAULT_USER_PERMISSIONS
    )
    return get_permission(default_permissions, permission_hierarchy)


def _matches_group_terms(
    user_group_ids: List[str], group_terms: List[List[str]]
) -> bool:
    """llens 拡張: 交差(AND)条件の判定。

    group_terms は DNF(選言標準形)。項(term)内の group id は AND、
    項同士は OR で評価する。例:「(内科 AND 部長) OR (外科 AND 部長)」は
    [[dept_naika, rank_bucho], [dept_geka, rank_bucho]]。
    空の term は誤って全許可にならないよう不成立として扱う。
    """
    user_group_set = set(user_group_ids)
    return any(term and user_group_set.issuperset(term) for term in group_terms)


def has_access(
    user_id: str,
    type: str = "write",
    access_control: Optional[dict] = None,
) -> bool:
    if access_control is None:
        return type == "read"

    user_groups = Groups.get_groups_by_member_id(user_id)
    user_group_ids = [group.id for group in user_groups]
    permission_access = access_control.get(type, {})
    permitted_group_ids = permission_access.get("group_ids", [])
    permitted_user_ids = permission_access.get("user_ids", [])
    # llens 拡張(_memos/group-intersection-design.md): 診療科×職位の交差条件
    permitted_group_terms = permission_access.get("group_terms", [])

    return (
        user_id in permitted_user_ids
        or any(group_id in permitted_group_ids for group_id in user_group_ids)
        or _matches_group_terms(user_group_ids, permitted_group_terms)
    )


def has_read_access_to_file(file_id: str, user: "UserModel") -> bool:
    """
    llens(#40, GHSA-vjqm/vrhc/4g37): file_id 単位の読取権限突合。
    要求ユーザーが 所有 / 管理者 / 共有 public マーカー(#41)/ file 自身の
    access_control(read)のいずれかを満たす場合のみ True。存在しない file は
    False(非公開扱い)。循環 import 回避のため Files は関数内で遅延 import する。
    """
    if not file_id:
        return False
    if getattr(user, "role", None) == "admin":
        return True

    from open_webui.models.files import Files

    file = Files.get_file_by_id(file_id)
    if not file:
        return False
    if file.user_id == user.id:
        return True
    # llens(#41): 共有チャットの public マーカー。チャット share 時に
    # {"read": {"public": true}} が焼かれ、認証ユーザー全員が read 可になる。
    # 共有解除では戻さない — 狭める操作はファイル管理機構(#69)の明示操作のみ
    # (_memos/chat-share-files.md)。
    if ((file.access_control or {}).get("read") or {}).get("public") is True:
        return True
    # 注意: file の access_control=None は「所有者のみ(非公開)」を意味する。
    # has_access は None を public-read として扱うため、明示設定時のみ委譲する
    # (アップロード既定は None = 非公開。共有は明示的に user/group を列挙したときだけ)。
    if file.access_control is not None and has_access(
        user.id, "read", file.access_control
    ):
        return True
    return False


def has_read_access_to_collection(collection_name: str, user: "UserModel") -> bool:
    """
    llens(#40): collection 参照の読取権限突合。
    `file-<id>` 形式のコレクションは対応する file の権限へ委譲する。KB は #52 で撤去済み
    (テーブルも #41 で drop)のため、それ以外のコレクションは常に False(非公開扱い)。
    """
    if not collection_name:
        return False
    if getattr(user, "role", None) == "admin":
        return True

    if collection_name.startswith("file-"):
        return has_read_access_to_file(collection_name[len("file-") :], user)

    return False


# Get all users with access to a resource
def get_users_with_access(
    type: str = "write", access_control: Optional[dict] = None
) -> List[UserModel]:
    if access_control is None:
        return Users.get_users()

    permission_access = access_control.get(type, {})
    permitted_group_ids = permission_access.get("group_ids", [])
    permitted_user_ids = permission_access.get("user_ids", [])
    permitted_group_terms = permission_access.get("group_terms", [])

    user_ids_with_access = set(permitted_user_ids)

    for group_id in permitted_group_ids:
        group_user_ids = Groups.get_group_user_ids_by_id(group_id)
        if group_user_ids:
            user_ids_with_access.update(group_user_ids)

    # llens 拡張: 交差条件は「term 内の全 group に所属するユーザー」の積集合
    for term in permitted_group_terms:
        if not term:
            continue
        member_sets = []
        for group_id in term:
            group_user_ids = Groups.get_group_user_ids_by_id(group_id)
            member_sets.append(set(group_user_ids or []))
        if member_sets:
            user_ids_with_access.update(set.intersection(*member_sets))

    return Users.get_users_by_user_ids(list(user_ids_with_access))
