# Spec Delta

## MODIFIED Requirements

### Requirement: 文档检索(small-to-big + 混合召回)

文档检索 SHALL:子块向量 top-K + 子块全文 top-K,RRF 融合(复用既有融合函数);
部署级启用精排时(见「检索精排配置与降级」),融合后候选池扩量(部署级可配,
缺省 top-20 子块)交精排模型打分,按分数重排序后取父块边界(父块数有界,
缺省 top-5);未启用精排时按 RRF 名次直接截断(与既有行为逐字一致)。按
parent_id 集合回填父块全文;与 schema 通道(table_descriptions)SHALL 物理+
逻辑双隔离(文档命中不进 schema_hint,表说明不进文档段;精排只作用于文档
通道命中集,SHALL NOT 引入跨通道重排)。**文档面的全文分词配置 SHALL 与
表说明全文路共用同一部署配置(单一配置单源):写入与检索写读同配置,
SHALL NOT 出现同库两套分词语义;配置不可用时的 fail-closed 语义、白名单
约束与配置变更后的缓存边界与 rag 全文路同口径。**精排改变命中名次时,
稳定引用键派生 SHALL 保持既有恒定语义(与名次无关)。

#### Scenario: small-to-big 回填

- GIVEN 问句命中某子块
- WHEN 检索执行
- THEN 返回其父块全文(非孤立子块片段),并带 source_ref

#### Scenario: 精排重排序后回填

- GIVEN 部署已启用精排,RRF 融合后候选池含 20 个子块、分布于 8 个父块
- WHEN 检索执行
- THEN 精排按问句对候选池打分,取分数最高的 5 个父块回填全文;
    名次与 RRF 序不同时引用键派生不变

#### Scenario: 未启用精排零回归

- GIVEN 部署未配置精排供应商或 key 为空
- WHEN 检索执行
- THEN 融合排序与父块截断行为与本变更前逐字一致

#### Scenario: 文档全文与表说明全文同配置

- GIVEN 全文检索配置为中文分词配置
- WHEN 上传含中文表说明的文档并以其内容关键词检索
- THEN 文档全文向量按同一分词配置写入与检索,与表说明全文路行为一致

#### Scenario: 文档面无独立配置入口

- GIVEN 管理员检查部署配置
- WHEN 查找文档检索的分词配置
- THEN 不存在独立的第二配置项(与 rag 全文路同一单源)

## ADDED Requirements

### Requirement: 检索精排配置与降级

系统 SHALL 支持经部署级环境变量注册文档检索精排(rerank)供应商:条目
MUST 含出口主机、api_key(仅控制面进程内使用,落库与日志 SHALL 掩码,
任何输出 SHALL NOT 回显明文)与模型名;供应商不在注册表或 key 为空时
精排级 SHALL NOT 生效(与「能力从未存在」不可区分,缺省即此形态)。
精排调用 SHALL 复用统一出站通道语义(出口白名单/超时/错误分类)。精排
服务不可用、超时或报错时 SHALL 降级为 RRF-only 继续完成检索(同
「embedding 不可用降级仅全文路」的先例语义),降级事件 SHALL 计入可
观测(原因可见),SHALL NOT 向用户报错、SHALL NOT 静默无痕。精排分数
阈值 SHALL 为部署级可配项,缺省 0(不过滤);启用阈值时低于阈值的候选
SHALL 被过滤,过滤后命中集为空时按既有「文档未命中」收口语义处理
(knowledge-qa:退化为纯数据答,不引用文档粉饰)。

#### Scenario: 精排不可用降级

- GIVEN 部署已启用精排,精排端点持续超时
- WHEN 知识态问句触发文档检索
- THEN 检索按 RRF-only 完成,回答正常产出,降级原因计入 trace/可观测面

#### Scenario: key 未配置零存在感

- GIVEN 部署未配置精排供应商
- WHEN 检索执行
- THEN 管理面与可观测面不出现精排级配置项痕迹,行为与未引入本机制一致

#### Scenario: 阈值过滤后空命中

- GIVEN 部署启用精排且阈值 >0,某问句全部候选精排分低于阈值
- WHEN 融合态问句走双通道
- THEN 文档通道按零命中处理,退化为纯数据答(零引用),不报错

#### Scenario: 出站白名单约束

- GIVEN 精排供应商出口主机不在部署出站白名单内
- WHEN 配置装载
- THEN 该条目被剔除并告警,精排级不生效(fail-closed 不裸崩)
