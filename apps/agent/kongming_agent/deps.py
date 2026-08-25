"""运行期依赖工厂(LLM/控制面客户端)。

刻意不做单例缓存:每次调用新建客户端——规避跨事件循环绑定的坑
(测试面 function 级 loop 与运行面共存),实例化开销在本链路可忽略。
测试经 monkeypatch 替换本模块的 get_llm/get_api 注入替身。
"""

from kongming_agent.apiclient import ApiClient
from kongming_agent.llm import LlmClient
from kongming_agent.settings import Settings


def get_llm() -> LlmClient:
    settings = Settings()
    # 021:usage 上报通道装配(fire-and-forget,主链路零阻塞)
    return LlmClient(settings, usage_reporter=ApiClient(settings).report_usage)


def get_api() -> ApiClient:
    return ApiClient(Settings())
