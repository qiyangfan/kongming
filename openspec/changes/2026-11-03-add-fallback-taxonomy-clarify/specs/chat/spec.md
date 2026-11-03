# Spec Delta

## ADDED Requirements

### Requirement: 兜底轮持久化与呈现

问答链路的兜底轮(闲聊轮=CHITCHAT,含复语直通命中与语义判定命中两个
来源;澄清引导轮=CLARIFY)SHALL
携带兜底分类标记持久化:`chat_message` SHALL 具 nullable 的
`fallback_type` 诊断列,取值为闭合枚举(`CHITCHAT` / `CLARIFY`)或空
(非兜底轮);取值域 SHALL 以数据库约束收口。兜底轮的状态映射 SHALL
复用既有中性第四态通道(status=NO_DATA、summary 承载中性文案、error
为空、产物卡为空),既有四态(SUCCESS/FAILED/REJECTED/NO_DATA)的
映射与呈现 SHALL 逐字不变。兜底轮的反馈操作(点踩/纠错)SHALL 照常
可用。`fallback_type` SHALL 为管理面诊断字段,SHALL NOT 进入用户响应
与流式终态事件;web 实时映射与历史回显 SHALL 不因该字段存在而变化。

#### Scenario: 复语轮携带分类标记落库

- GIVEN 用户输入纯复语「谢谢」,直通判定命中
- WHEN 该轮终态落库
- THEN 消息 status=NO_DATA、fallback_type=CHITCHAT、summary 为族别
  中性文案、产物卡为空;用户响应与历史回显不携带 fallback_type

#### Scenario: 语义判定闲聊轮携带分类标记落库

- GIVEN 词表漏网的闲聊输入被意图路由帧语义判定命中
- WHEN 该轮终态落库
- THEN 消息 status=NO_DATA、fallback_type=CHITCHAT、summary 为统一
  中性文案、产物卡为空;用户响应与历史回显不携带 fallback_type

#### Scenario: 澄清轮携带分类标记落库

- GIVEN 高置信歧义问句触发澄清早退
- WHEN 该轮终态落库
- THEN 消息 status=NO_DATA、fallback_type=CLARIFY、summary 为澄清
  问句、产物卡为空;呈现为中性说明形态(非错误横幅),反馈可用

#### Scenario: 非兜底轮分类为空

- GIVEN 正常数据问答轮与既有各类轮次
- WHEN 终态落库
- THEN fallback_type 为空,既有行为与呈现逐字不变

#### Scenario: 非法取值拒绝落库

- WHEN 诊断字段出现闭合枚举之外的取值
- THEN 数据库约束拒绝写入(该轮按无分类处理,主链路不受影响)
