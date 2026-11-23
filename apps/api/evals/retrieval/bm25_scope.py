"""评测资料快照核对:对照运行前后比对资料集合、更新时间与块数。

任一变化即作废本次对照(spec「对照期间资料发生变化」);本模块只做
快照与差异,取数由调用方经既有文档管理接口完成。
"""

from collections.abc import Mapping, Sequence


def doc_snapshot(docs: Sequence[Mapping[str, object]]) -> dict[str, tuple[str, int]]:
    """资料列表接口行 → {文档名: (更新时间, 块数)}。"""
    return {
        str(doc["doc_name"]): (str(doc["updated_at"]), int(str(doc["block_count"]))) for doc in docs
    }


def snapshot_diff(
    before: Mapping[str, tuple[str, int]], after: Mapping[str, tuple[str, int]]
) -> list[str]:
    """返回新增、删除或变更的文档名(排序;空列表=资料未变)。"""
    names = set(before) | set(after)
    return sorted(name for name in names if before.get(name) != after.get(name))
