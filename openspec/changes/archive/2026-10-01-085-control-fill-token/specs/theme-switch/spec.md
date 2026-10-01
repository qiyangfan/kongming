## ADDED Requirements

### Requirement: 控件填充令牌与控件边界令牌分离

表单控件的**底/轨道填充**必须由独立令牌 `--input-fill`（muniu 侧 `--color-input-fill`）提供，禁止 (MUST NOT) 消费控件边界令牌 `--input`（`--color-input`）——同一个 token 同时承担「描边」与「填充」两个语义时，任何为达标对比度而做的描边调整都会连带把未勾选开关轨道、输入框内底一起提亮，构成未被要求的外观变更。控件填充的取值与描边比值互不约束：`--input` 继续按「对所在面 ≥3:1」定值，`--input-fill` 按控件底/轨道的中性面定值。三仓现值：kongming/jinguan 亮 `#dfe3de` 暗 `#2a363a`；muniu 亮 `#e2e2de` 暗 `#2b302a`。标准件内出现裸 `bg-input`（非 `bg-input-fill`）视为回归。

#### Scenario: 调整控件边界不牵连填充

- GIVEN 某仓为提高控件可辨识性而修改 `--input` 的取值
- WHEN 检查 `ui/` 标准件里控件内底与开关轨道的类名
- THEN 相关类均为 `bg-input-fill*`，其计算色仍等于 `--input-fill` 的取值，与本次描边调整无关

#### Scenario: 未勾选开关轨道不呈现为激活态

- GIVEN 应用处于亮色主题，页面存在 `Switch` 的未勾选态（`data-state=unchecked`）
- WHEN 读取该轨道的计算背景色
- THEN 取值为 `--input-fill` 的亮色值（kongming/jinguan `#dfe3de`），不随控件边界令牌变深

#### Scenario: 填充令牌在两态均有独立取值

- GIVEN 三仓任一仓的 `index.css`
- WHEN 检查亮色块与暗色块
- THEN 两处都各自声明了 `--input-fill`（或 `--color-input-fill`），且 `@theme` 生成的 `bg-input-fill*` 工具类在构建产物中解析到该变量，不存在类未生成导致底色丢失
