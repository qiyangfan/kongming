"""team 领域模型(零框架):可见性判定纯函数(全部列表/直访/
运行/编辑判定收于此,单点防漂移)。

角色序:VIEWER(1) < MEMBER(2) < OWNER(3);None=非成员。
"""

import re

TEAM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

ROLES = ("VIEWER", "MEMBER", "OWNER")
_ROLE_RANK = {"VIEWER": 1, "MEMBER": 2, "OWNER": 3}


class TeamError(Exception):
    """领域层错误基类。"""


class TeamValidationError(TeamError):
    """400(key/名称/角色形态)。"""


class TeamNotFound(TeamError):
    """404(不存在/跨租户/非成员——同形)。"""


class TeamForbidden(TeamError):
    """403(成员但角色不足)。"""


def visible(*, team_id: str | None, creator_id: str, user_id: str, member_role: str | None) -> bool:
    """可见性:个人资源仅创建者;团队资源成员即可见(角色不分)。"""
    if creator_id == user_id:
        return True
    if team_id is None:
        return False
    return member_role in ROLES


def can_edit(
    *, team_id: str | None, creator_id: str, user_id: str, member_role: str | None
) -> bool:
    """编辑(新版本/启停/改定义):创建者或 MEMBER+。"""
    if creator_id == user_id:
        return True
    if team_id is None:
        return False
    return _ROLE_RANK.get(member_role or "", 0) >= _ROLE_RANK["MEMBER"]


def can_manage(
    *, team_id: str | None, creator_id: str, user_id: str, member_role: str | None
) -> bool:
    """全权(删除/归属变更/成员管理):创建者或 OWNER。"""
    if creator_id == user_id:
        return True
    if team_id is None:
        return False
    return member_role == "OWNER"
