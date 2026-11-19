# pyright: reportPrivateUsage=false
"""回归测试：在 Python 3.13 的注解求值语义下检查反馈路由可导入，
避免运行时签名引用只在 TYPE_CHECKING 中导入的名称。"""

import ast
import builtins
from pathlib import Path

import pytest

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


# 结构拆分产出的组合根模块清单(随批次递增);守卫先于迁移代码生效
_COMPOSITION_ROOT_MODULES = [
    "kongming/chat/schemas.py",
    "kongming/chat/dependencies.py",
    "kongming/chat/adapters.py",
    "kongming/chat/session_service.py",
    "kongming/task/schemas.py",
    "kongming/task/dependencies.py",
    "kongming/task/adapters.py",
    "kongming/task/dispatch.py",
    "kongming/rag/schemas.py",
    "kongming/rag/dependencies.py",
    "kongming/rag/adapters.py",
    "kongming/rag/admin_service.py",
    "kongming/trigger/schemas.py",
    "kongming/trigger/dependencies.py",
    "kongming/trigger/runners.py",
    "kongming/bot/schemas.py",
    "kongming/bot/dependencies.py",
    "kongming/bot/adapters.py",
    "kongming/toolcall/schemas.py",
    "kongming/toolcall/dependencies.py",
    "kongming/toolcall/adapters.py",
    "kongming/billing/schemas.py",
    "kongming/billing/dependencies.py",
    "kongming/billing/adapters.py",
]


def _defined_names(tree: ast.Module) -> set[str]:
    """模块级定义(类/函数/赋值)提供的运行时名字。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _all_runtime_imports(tree: ast.Module) -> set[str]:
    """全树运行时 import 名(含函数内局部导入;TYPE_CHECKING 块除外)。"""
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            return
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return names


def _names_in(node: ast.AST) -> set[str]:
    """子树内出现的所有 Name 标识符。"""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _signature_eager_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """函数定义期求值位置取用的名字:参数注解、返回注解、装饰器。"""
    a = node.args
    names: set[str] = set()
    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
        if arg is not None and arg.annotation is not None:
            names |= _names_in(arg.annotation)
    if node.returns is not None:
        names |= _names_in(node.returns)
    for d in node.decorator_list:
        names |= _names_in(d)
    return names


def _class_eager_names(node: ast.ClassDef) -> set[str]:
    """类定义期求值位置取用的名字:基类、装饰器、类体字段注解。"""
    names: set[str] = set()
    for b in node.bases:
        names |= _names_in(b)
    for d in node.decorator_list:
        names |= _names_in(d)
    for sub in node.body:
        if isinstance(sub, ast.AnnAssign):
            names |= _names_in(sub.annotation)
    return names


def _eager_names(tree: ast.Module) -> set[str]:
    """定义期/异常期求值位置取用的名字:签名与返回注解、装饰器、基类、except、
    模块与类体字段注解。函数体内局部变量注解不求值(PEP 526),不收。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names |= _signature_eager_names(node)
        elif isinstance(node, ast.ClassDef):
            names |= _class_eager_names(node)
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            names |= _names_in(node.type)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            names |= _names_in(node.annotation)
    return names


@pytest.mark.parametrize("rel", _COMPOSITION_ROOT_MODULES)
def test_composition_root_names_resolve_at_runtime(rel: str) -> None:
    """组合根模块在 3.13 注解求值语义下可导入:求值位置禁现仅 TYPE_CHECKING
    导入的名(070 坑扩展守卫;字符串注解不在此列)。"""
    tree = ast.parse((_ROOT / rel).read_text())
    runtime = _all_runtime_imports(tree) | _defined_names(tree)
    missing = {n for n in _eager_names(tree) if n not in runtime and not hasattr(builtins, n)}
    assert not missing, f"{rel} 求值位置名运行时可解析失败(3.13 会 NameError): {missing}"


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
