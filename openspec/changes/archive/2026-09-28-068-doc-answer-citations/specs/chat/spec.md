# chat spec delta(068-doc-answer-citations)

## ADDED Requirements

### Requirement: 文档道回答的结构化来源

问答响应与消息历史 SHALL 携带该轮的结构化来源列表(`sources`),每项含引用键、
文档名称快照、段落标识与段落序号。该列表 SHALL **由净化后的回答正文单向投影**
得到:正文里存活的每个引用标记对应一项,一项对应正文里至少一处标记——
SHALL NOT 出现"列表有条目而正文无标注"或"正文有标注而列表查不到"的不一致。

来源列表 SHALL 只包含本轮真实下发过的文档块,SHALL NOT 回查文档表补全
(名称为提问时点快照,保证历史回放在源文档改名或删除后仍可呈现)。

字段 SHALL 为纯增:缺省为空列表,老客户端与历史消息(无该字段)SHALL 按无来源
呈现而不报错,SHALL NOT 影响既有四态终态语义(SUCCESS/REJECTED/FAILED/NO_DATA)。
流式链路的来源 SHALL 随既有终态帧下发,SHALL NOT 为此新增事件类型。

#### Scenario: 来源与正文一致

- GIVEN 一轮文档道回答含两处有效引用标记与一处被净化降级的编造标记
- WHEN 响应组装
- THEN `sources` 恰含两项且与正文存活标记一一对应,被降级的那处不出现在列表中

#### Scenario: 历史回放不依赖源文档现存

- GIVEN 某消息落库后其引用的文档被改名
- WHEN 拉取该会话历史
- THEN 来源条目仍呈现提问时点的名称快照,引用仍可在来源区定位

#### Scenario: SQL 道来源为空

- GIVEN 一轮走 SQL 道成功返回结果
- WHEN 响应组装
- THEN `sources` 为空列表,既有字段与终态语义零变化

#### Scenario: 老客户端向后兼容

- GIVEN 消费方未升级、不识别 `sources`
- WHEN 收流并渲染
- THEN 按既有字段正常呈现,不因新增字段失败
