# pyright: reportPrivateUsage=false
"""回归测试：在 Python 3.13 的注解求值语义下检查反馈路由可导入，
避免运行时签名引用只在 TYPE_CHECKING 中导入的名称。"""

import ast
import builtins
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _runtime_names(tree: ast.Module) -> set[str]:
    """模块级(含 TYPE_CHECKING 外的)import 提供的运行时名字。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def _type_checking_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom):
                    names.update(a.asname or a.name for a in sub.names)
    return names


def test_no_type_checking_only_names_in_eager_positions() -> None:
    """定义时求值位置(签名/except/基类/装饰器)禁现 TYPE_CHECKING 专有名。"""
    tc_only = _type_checking_names(
        ast.parse((_ROOT / "kongming" / "feedback" / "router.py").read_text())
    )
    assert tc_only == set(), (
        f"feedback/router.py 应为运行时导入(组合根真名);仍残留 TYPE_CHECKING 专有名: {tc_only}"
    )


def test_gate_class_annotations_resolve_at_runtime() -> None:
    """_FeedbackSampleGate.__init__ 签名注解全部可运行解析(3.13 语义)。"""
    import kongming.feedback.router as mod

    src = (_ROOT / "kongming" / "feedback" / "router.py").read_text()
    tree = ast.parse(src)
    runtime = _runtime_names(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_FeedbackSampleGate":
            for sub in ast.walk(node):
                if isinstance(sub, ast.arg) and sub.annotation is not None:
                    names.update(n.id for n in ast.walk(sub.annotation) if isinstance(n, ast.Name))
    missing = {n for n in names if n not in runtime and not hasattr(builtins, n)}
    assert not missing, f"签名注解名运行时可解析失败(3.13 会 NameError): {missing}"
    assert hasattr(mod, "_FeedbackSampleGate")


def test_except_clause_names_resolve_at_runtime() -> None:
    """except 子句取的名必须运行时可解析(EmbeddingError 503 路径)。"""
    import kongming.feedback.router as mod

    src = (_ROOT / "kongming" / "feedback" / "router.py").read_text()
    tree = ast.parse(src)
    runtime = _runtime_names(tree)
    caught = {
        n.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is not None
        for n in ast.walk(node.type)
        if isinstance(n, ast.Name)
    }
    missing = {n for n in caught if n not in runtime and not hasattr(builtins, n)}
    assert not missing, f"except 子句名运行时可解析失败: {missing}"
    assert "EmbeddingError" in caught, "应存在 EmbeddingError 捕获(070 503 路径)"
    # mod 的引用即本测试的存在意义:导入本身成功=3.13 定义时求值不炸
    assert hasattr(mod, "_FeedbackSampleGate")
