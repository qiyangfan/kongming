# Design: improve-sql-block-rendering

## 现状

- `apps/web/src/components/artifact-blocks.tsx` 的 SQL 卡是折叠块 + `Textarea`
  (等宽) + 「重新执行/还原」,直接可编辑;无语法高亮,与文档卡渲染形态不一致。
- `apps/web/src/components/markdown-text.tsx` 的围栏代码块只做 `pre` 底色与
  等宽,无高亮;项目未引入任何高亮依赖。

## 形态决策

默认只读、编辑收次级,理由见 proposal(编辑是低频添头,真实价值是透明可复核):

1. 折叠标题区维持「SQL」+ 图标;展开后默认渲染**只读高亮代码块**与「复制」。
2. 展开区加次级「编辑」按钮(ghost,小尺寸);点击进入编辑态——textarea 预填
   当前 SQL,保留「重新执行/还原」;「还原」同时回到只读态。
3. 编辑态执行成功后由消息刷新收口(现有 `invalidateQueries` 路径不变);执行失败
   停在编辑态并展示错误。
4. 只读态复用 MarkdownText 的代码块管线(` ```sql ` 渲染),保证与文档卡一致;
   编辑态是朴素 textarea(不做编辑器)。

## 高亮选型(实测数据)

esbuild --minify 打包,gzip 体积;预算口径为 brotli(size-limit 总 800 kB,余量
约 136 kB):

| 方案 | 体积 | 取舍 |
| --- | --- | --- |
| refractor/core + refractor/sql + 薄 rehype 插件 | 13.2 KB | **采纳**:只注册 sql;插件约 25 行(遍历 hast code 节点替换 children),配单测 |
| rehype-highlight | 55.4 KB | 备选:零手写但 lowlight common 全家言 |
| shiki 细粒度(core+sql+单主题+JS engine) | 65.6 KB | 视觉最好;oniguruma 引擎另需 ~400KB WASM 资产 |
| rehype-prism-plus | 224 KB | 排除:refractor 全量 + parse5,超预算 |

结论:refractor 细粒度。高亮质量(关键字/字符串/数字/注释/函数)对 SQL 展示足够,
预留给后续功能的预算余量更重要。

## 渲染管线

- `MarkdownText` 增加一个 rehype 插件:仅当 `code` 节点 className 含
  `language-sql` 时调用 `refractor.highlight(code, "sql")` 并以返回的 hast 树
  替换节点 children;其余语言与无语言代码块原样(等宽 + 底色)。
- 未知/缺省语言不抛错(回归点:未标注语言的代码块与行内 code 行为不变)。
- 插件放 `apps/web/src/lib/rehype-sql-highlight.ts`(纯函数,单测覆盖:sql 高亮
  命中、非 sql 原样、无 className 原样、空内容不抛)。

## 设计 token

`index.css` 新增代码高亮变量组(亮暗双值,`theme-tokens.test.ts` 的暗色覆盖
守卫自动纳入):

- `--code-keyword` / `--code-string` / `--code-number` / `--code-comment` /
  `--code-function` / `--code-punctuation` / `--code-operator`

取值从现有调色板派生,保证亮暗两态对比度可读;组件侧只用变量,禁字面色值。
refractor 输出的 `token keyword` 等类名在 `index.css` 内映射到变量(唯一允许的
新增样式位置)。

## 体积与门禁

- 预计 +11 kB brotli(主 chunk);`web-check` 的 size-limit 复测记录实测值。
- 超出总预算才调整 `size-limit` 配置并登记理由;当前预期不触发。
- 宪法自检:无 hex、无原生控件(编辑用既有 Textarea/Button)、无内联 style、
  间距走 Tailwind 刻度。

## 风险

- refractor sql 语法对少见的方言写法可能高亮不准:只影响观感,不影响内容;
  以「未知 token 原样输出」为兜底。
- 文档卡代码块观感变化:预期正收益;亮暗两态走查覆盖。
- 编辑态回退只读的焦点管理:进入/退出编辑需恢复焦点到触发按钮(键盘可达)。
