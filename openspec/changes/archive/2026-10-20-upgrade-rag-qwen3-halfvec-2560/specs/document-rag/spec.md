## MODIFIED Requirements

### Requirement: 文档上传与父子分块

系统 SHALL 支持上传 .md/.txt/.pdf/.docx 文档(大小上限沿用既有分档:文本类 1MB、二进制 10MB):父块 ~800 字符按标题层级切分,子块 ~200 字符滑窗(overlap ~50);仅子块计算 2560 维 embedding,且检索问句使用相同维度;同名文档覆盖式更新(先删后插),content_hash(SHA-256)相同 SHALL 跳过重切(幂等);解析失败/超限 SHALL 400 不静默。

#### Scenario: 文档子块使用统一向量维度

- GIVEN 上传文档被切分为子块且 embedding 服务可用
- WHEN 系统为子块建立向量并执行文档检索
- THEN 子块向量和检索问句向量均为 2560 维

#### Scenario: 父子分块结构

- GIVEN 上传一份带多级标题的 Markdown 文档
- WHEN 分块完成
- THEN 父块按标题层级聚合,子块滑窗覆盖父块全文,子块 parent_id 指向父块

#### Scenario: 同内容幂等

- GIVEN 文档已上传且内容未变(content_hash 相同)
- WHEN 再次上传同名文档
- THEN 跳过重切,块数不变

