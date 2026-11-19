# ADR-001: 技术栈定案 —— Python 双进程后端 + React SPA

日期: 2026-08-16
状态: 已接受(经三轮对抗评审后的 55/45 定案)

## 背景

从零构建企业级 Agent 数据平台(Kongming)。前系统(nl2sql-data-platform)为
Java COLA 单体 + Python Agent 双语言三单元,遗留跨语言契约税。
候选:纯 Java、纯 Python、TS monorepo。

## 决策

> **后端 Python 3.13 双进程 + 前端 React 19 SPA**

- `apps/api` — 控制面:FastAPI + Pydantic v2 + SQLAlchemy 2.0(async)+ asyncpg,
  持有全部凭证,承担 RBAC / 租户 / SQL 防火墙(sqlglot)/ 审计。
- `apps/agent` — Agent 面:LangGraph 编排,零凭证,SQL 执行与 RAG 检索
  一律经控制面受控接口回调。
- `apps/web` — React 19 + Vite + TS + Tailwind v4 + shadcn/ui,纯 SPA(不用 Next.js)。
- 数据层:PostgreSQL 17 + pgvector,Redis 8。

## 核心论据(修正后账本)

1. **可替代性不对称**:契约同步是可购买的能力(生成器 + 漂移门禁,见 ADR-005);
   Agent/RAG/eval 生态深度买不到。产品差异化集中在 Agent 面(41 个能力域中
   超三分之二),押二等舱生态等于长期缴税。
2. **语言统一 ≠ 信任边界统一**:统一的是语言(三语言→两语言,语言税大头已消);
   部署上保持双进程(见 ADR-002)。
3. 前端 React 定案依据:AI 辅助编程语料优势、AI 对话产品 UI 范式在 React 生态、
   完全重写使存量框架资产论失效;内部平台无 SEO 需求,SSR 是净负资产。

## 诚实标注

- 这是 **55/45 的决策**,不是碾压。若未来出现「单语言 monorepo 权重压倒一切
  且接受 Agent 层二等舱」的条件,TS monorepo 回到并列第一。
- 被证据出局的论据:sqlglot 多方言——`datasource/spec.md` 明确 PG-only,
  多方言是未来假设而非产品事实。多方言成为现实需求时,本决策的优势扩大。

## 已否决的替代方案

| 方案                   | 否决理由                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------- |
| 纯 Java                | LLM 生态二等公民;评估体系需整体重造                                                                           |
| TS monorepo            | Agent 编排层生态税未清偿;契约收益可被生成管线替代                                                             |
| Next.js/SSR            | 内部平台无 SEO/营销首屏;复杂度净负                                                                            |
| Litestar(替代 FastAPI) | 内建分层主张可由域包 + import-linter 自建;生态/语料/人才池小众一个数量级,违背本 ADR"生态深度买不到"的首要论据 |
