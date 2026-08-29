"""AgentRagClient httpx 连接复用(013 挂账⑭)直测:实例级 lazy 复用 +
模块级单例工厂;embed/describe 业务语义由 test_rag_admin/test_rag_recall
类级补丁覆盖,此处只锁复用形态。
"""
# pyright: reportPrivateUsage=false

import httpx

import kongming.rag.agent_callback as mod
from kongming.rag.agent_callback import AgentRagClient, get_agent_rag_client


async def test_get_client_lazy_reuse() -> None:
    """同实例两次 _get_client 返回同一 AsyncClient(消每请求 TLS 握手)。"""
    client = AgentRagClient()
    c1 = client._get_client()
    c2 = client._get_client()
    assert isinstance(c1, httpx.AsyncClient)
    assert c1 is c2
    await c1.aclose()  # 测试自建的实例负责关闭,防连接泄漏告警


async def test_get_agent_rag_client_singleton() -> None:
    """模块级工厂进程内单例(组合根连接池跨请求复用的前提)。"""
    mod._singleton = None  # 测试隔离:重置单例槽(单例无业务状态,复位无害)
    a = get_agent_rag_client()
    b = get_agent_rag_client()
    assert a is b
    if a._client is not None:  # 本用例未触发 HTTP,正常为 None;防御式关闭
        await a._client.aclose()
    mod._singleton = None  # 复位,不污染其他用例
