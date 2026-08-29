# 040-theme-switch 提案

## Why

前端目前只有宣纸白(亮色)单态:暗色令牌(`index.css` 的 `.dark` 全套,黛墨夜配色)已按品牌三元色调校完毕,但全仓没有任何挂载/切换 `.dark` 类的代码——令牌长期无消费方,既挡住了用户可感知的主题能力,也存在随亮色迭代腐化漂移的风险。多屏走查清偿后,主题是前端基建的最后一块缺口。

## What Changes

- 用户菜单新增主题切换入口(亮/暗两态切换,带当前态图标)。
- 主题解析语义:首次访问跟随系统 `prefers-color-scheme`;用户手动切换后,手动值持久化(localStorage)并**优先于**系统偏好,直到用户再次手动切换。
- `index.html` 内联防 FOUC 脚本:首帧绘制前按「存储值 → 系统偏好」解析并挂 `.dark` 类,暗色用户刷新不闪白屏。
- 主题切换时 ECharts 实例重挂:`chart-view-impl` 的色板与轴色在挂载时经 `getComputedStyle` 读 token,不重挂则图表停留在旧主题。
- 公开对话页(`BotPublicPage`)跟随访客系统偏好,不提供切换入口(公开页无用户会话语义,入口无落点)。

## Capabilities

### New Capabilities

- `theme-switch`: 前端亮/暗主题切换——偏好解析与持久化语义、防 FOUC、图表主题联动、公开页跟随策略。

### Modified Capabilities

(无——现有 `web-nav`/`web-admin` 等能力域的需求不因主题切换变化;用户菜单入口的行为由 `theme-switch` spec 描述。)

## Impact

- `apps/web`:`index.html`(内联脚本)、`user-menu.tsx`(入口)、新增 `theme-store`(zustand,仿 `tenant-store` 形态)、`chart-view-impl.tsx`(主题变化重挂)、`BotPublicPage.tsx`(跟随系统只读)。
- 不涉后端、OpenAPI 契约、schema 与信任边界(ADR-002/003/004/005 均不触发);色值全部走既有 token,符合前端样式铁律(docs/tech-stack.md)。

## Non-goals

- 不做多主题/自定义色:仅宣纸白与黛墨夜两态,不引入主题配置面。
- 不做定时切换(如日落自动切暗)与系统偏好实时监听跟随之外的高级策略。
- 不做租户级/组织级主题:主题是浏览器本地偏好,不入库、不随租户走。
- 不做 i18n(维持挂起,等真实需求另行立项)。
