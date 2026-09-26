# ADR-009: web 文档卡 Markdown 渲染收编 react-markdown 生态

日期: 2026-09-26

状态: 已接受(推翻 026 变更的「零依赖、手写 tokenizer」子裁决)

## 背景

026 conversation-artifacts 落地时,document 卡的 Markdown 渲染以手写解析实现,
当时裁决「不引 react-markdown——proposal 依赖节未放行新库」(技术栈锁定)。
实际维护暴露手写实现的边角成本:GFM 表格/删除线、单换行段落习惯、嵌套列表
等长尾语法各自需要补丁,tokenizer 正确性是持续自担的维护面。

## 决策

文档卡 Markdown 渲染收编 `react-markdown` + `remark-gfm` + `remark-breaks`:

- **成熟库优先**(与组件层 shadcn/ui 同一哲学):Markdown 语法的长尾正确性
  外包给活跃维护的生态,不自研 tokenizer;
- **安全边界不变**:不经 `dangerouslySetInnerHTML`、不引 rehype-raw——
  原始 HTML 一律按字面文本呈现(spec「Markdown 渲染不解析原始 HTML」);
  react-markdown 默认 urlTransform 剥离 `javascript:` 协议;
- **样式映射保持既有 document 卡视觉**,色彩全走设计 token(宪法 Article 3);
- **范围限定文档卡**(`MarkdownText` 组件,消费方 artifact-blocks 文档卡),
  消息流纯文本路径不涉及。

## 影响

- `apps/web` 新增三个纯前端渲染依赖,lockfile 入库;无运行时副作用面;
- `apps/web/src/components/markdown-text.tsx` 由手写解析改写为组件映射;
- 零 API 契约变更、零后端影响。

## 与既有决策的关系

- **技术栈锁定**(docs/tech-stack.md):「内容渲染」行以本 ADR 为立法留痕;
- **026 变更**:其「零依赖」子裁决自本 ADR 起废止,其余决策不变。
