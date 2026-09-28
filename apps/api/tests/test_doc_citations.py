"""068-doc-answer-citations api 面测试:键派生恒定/异构/改名稳定(控制面纯函数)。"""

from kongming.rag.documents_service import derive_citation_key


def test_same_parent_constant() -> None:
    """任务 1.1:同一父块任意次数派生恒定。"""
    k1 = derive_citation_key(parent_id="p-1", doc_name="员工手册.docx")
    k2 = derive_citation_key(parent_id="p-1", doc_name="员工手册.docx")
    assert k1 == k2


def test_different_parents_differ() -> None:
    """任务 1.1:不同父块派生不同键。"""
    a = derive_citation_key(parent_id="p-1", doc_name="员工手册.docx")
    b = derive_citation_key(parent_id="p-2", doc_name="员工手册.docx")
    assert a != b


def test_rename_stable_suffix() -> None:
    """任务 1.1:改名/重切块不改变哈希后缀段(历史引用不腐坏)。"""
    a = derive_citation_key(parent_id="p-1", doc_name="员工手册.docx")
    b = derive_citation_key(parent_id="p-1", doc_name="员工手册v2.pdf")
    assert a.rpartition("-")[2] == b.rpartition("-")[2]


def test_prefix_readable() -> None:
    """可读前缀=文档名去扩展名。"""
    assert derive_citation_key(parent_id="p-1", doc_name="运维手册.md").startswith("运维手册-")
    assert derive_citation_key(parent_id="p-1", doc_name="无扩展名").startswith("无扩展名-")


def test_derivation_independent_of_order() -> None:
    """任务 1.1:派生不依赖命中次序与路数(乱序输入 → 键集合相同)。"""
    parents = [("p-1", "A.md"), ("p-2", "B.md"), ("p-3", "C.md")]
    forward = {derive_citation_key(parent_id=p, doc_name=d) for p, d in parents}
    backward = {derive_citation_key(parent_id=p, doc_name=d) for p, d in reversed(parents)}
    assert forward == backward
