# rag 规格增量:上传文档名 PII 防线

## MODIFIED Requirements

### Requirement: 文档上传与索引

上传 Markdown 表说明(`## 表名` 二级标题切块),生成 embedding(1536 维)后
幂等写入 rag.table_descriptions;重复上传覆盖更新(upsert);无权限或超限被拒。
端点为 `POST /system/rag/documents/upload`(multipart),要求 system:rag:manage
权限 + 租户上下文(与 chat 域同构的 X-Tenant-Id 白名单复校);datasource_key
SHALL 通过存在+启用+归属当前租户校验;表单 schema_name SHALL 通过 PG 标识符
校验(`^[a-z_][a-z0-9_]{0,62}$`,与 datasource 域 default_schema 同一规则);
文档标题解析出的表名/schema SHALL 小写折叠(PG 未加引号标识符语义)。
上传文件名(doc_name)SHALL 过 PII 检测(护栏 pii_* 规则集),命中敏感信息
(身份证号/手机号等)即 400 拒绝——拒绝优于脱敏(脱敏后名可能撞
uq_documents_tdq 唯一键);数字命名的正常文件(订单号等)按既有 bankcard
19/20 前缀排除口径不误拒。
知识条目以 (tenant_id, datasource_key, schema_name, table_name) 四元唯一
(baseline 既定形态,与召回过滤维度对齐)。上传来源 SHALL 标记
source_type=MANUAL_UPLOAD;embedding 输入文本 SHALL 为「表名 + 表说明 +
字段说明原文」的确定性拼接。支持扩展名:.md/.markdown/.txt(文本链路)。
上传解析 SHALL 在表块数上限(默认 500)内——超限拒绝,防大文档切块风暴;
上传属管理写操作,SHALL 落 sys_oper_log 审计(与业务同事务)。

#### Scenario: 上传表说明 Markdown 成功索引

- GIVEN 用户已登录,角色有 system:rag:manage 权限,当前租户为 sakila
- WHEN 上传含「## customer」表说明的 Markdown
- THEN 按 `##` 切分解析出表块,生成向量写入 rag.table_descriptions(tenant/datasource/schema/table 四维)
- AND 返回 200 + 落库表名列表

#### Scenario: 同表重复上传覆盖更新(幂等)

- GIVEN table_descriptions 已有 customer 表记录(同租户同数据源同 schema)
- WHEN 再次上传含 customer 的文档
- THEN 触发唯一约束后改走 UPDATE,更新说明/示例/向量/全文索引/时间戳,不产生重复行

#### Scenario: 无权限用户上传被拒

- GIVEN 用户角色无 system:rag:manage
- WHEN 上传
- THEN 返回 403

#### Scenario: 上传内容过大被拒

- GIVEN 上传文本类文档 > 1MB(配置上限)
- WHEN 上传
- THEN 返回 400(文档过大)

#### Scenario: 表块数超限被拒

- GIVEN 上传文档解析出超过表块数上限(默认 500)的 `##` 表块
- WHEN 上传
- THEN 返回 400(表块数超限),不写入知识库,不触发 embedding

#### Scenario: 上传对 datasource_key 校验 fail-closed

- GIVEN 上传请求携带的 datasource_key 不存在/已禁用/不归属当前租户
- WHEN 上传
- THEN 拒绝(同形,不区分具体原因,防 key 探测)

#### Scenario: schema_name 非法标识符被拒

- GIVEN 上传表单 schema_name 含大写/引号/空白等不匹配 `^[a-z_][a-z0-9_]{0,62}$` 的形态
- WHEN 上传
- THEN 返回 400,不落库(防畸形值直达 LLM 提示层)

#### Scenario: 表名小写折叠幂等

- GIVEN 先后两次上传 `## Customer` 与 `## customer` 表块(其余内容一致)
- WHEN 落库
- THEN 解析为同一表名,第二次走 UPDATE 覆盖,不产生重复行

#### Scenario: 切块解析失败被拒

- GIVEN 上传的 Markdown 不含任何 `## 表名` 形态二级标题
- WHEN 解析
- THEN 返回 400(提示分块格式要求),不写入知识库

#### Scenario: 文件名含 PII 被拒

- GIVEN 上传文件名为「王五_11010119900101123X_简历.md」
- WHEN 上传
- THEN 返回 400(文件名含敏感信息),不落库

#### Scenario: 数字文件名不误拒

- GIVEN 上传文件名为「orders_20260825_export.md」(纯日期/订单号形态数字)
- WHEN 上传
- THEN 正常索引(PII 规则不误伤)
