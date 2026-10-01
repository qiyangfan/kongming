# theme-switch 能力域

## Purpose

为前端提供亮(宣纸白)/暗(黛墨夜)两态主题切换:定义偏好解析与持久化语义、首帧防闪烁、图表主题联动与公开页跟随策略,使暗色令牌成为有消费方的一等能力。

## Requirements

### Requirement: 主题偏好解析与持久化

主题解析顺序 MUST 为:localStorage 手动值优先;无手动值时跟随系统 `prefers-color-scheme`;两者皆无(或不支持)时默认亮色。解析结果以挂载/移除根元素 `.dark` 类的方式生效。用户手动切换后,手动值 MUST 写入 localStorage 并持续优先于系统偏好;用户可随时再次切换改写。

#### Scenario: 首次访问跟随系统

- **WHEN** 无本地主题存储的访客打开任意页面,且系统偏好为暗色
- **THEN** 页面以暗色主题渲染(根元素挂 `.dark`),且 localStorage 不写入手动值

#### Scenario: 手动值优先于系统

- **WHEN** 用户手动切换为亮色后,再把系统偏好改为暗色并刷新页面
- **THEN** 页面仍以亮色渲染(localStorage 手动值优先于系统偏好)

#### Scenario: 手动切换即时生效并持久化

- **WHEN** 用户在任意页面切换主题
- **THEN** 全站 UI 即时切换为目标主题,刷新或重开浏览器后仍保持该选择

### Requirement: 主题切换入口

已登录应用壳内 MUST 提供主题切换入口(用户菜单内),入口 MUST 呈现切换目标态(当前亮色时呈现「切换为暗色」及月亮图标,当前暗色时同理反转),点击后切换;切换 MUST 仅影响本浏览器,不产生任何网络请求。

#### Scenario: 入口切换

- **WHEN** 用户在用户菜单点击主题切换入口
- **THEN** 当前主题立即翻转,入口图标与文案同步翻转为新的目标态,且无网络请求发生

### Requirement: 首帧防闪烁(防 FOUC)

页面加载 MUST 在首帧绘制前于内联脚本中按「localStorage 手动值 → 系统偏好」完成主题解析并设置根元素类,暗色用户刷新页面 MUST NOT 出现亮色闪烁。

#### Scenario: 暗色用户刷新无白闪

- **WHEN** 暗色主题用户刷新页面
- **THEN** 首帧即以暗色渲染,无先亮后暗的闪烁

### Requirement: 图表主题联动

主题切换时,页面上已渲染的 ECharts 图表 MUST 以新主题重新渲染:色板、轴色、提示框等取自设计 token 的视觉元素 MUST 与当前主题一致,不得停留在切换前的配色。

#### Scenario: 切换后图表换色

- **WHEN** 页面存在已渲染图表时用户切换主题
- **THEN** 图表立即按新主题的 token 色板重绘,与页面其余部分一致

### Requirement: 公开对话页跟随策略

公开对话页(BotPublicPage,免登录嵌入场景)MUST 跟随访客系统偏好渲染主题,MUST NOT 提供主题切换入口;若访客系统偏好为暗色则暗色渲染,否则亮色。

#### Scenario: 公开页无入口但跟随系统

- **WHEN** 匿名访客在公开对话页且系统偏好为暗色
- **THEN** 页面以暗色渲染,且页面内不存在主题切换入口

### Requirement: 控件边界令牌在两态均可辨识

表单控件的边界色必须由独立令牌 `--input`（muniu 侧 `--color-input`）提供，且必须 (MUST NOT) 等于分隔线令牌 `--border`（`--color-border`）——后者是刻意低对比的 hairline，两者同值会让未勾选的复选框等「只有一条线可辨」的控件隐身。控件边界必须 (MUST) 对其最近的不透明承载面（白卡 / 弹层底）达到 **≥3:1** 非文本对比，并对页面底同样 ≥3:1，亮暗两态各自独立成立，禁止 (MUST NOT) 用亮色结论代替暗色验证。三仓现值：kongming/jinguan 亮 `#808c86`（对宣纸白 3.35、对白卡 3.49）暗 `#62767c`（对卡面 3.58、对底 3.89）；muniu 亮 `#7c8781`（3.47/3.72）暗 `#6b7a6f`（4.06/3.69）。色板变更后必须 (MUST) 重新计算比值，禁止跨仓直接拷贝数值。

#### Scenario: 控件边界不等于分隔线

- GIVEN 某仓 `index.css` 的亮色令牌
- WHEN 比较 `--input` 与 `--border` 的取值
- THEN 两者不相等，且 `--input` 对其承载面比值 ≥3:1

#### Scenario: 暗色独立达标

- GIVEN 应用处于 `.dark` 态且弹层打开
- WHEN 测量复选框或输入框边框与其最近不透明底面
- THEN 对比度 ≥3:1，控件边界肉眼可辨（不依赖聚焦环或占位文本才能认出这是控件）

#### Scenario: 分隔线基线不被牵连

- GIVEN 控件边界令牌被调整
- WHEN 检查卡片描边、表格分隔线、底部导航边线
- THEN 仍沿用 `--border` 的 hairline 取值，视觉基线零变化

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
