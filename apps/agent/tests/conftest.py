"""Agent 测试基建:env 先置(任何 kongming_agent 导入前),防本地 .env 渗入测试面。"""

import os

os.environ.setdefault("KM_AGENT_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("KM_AGENT_LLM_API_KEY", "")
os.environ.setdefault("KM_AGENT_API_BASE_URL", "http://api-unused-in-tests")
# 030:多模型注册表/路由(真值经 .env 渗入会污染路由断言,003 教训同款)
os.environ.setdefault("KM_AGENT_MODEL_REGISTRY_JSON", "")
os.environ.setdefault("KM_AGENT_MODEL_ROUTES_JSON", "")
