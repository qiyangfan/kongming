"""067:agent 面只读根与 liannu 单源恒等(禁止字面值拷贝)。"""

from liannu.rules import READONLY_ROOTS

from kongming_agent import sqlguard


def test_readonly_roots_is_liannu_object() -> None:
    # 模块内 _READONLY_ROOTS 与公开导入同源(067:禁止第二处字面值)
    assert sqlguard._READONLY_ROOTS is READONLY_ROOTS  # pyright: ignore[reportPrivateUsage]
