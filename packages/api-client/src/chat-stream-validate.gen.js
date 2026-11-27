"use strict";
export const validate = validate20;
export default validate20;
const schema31 = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  oneOf: [
    { $ref: "#/$defs/StreamStageEvent" },
    { $ref: "#/$defs/StreamSqlEvent" },
    { $ref: "#/$defs/StreamSummaryEvent" },
    { $ref: "#/$defs/StreamToolCallEvent" },
    { $ref: "#/$defs/StreamResultEvent" },
    { $ref: "#/$defs/StreamErrorEvent" },
  ],
  $defs: {
    ApiKeyOut: {
      properties: {
        id: { type: "string", title: "Id" },
        name: { type: "string", title: "Name" },
        revoked_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Revoked At",
        },
        last_used_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Last Used At",
        },
        created_at: { type: "string", title: "Created At" },
      },
      type: "object",
      required: ["id", "name", "revoked_at", "last_used_at", "created_at"],
      title: "ApiKeyOut",
    },
    AppendedStepIn: {
      properties: {
        goal: { type: "string", maxLength: 2000, minLength: 1, title: "Goal" },
        require_confirm: {
          type: "boolean",
          title: "Require Confirm",
          default: false,
        },
      },
      type: "object",
      required: ["goal"],
      title: "AppendedStepIn",
    },
    ApproveIn: {
      properties: {
        approve: { type: "boolean", title: "Approve", default: true },
      },
      type: "object",
      title: "ApproveIn",
    },
    ArchiveRestoreIn: {
      properties: {
        window_dir: {
          type: "string",
          maxLength: 8,
          minLength: 8,
          pattern: "^\\d{8}$",
          title: "Window Dir",
        },
      },
      type: "object",
      required: ["window_dir"],
      title: "ArchiveRestoreIn",
    },
    ArchiveRestoreOut: {
      properties: {
        restored: { type: "integer", title: "Restored" },
        skipped: { type: "integer", title: "Skipped" },
      },
      type: "object",
      required: ["restored", "skipped"],
      title: "ArchiveRestoreOut",
    },
    ArchiveRunOut: {
      properties: {
        exported: { type: "integer", title: "Exported" },
        deleted: { type: "integer", title: "Deleted" },
        files: { items: { type: "string" }, type: "array", title: "Files" },
        window_dir: { type: "string", title: "Window Dir" },
        skipped_export: { type: "boolean", title: "Skipped Export" },
      },
      type: "object",
      required: [
        "exported",
        "deleted",
        "files",
        "window_dir",
        "skipped_export",
      ],
      title: "ArchiveRunOut",
    },
    ArtifactOut: {
      properties: {
        type: { type: "string", title: "Type" },
        content: { type: "string", title: "Content" },
        metadata: {
          additionalProperties: true,
          type: "object",
          title: "Metadata",
          default: {},
        },
      },
      type: "object",
      required: ["type", "content"],
      title: "ArtifactOut",
      description: "类型化产物:type ∈ sql/chart/document。",
    },
    AttachIn: {
      properties: {
        resource_type: {
          type: "string",
          pattern: "^(bot|workflow)$",
          title: "Resource Type",
        },
        resource_id: { type: "string", minLength: 1, title: "Resource Id" },
      },
      type: "object",
      required: ["resource_type", "resource_id"],
      title: "AttachIn",
    },
    AttachmentOut: {
      properties: {
        attachment_id: { type: "string", title: "Attachment Id" },
        filename: { type: "string", title: "Filename" },
        mime: { type: "string", title: "Mime" },
        size_bytes: { type: "integer", title: "Size Bytes" },
      },
      type: "object",
      required: ["attachment_id", "filename", "mime", "size_bytes"],
      title: "AttachmentOut",
    },
    BindIn: {
      properties: {
        datasource_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Datasource Key",
        },
        enabled: { type: "boolean", title: "Enabled" },
      },
      type: "object",
      required: ["datasource_key", "enabled"],
      title: "BindIn",
    },
    Body_transcribe_audio_chat_stt_post: {
      properties: {
        file: {
          type: "string",
          contentMediaType: "application/octet-stream",
          title: "File",
          description: "音频文件(webm/opus/mp3/wav/m4a)",
        },
      },
      type: "object",
      required: ["file"],
      title: "Body_transcribe_audio_chat_stt_post",
    },
    Body_upload_attachment_chat_attachments_post: {
      properties: {
        file: {
          type: "string",
          contentMediaType: "application/octet-stream",
          title: "File",
        },
      },
      type: "object",
      required: ["file"],
      title: "Body_upload_attachment_chat_attachments_post",
    },
    Body_upload_doc_system_document_upload_post: {
      properties: {
        file: {
          type: "string",
          contentMediaType: "application/octet-stream",
          title: "File",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
      },
      type: "object",
      required: ["file", "datasource_key"],
      title: "Body_upload_doc_system_document_upload_post",
    },
    Body_upload_document_system_rag_documents_upload_post: {
      properties: {
        file: {
          type: "string",
          contentMediaType: "application/octet-stream",
          title: "File",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
        schema_name: { type: "string", title: "Schema Name" },
        team_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Team Id",
        },
      },
      type: "object",
      required: ["file", "datasource_key", "schema_name"],
      title: "Body_upload_document_system_rag_documents_upload_post",
    },
    BotChatIn: {
      properties: {
        question: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Question",
        },
      },
      type: "object",
      required: ["question"],
      title: "BotChatIn",
    },
    BotChatOut: {
      properties: {
        success: { type: "boolean", title: "Success" },
        summary: { type: "string", title: "Summary" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
      },
      type: "object",
      required: ["success", "summary"],
      title: "BotChatOut",
    },
    BotIn: {
      properties: {
        bot_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Bot Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        system_prompt: {
          type: "string",
          maxLength: 4000,
          minLength: 1,
          title: "System Prompt",
        },
        tools: { items: { type: "string" }, type: "array", title: "Tools" },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
        model: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Model",
        },
        is_public: { type: "boolean", title: "Is Public", default: false },
      },
      type: "object",
      required: ["bot_key", "name", "system_prompt"],
      title: "BotIn",
    },
    BotOut: {
      properties: {
        id: { type: "string", title: "Id" },
        bot_key: { type: "string", title: "Bot Key" },
        name: { type: "string", title: "Name" },
        system_prompt: { type: "string", title: "System Prompt" },
        tools: { items: { type: "string" }, type: "array", title: "Tools" },
        datasource_key: { type: "string", title: "Datasource Key" },
        model: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Model",
        },
        enabled: { type: "boolean", title: "Enabled" },
        is_public: { type: "boolean", title: "Is Public" },
        published_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Published At",
        },
        team_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Team Id",
        },
        created_at: { type: "string", title: "Created At" },
      },
      type: "object",
      required: [
        "id",
        "bot_key",
        "name",
        "system_prompt",
        "tools",
        "datasource_key",
        "model",
        "enabled",
        "is_public",
        "created_at",
      ],
      title: "BotOut",
    },
    BotSnapshotOut: {
      properties: {
        name: { type: "string", title: "Name" },
        greeting: { type: "string", title: "Greeting", default: "" },
        suggestions: {
          items: { type: "string" },
          type: "array",
          title: "Suggestions",
          default: [],
        },
        system_prompt: { type: "string", title: "System Prompt", default: "" },
        tools: {
          items: { type: "string" },
          type: "array",
          title: "Tools",
          default: [],
        },
      },
      type: "object",
      required: ["name"],
      title: "BotSnapshotOut",
      description: "版本快照定型(读侧单源=StoreSnapshotFields 白名单)。",
    },
    BotUpdateIn: {
      properties: {
        name: {
          anyOf: [{ type: "string", maxLength: 128 }, { type: "null" }],
          title: "Name",
        },
        system_prompt: {
          anyOf: [{ type: "string", maxLength: 4000 }, { type: "null" }],
          title: "System Prompt",
        },
        tools: {
          anyOf: [
            { items: { type: "string" }, type: "array" },
            { type: "null" },
          ],
          title: "Tools",
        },
        datasource_key: {
          anyOf: [
            { type: "string", pattern: "^[a-z][a-z0-9_]{1,63}$" },
            { type: "null" },
          ],
          title: "Datasource Key",
        },
        model: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Model",
        },
        enabled: {
          anyOf: [{ type: "boolean" }, { type: "null" }],
          title: "Enabled",
        },
        is_public: {
          anyOf: [{ type: "boolean" }, { type: "null" }],
          title: "Is Public",
        },
      },
      type: "object",
      title: "BotUpdateIn",
    },
    BotVersionOut: {
      properties: {
        version_no: { type: "integer", title: "Version No" },
        published_at: { type: "string", title: "Published At" },
        current: { type: "boolean", title: "Current" },
        snapshot: { $ref: "#/$defs/BotSnapshotOut" },
      },
      type: "object",
      required: ["version_no", "published_at", "current", "snapshot"],
      title: "BotVersionOut",
      description: "版本历史项:版本号/上架时间/当前标识/快照内容。",
    },
    CallbackIn: {
      properties: {
        code: { type: "string", title: "Code" },
        state: { type: "string", title: "State" },
      },
      type: "object",
      required: ["code", "state"],
      title: "CallbackIn",
    },
    CallbackRequest: {
      properties: {
        code: { type: "string", title: "Code" },
        state: { type: "string", title: "State" },
      },
      type: "object",
      required: ["code", "state"],
      title: "CallbackRequest",
    },
    ChartCandidateOut: {
      properties: {
        type: { type: "string", title: "Type" },
        x: { anyOf: [{ type: "string" }, { type: "null" }], title: "X" },
        y: {
          items: { type: "string" },
          type: "array",
          title: "Y",
          default: [],
        },
        series: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Series",
        },
      },
      type: "object",
      required: ["type"],
      title: "ChartCandidateOut",
      description:
        "图表候选:候选自带完整绑定,y 为值列(多度量多系列),\nseries 为系列维(分组柱/时序多系列)。既有 chart 字段保留=首候选展平。",
    },
    ChartInfoOut: {
      properties: {
        type: { type: "string", title: "Type" },
        x: { anyOf: [{ type: "string" }, { type: "null" }], title: "X" },
        y: { anyOf: [{ type: "string" }, { type: "null" }], title: "Y" },
      },
      type: "object",
      required: ["type"],
      title: "ChartInfoOut",
    },
    ChartOut: {
      properties: {
        type: { type: "string", title: "Type" },
        x: { anyOf: [{ type: "string" }, { type: "null" }], title: "X" },
        y: { anyOf: [{ type: "string" }, { type: "null" }], title: "Y" },
      },
      type: "object",
      required: ["type"],
      title: "ChartOut",
    },
    ChatQueryIn: {
      properties: {
        question: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Question",
        },
        session_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Session Id",
        },
        persist: { type: "boolean", title: "Persist", default: true },
        history: {
          items: { type: "string", maxLength: 2000 },
          type: "array",
          maxItems: 10,
          title: "History",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
        agent_mode: {
          type: "string",
          enum: ["recipe", "react"],
          title: "Agent Mode",
          default: "recipe",
        },
        attachment_ids: {
          items: { type: "string", format: "uuid" },
          type: "array",
          maxItems: 3,
          title: "Attachment Ids",
        },
        voice_turn_token: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Voice Turn Token",
        },
      },
      type: "object",
      required: ["question"],
      title: "ChatQueryIn",
    },
    ChatQueryOut: {
      properties: {
        session_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Session Id",
        },
        success: { type: "boolean", title: "Success" },
        verdict: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Verdict",
        },
        summary: { type: "string", title: "Summary" },
        insights: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          title: "Insights",
        },
        stats: { additionalProperties: true, type: "object", title: "Stats" },
        columns: { items: { type: "string" }, type: "array", title: "Columns" },
        rows: {
          items: { items: {}, type: "array" },
          type: "array",
          title: "Rows",
        },
        sql: { type: "string", title: "Sql" },
        chart: { $ref: "#/$defs/ChartOut" },
        chart_candidates: {
          items: { $ref: "#/$defs/ChartCandidateOut" },
          type: "array",
          title: "Chart Candidates",
          default: [],
        },
        retry_count: { type: "integer", title: "Retry Count" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
        error_detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error Detail",
        },
        message_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Message Id",
        },
        datasource_key: {
          type: "string",
          title: "Datasource Key",
          default: "default",
        },
        pending_tool_calls: {
          items: { $ref: "#/$defs/PendingToolCallOut" },
          type: "array",
          title: "Pending Tool Calls",
          default: [],
        },
        artifacts: {
          items: { $ref: "#/$defs/ArtifactOut" },
          type: "array",
          title: "Artifacts",
          default: [],
        },
        attachments: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Attachments",
          default: [],
        },
        column_labels: {
          items: { type: "string" },
          type: "array",
          title: "Column Labels",
          default: [],
        },
        metric_hits: {
          items: { $ref: "#/$defs/MetricHitOut" },
          type: "array",
          title: "Metric Hits",
          default: [],
        },
        sources: {
          items: { $ref: "#/$defs/SourceOut" },
          type: "array",
          title: "Sources",
          description:
            "纯数据问答不返回来源;知识问答和融合问答会从净化后的回答正文提取来源。",
        },
      },
      type: "object",
      required: [
        "session_id",
        "success",
        "verdict",
        "summary",
        "insights",
        "stats",
        "columns",
        "rows",
        "sql",
        "chart",
        "retry_count",
        "error",
      ],
      title: "ChatQueryOut",
    },
    CollabFinishIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        run_id: { type: "string", title: "Run Id" },
        verdict: { type: "string", title: "Verdict" },
        pending_count: {
          type: "integer",
          minimum: 0,
          title: "Pending Count",
          default: 0,
        },
        dissent: { type: "boolean", title: "Dissent", default: false },
        summary: {
          type: "string",
          maxLength: 8000,
          title: "Summary",
          default: "",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "run_id", "verdict"],
      title: "CollabFinishIn",
    },
    CollabParticipantIn: {
      properties: {
        role: {
          anyOf: [{ type: "string", maxLength: 32 }, { type: "null" }],
          title: "Role",
        },
        bot_key: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Bot Key",
        },
      },
      type: "object",
      title: "CollabParticipantIn",
      description: "圆桌参与者声明条目:role 取内置章程域,bot_key 可空绑定。",
    },
    CollabRunOut: {
      properties: {
        id: { type: "string", title: "Id" },
        step_id: { type: "string", title: "Step Id" },
        topology: {
          additionalProperties: true,
          type: "object",
          title: "Topology",
        },
        status: { type: "string", title: "Status" },
        verdict: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Verdict",
        },
        depth: { type: "integer", title: "Depth" },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
        updated_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Updated At",
        },
        workers: {
          items: { $ref: "#/$defs/CollabWorkerOut" },
          type: "array",
          title: "Workers",
        },
        turns: {
          items: { $ref: "#/$defs/CollabTurnOut" },
          type: "array",
          title: "Turns",
        },
      },
      type: "object",
      required: ["id", "step_id", "topology", "status", "verdict", "depth"],
      title: "CollabRunOut",
    },
    CollabRunsOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/CollabRunOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["items"],
      title: "CollabRunsOut",
    },
    CollabStartIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        step_id: { type: "string", title: "Step Id" },
        depth: {
          type: "integer",
          maximum: 8,
          minimum: 1,
          title: "Depth",
          default: 1,
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "step_id"],
      title: "CollabStartIn",
    },
    CollabTopologyIn: {
      properties: {
        kind: { anyOf: [{ type: "string" }, { type: "null" }], title: "Kind" },
        max_workers: {
          anyOf: [
            { type: "integer", maximum: 16, minimum: 1 },
            { type: "null" },
          ],
          title: "Max Workers",
        },
        critic: { type: "boolean", title: "Critic", default: false },
        dispatch: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Dispatch",
        },
        bot_key: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Bot Key",
        },
        max_rounds: {
          anyOf: [
            { type: "integer", maximum: 16, minimum: 1 },
            { type: "null" },
          ],
          title: "Max Rounds",
        },
        participants: {
          anyOf: [
            {
              items: { $ref: "#/$defs/CollabParticipantIn" },
              type: "array",
              maxItems: 16,
            },
            { type: "null" },
          ],
          title: "Participants",
        },
        workers: {
          anyOf: [
            {
              items: { $ref: "#/$defs/CollabWorkerIn" },
              type: "array",
              maxItems: 16,
            },
            { type: "null" },
          ],
          title: "Workers",
        },
      },
      type: "object",
      title: "CollabTopologyIn",
    },
    CollabTurnIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        run_id: { type: "string", title: "Run Id" },
        round_no: {
          type: "integer",
          maximum: 64,
          minimum: 1,
          title: "Round No",
        },
        worker_no: {
          type: "integer",
          maximum: 64,
          minimum: 0,
          title: "Worker No",
        },
        kind: { type: "string", title: "Kind" },
        content: {
          additionalProperties: true,
          type: "object",
          title: "Content",
        },
        evidence: {
          additionalProperties: true,
          type: "object",
          title: "Evidence",
        },
        usage: { additionalProperties: true, type: "object", title: "Usage" },
        trace_id: {
          type: "string",
          maxLength: 64,
          title: "Trace Id",
          default: "",
        },
      },
      type: "object",
      required: [
        "task_id",
        "tenant_key",
        "run_id",
        "round_no",
        "worker_no",
        "kind",
      ],
      title: "CollabTurnIn",
      description: "圆桌轮次上账载荷(追加只写,唯一键拒绝重复)。",
    },
    CollabTurnOut: {
      properties: {
        round_no: { type: "integer", title: "Round No" },
        worker_no: { type: "integer", title: "Worker No" },
        kind: { type: "string", title: "Kind" },
        content: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Content",
        },
        evidence: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Evidence",
        },
        usage: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Usage",
        },
        trace_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Trace Id",
        },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "round_no",
        "worker_no",
        "kind",
        "content",
        "evidence",
        "usage",
        "trace_id",
      ],
      title: "CollabTurnOut",
      description: "圆桌轮次转写行(黑板观测面,追加只写)。",
    },
    CollabWorkerIn: {
      properties: {
        name: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Name",
        },
        bot_key: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Bot Key",
        },
      },
      type: "object",
      title: "CollabWorkerIn",
      description:
        "分工名册成员声明条目:Bot 点名,可选别名\n(身份 = name 缺省 bot_key;声明期锁定,运行期不可增减改绑)。",
    },
    CollabWorkerOut: {
      properties: {
        worker_no: { type: "integer", title: "Worker No" },
        subgoal: { type: "string", title: "Subgoal" },
        bot_key: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Bot Key",
        },
        status: { type: "string", title: "Status" },
        conclusion: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Conclusion",
        },
        evidence: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Evidence",
        },
        usage: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Usage",
        },
        trace_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Trace Id",
        },
        updated_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Updated At",
        },
      },
      type: "object",
      required: [
        "worker_no",
        "subgoal",
        "bot_key",
        "status",
        "conclusion",
        "evidence",
        "usage",
        "trace_id",
      ],
      title: "CollabWorkerOut",
    },
    CollabWorkerResultIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        run_id: { type: "string", title: "Run Id" },
        worker_no: { type: "integer", minimum: 1, title: "Worker No" },
        status: { type: "string", title: "Status" },
        conclusion: {
          additionalProperties: true,
          type: "object",
          title: "Conclusion",
        },
        evidence: {
          additionalProperties: true,
          type: "object",
          title: "Evidence",
        },
        usage: { additionalProperties: true, type: "object", title: "Usage" },
        trace_id: {
          type: "string",
          maxLength: 64,
          title: "Trace Id",
          default: "",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "run_id", "worker_no", "status"],
      title: "CollabWorkerResultIn",
    },
    CollabWorkerStartIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        run_id: { type: "string", title: "Run Id" },
        worker_no: { type: "integer", minimum: 1, title: "Worker No" },
        subgoal: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Subgoal",
        },
        bot_key: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Bot Key",
        },
        trace_id: {
          type: "string",
          maxLength: 64,
          title: "Trace Id",
          default: "",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "run_id", "worker_no", "subgoal"],
      title: "CollabWorkerStartIn",
    },
    ConnectorIn: {
      properties: {
        key: { type: "string", maxLength: 64, minLength: 2, title: "Key" },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        description: {
          type: "string",
          maxLength: 512,
          minLength: 1,
          title: "Description",
        },
        method: { type: "string", maxLength: 8, minLength: 1, title: "Method" },
        path_template: {
          type: "string",
          maxLength: 1024,
          minLength: 1,
          title: "Path Template",
        },
        params: {
          items: { $ref: "#/$defs/ParamDeclIn" },
          type: "array",
          title: "Params",
          default: [],
        },
        allowed_hosts: {
          items: { type: "string" },
          type: "array",
          maxItems: 10,
          title: "Allowed Hosts",
        },
        auth_type: {
          type: "string",
          maxLength: 16,
          minLength: 1,
          title: "Auth Type",
        },
        auth_secret: {
          anyOf: [{ type: "string", maxLength: 2048 }, { type: "null" }],
          title: "Auth Secret",
        },
        requires_confirmation: {
          anyOf: [{ type: "boolean" }, { type: "null" }],
          title: "Requires Confirmation",
        },
        timeout_ms: { type: "integer", minimum: 1, title: "Timeout Ms" },
      },
      type: "object",
      required: [
        "key",
        "name",
        "description",
        "method",
        "path_template",
        "allowed_hosts",
        "auth_type",
        "timeout_ms",
      ],
      title: "ConnectorIn",
    },
    ConnectorOut: {
      properties: {
        id: { type: "string", title: "Id" },
        tenant_id: { type: "string", title: "Tenant Id" },
        key: { type: "string", title: "Key" },
        name: { type: "string", title: "Name" },
        description: { type: "string", title: "Description" },
        method: { type: "string", title: "Method" },
        path_template: { type: "string", title: "Path Template" },
        params: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Params",
        },
        allowed_hosts: {
          items: { type: "string" },
          type: "array",
          title: "Allowed Hosts",
        },
        auth_type: { type: "string", title: "Auth Type" },
        has_secret: { type: "boolean", title: "Has Secret" },
        secret_mask: { type: "string", title: "Secret Mask" },
        requires_confirmation: {
          type: "boolean",
          title: "Requires Confirmation",
        },
        timeout_ms: { type: "integer", title: "Timeout Ms" },
        enabled: { type: "string", title: "Enabled" },
        created_at: { title: "Created At" },
        updated_at: { title: "Updated At" },
      },
      type: "object",
      required: [
        "id",
        "tenant_id",
        "key",
        "name",
        "description",
        "method",
        "path_template",
        "params",
        "allowed_hosts",
        "auth_type",
        "has_secret",
        "secret_mask",
        "requires_confirmation",
        "timeout_ms",
        "enabled",
      ],
      title: "ConnectorOut",
      description:
        "连接器条目(掩码面;凭证只出形态与尾号掩码,spec「凭证不回显」)。",
    },
    ControlStatusOut: {
      properties: {
        mode: { type: "string", title: "Mode" },
        enabled: { type: "boolean", title: "Enabled" },
        service_id: { type: "string", title: "Service Id" },
        base_url: { type: "string", title: "Base Url" },
        snapshot_present: { type: "boolean", title: "Snapshot Present" },
        snapshot_version: { type: "integer", title: "Snapshot Version" },
        observed_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Observed At",
        },
        stale: { type: "boolean", title: "Stale" },
        security_ok: { type: "boolean", title: "Security Ok" },
        pending_usage: { type: "integer", title: "Pending Usage" },
        dropped_usage: { type: "integer", title: "Dropped Usage" },
        readonly_enabled: { type: "boolean", title: "Readonly Enabled" },
        last_error: { type: "string", title: "Last Error" },
      },
      type: "object",
      required: [
        "mode",
        "enabled",
        "service_id",
        "base_url",
        "snapshot_present",
        "snapshot_version",
        "observed_at",
        "stale",
        "security_ok",
        "pending_usage",
        "dropped_usage",
        "readonly_enabled",
        "last_error",
      ],
      title: "ControlStatusOut",
    },
    CoverageReportOut: {
      properties: {
        datasource_key: { type: "string", title: "Datasource Key" },
        unregistered_tables: {
          items: { $ref: "#/$defs/SchemaTableOut" },
          type: "array",
          title: "Unregistered Tables",
        },
        unregistered_total: { type: "integer", title: "Unregistered Total" },
        structure_only_tables: {
          items: { $ref: "#/$defs/SchemaTableOut" },
          type: "array",
          title: "Structure Only Tables",
        },
        structure_only_total: {
          type: "integer",
          title: "Structure Only Total",
        },
        missing_columns: {
          items: { $ref: "#/$defs/MissingColumnsOut" },
          type: "array",
          title: "Missing Columns",
        },
        missing_columns_total: {
          type: "integer",
          title: "Missing Columns Total",
        },
        no_sample_tables: {
          items: { $ref: "#/$defs/SchemaTableOut" },
          type: "array",
          title: "No Sample Tables",
        },
        no_sample_total: { type: "integer", title: "No Sample Total" },
        history: { $ref: "#/$defs/HistoryOverviewOut" },
        failure_clusters: {
          items: { $ref: "#/$defs/FailureClusterOut" },
          type: "array",
          title: "Failure Clusters",
        },
        failure_cluster_total: {
          type: "integer",
          title: "Failure Cluster Total",
        },
      },
      type: "object",
      required: [
        "datasource_key",
        "unregistered_tables",
        "unregistered_total",
        "structure_only_tables",
        "structure_only_total",
        "missing_columns",
        "missing_columns_total",
        "no_sample_tables",
        "no_sample_total",
        "history",
        "failure_clusters",
        "failure_cluster_total",
      ],
      title: "CoverageReportOut",
    },
    CreateIn: {
      properties: {
        goal: { type: "string", maxLength: 4000, minLength: 1, title: "Goal" },
        datasource_key: {
          type: "string",
          maxLength: 64,
          title: "Datasource Key",
          default: "default",
        },
        session_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Session Id",
        },
        budget: {
          anyOf: [
            { additionalProperties: { type: "integer" }, type: "object" },
            { type: "null" },
          ],
          title: "Budget",
        },
      },
      type: "object",
      required: ["goal"],
      title: "CreateIn",
    },
    CronPreviewOut: {
      properties: {
        next: { items: { type: "string" }, type: "array", title: "Next" },
      },
      type: "object",
      required: ["next"],
      title: "CronPreviewOut",
      description: "cron 预览(与创建/调度同源的解析计算,前端防抖消费)。",
    },
    DatasourceActionIn: {
      properties: {
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
      },
      type: "object",
      required: ["datasource_key"],
      title: "DatasourceActionIn",
    },
    DatasourceCreateIn: {
      properties: {
        datasource_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Datasource Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        tenant_id: { type: "string", format: "uuid", title: "Tenant Id" },
        host: { type: "string", maxLength: 255, minLength: 1, title: "Host" },
        port: {
          type: "integer",
          maximum: 65535,
          minimum: 1,
          title: "Port",
          default: 5432,
        },
        database: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Database",
        },
        username: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Username",
        },
        password: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Password",
        },
        default_schema: {
          type: "string",
          maxLength: 64,
          title: "Default Schema",
          default: "public",
        },
        readonly: { type: "boolean", title: "Readonly", default: true },
      },
      type: "object",
      required: [
        "datasource_key",
        "name",
        "tenant_id",
        "host",
        "database",
        "username",
        "password",
      ],
      title: "DatasourceCreateIn",
    },
    DatasourceItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        datasource_key: { type: "string", title: "Datasource Key" },
        name: { type: "string", title: "Name" },
        tenant_id: { type: "string", title: "Tenant Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        tenant_name: { type: "string", title: "Tenant Name" },
        host: { type: "string", title: "Host" },
        port: { type: "integer", title: "Port" },
        database: { type: "string", title: "Database" },
        username: { type: "string", title: "Username" },
        default_schema: { type: "string", title: "Default Schema" },
        readonly: { type: "boolean", title: "Readonly" },
        status: { type: "string", title: "Status" },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "datasource_key",
        "name",
        "tenant_id",
        "tenant_key",
        "tenant_name",
        "host",
        "port",
        "database",
        "username",
        "default_schema",
        "readonly",
        "status",
        "created_at",
      ],
      title: "DatasourceItemOut",
    },
    DatasourceOptionOut: {
      properties: {
        datasource_key: { type: "string", title: "Datasource Key" },
        name: { type: "string", title: "Name" },
      },
      type: "object",
      required: ["datasource_key", "name"],
      title: "DatasourceOptionOut",
    },
    DatasourcePageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/DatasourceItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "DatasourcePageOut",
    },
    DatasourceUpdateIn: {
      properties: {
        datasource_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Datasource Key",
        },
        tenant_id: { type: "string", format: "uuid", title: "Tenant Id" },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        host: { type: "string", maxLength: 255, minLength: 1, title: "Host" },
        port: {
          type: "integer",
          maximum: 65535,
          minimum: 1,
          title: "Port",
          default: 5432,
        },
        database: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Database",
        },
        username: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Username",
        },
        password: {
          anyOf: [
            { type: "string", maxLength: 128, minLength: 1 },
            { type: "null" },
          ],
          title: "Password",
        },
        default_schema: {
          type: "string",
          maxLength: 64,
          title: "Default Schema",
          default: "public",
        },
        readonly: { type: "boolean", title: "Readonly", default: true },
      },
      type: "object",
      required: [
        "datasource_key",
        "tenant_id",
        "name",
        "host",
        "database",
        "username",
      ],
      title: "DatasourceUpdateIn",
      description:
        "全量更新数据源配置;key 和 tenant_id 不可修改,密码为空或未提交时保持原值。",
    },
    DeptCreateIn: {
      properties: {
        parent_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Parent Id",
        },
        dept_name: {
          type: "string",
          maxLength: 100,
          minLength: 1,
          title: "Dept Name",
        },
        tenant_id: { type: "string", format: "uuid", title: "Tenant Id" },
        sort: { type: "integer", title: "Sort", default: 0 },
      },
      type: "object",
      required: ["dept_name", "tenant_id"],
      title: "DeptCreateIn",
    },
    DeptNodeOut: {
      properties: {
        id: { type: "string", title: "Id" },
        parent_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Parent Id",
        },
        dept_name: { type: "string", title: "Dept Name" },
        tenant_id: { type: "string", title: "Tenant Id" },
        sort: { type: "integer", title: "Sort" },
        status: { type: "string", title: "Status" },
        children: {
          items: { $ref: "#/$defs/DeptNodeOut" },
          type: "array",
          title: "Children",
        },
      },
      type: "object",
      required: ["id", "parent_id", "dept_name", "tenant_id", "sort", "status"],
      title: "DeptNodeOut",
    },
    DeptUpdateIn: {
      properties: {
        parent_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Parent Id",
        },
        dept_name: {
          type: "string",
          maxLength: 100,
          minLength: 1,
          title: "Dept Name",
        },
        tenant_id: { type: "string", format: "uuid", title: "Tenant Id" },
        sort: { type: "integer", title: "Sort" },
        status: {
          type: "string",
          enum: ["ACTIVE", "DISABLED"],
          title: "Status",
        },
      },
      type: "object",
      required: ["dept_name", "tenant_id", "sort", "status"],
      title: "DeptUpdateIn",
    },
    DocEnrichOut: {
      properties: {
        doc_id: { type: "string", title: "Doc Id" },
        entities: { type: "integer", title: "Entities" },
      },
      type: "object",
      required: ["doc_id", "entities"],
      title: "DocEnrichOut",
    },
    DocUploadOut: {
      properties: {
        doc_id: { type: "string", title: "Doc Id" },
        blocks: { type: "integer", title: "Blocks" },
        skipped: { type: "boolean", title: "Skipped" },
      },
      type: "object",
      required: ["doc_id", "blocks", "skipped"],
      title: "DocUploadOut",
    },
    DocumentChunkOut: {
      properties: {
        id: { type: "string", title: "Id" },
        chunk_level: { type: "string", title: "Chunk Level" },
        seq: { type: "integer", title: "Seq" },
        source_ref: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Source Ref",
        },
        has_embedding: { type: "boolean", title: "Has Embedding" },
        content: { type: "string", title: "Content" },
      },
      type: "object",
      required: [
        "id",
        "chunk_level",
        "seq",
        "source_ref",
        "has_embedding",
        "content",
      ],
      title: "DocumentChunkOut",
    },
    DocumentItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        doc_name: { type: "string", title: "Doc Name" },
        block_count: { type: "integer", title: "Block Count" },
        updated_at: { type: "string", title: "Updated At" },
      },
      type: "object",
      required: ["id", "doc_name", "block_count", "updated_at"],
      title: "DocumentItemOut",
    },
    EditIn: {
      properties: {
        base_version: { type: "integer", title: "Base Version" },
        sections: {
          items: { $ref: "#/$defs/EditSectionIn" },
          type: "array",
          title: "Sections",
        },
        title: {
          anyOf: [{ type: "string", maxLength: 200 }, { type: "null" }],
          title: "Title",
        },
      },
      type: "object",
      required: ["base_version"],
      title: "EditIn",
    },
    EditSectionIn: {
      properties: {
        section_id: { type: "string", title: "Section Id" },
        title: {
          anyOf: [{ type: "string", maxLength: 200 }, { type: "null" }],
          title: "Title",
        },
        narrative_md: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Narrative Md",
        },
      },
      additionalProperties: false,
      type: "object",
      required: ["section_id"],
      title: "EditSectionIn",
    },
    EditTableIn: {
      properties: {
        table_comment: {
          type: "string",
          maxLength: 500,
          title: "Table Comment",
        },
        alias: { type: "string", maxLength: 64, title: "Alias" },
      },
      type: "object",
      required: ["table_comment", "alias"],
      title: "EditTableIn",
      description: "表说明人工精修请求(table_comment 与 alias 同面编辑)。",
    },
    ExplainIn: {
      properties: {
        message_id: { type: "string", format: "uuid", title: "Message Id" },
      },
      type: "object",
      required: ["message_id"],
      title: "ExplainIn",
      description: "message_id 引用形态:客户端不可提交裸 SQL。",
    },
    ExplainOut: {
      properties: {
        success: { type: "boolean", title: "Success" },
        plan: { anyOf: [{}, { type: "null" }], title: "Plan" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
      },
      type: "object",
      required: ["success"],
      title: "ExplainOut",
    },
    ExportIn: {
      properties: {
        format: { type: "string", enum: ["csv", "xlsx"], title: "Format" },
        columns: {
          items: { type: "string" },
          type: "array",
          maxItems: 200,
          minItems: 1,
          title: "Columns",
        },
        rows: {
          items: { items: {}, type: "array" },
          type: "array",
          maxItems: 10000,
          title: "Rows",
        },
      },
      type: "object",
      required: ["format", "columns"],
      title: "ExportIn",
      description:
        "所见即所导:前端传已展示的 columns+rows,不重跑 SQL。\n\nrows 传输层上限=修复:截断发生在 JSON 解析之后,挡不住请求体本身\n的内存放大;max_length 在解析期即拒(422),service 层截断保留为双闸。",
    },
    FailureClusterOut: {
      properties: {
        question: { type: "string", title: "Question" },
        count: { type: "integer", title: "Count" },
        last_status: { type: "string", title: "Last Status" },
        last_seen: { type: "string", format: "date-time", title: "Last Seen" },
        rating: { type: "string", title: "Rating" },
        has_corrected_sql: { type: "boolean", title: "Has Corrected Sql" },
      },
      type: "object",
      required: [
        "question",
        "count",
        "last_status",
        "last_seen",
        "rating",
        "has_corrected_sql",
      ],
      title: "FailureClusterOut",
    },
    FeedbackIn: {
      properties: {
        rating: { type: "string", enum: ["UP", "DOWN"], title: "Rating" },
        comment: {
          anyOf: [{ type: "string", maxLength: 500 }, { type: "null" }],
          title: "Comment",
        },
        corrected_sql: {
          anyOf: [{ type: "string", maxLength: 4000 }, { type: "null" }],
          title: "Corrected Sql",
        },
      },
      type: "object",
      required: ["rating"],
      title: "FeedbackIn",
    },
    FeedbackOut: {
      properties: {
        rating: { type: "string", title: "Rating" },
        comment: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Comment",
        },
        corrected_sql: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Corrected Sql",
        },
      },
      type: "object",
      required: ["rating", "comment", "corrected_sql"],
      title: "FeedbackOut",
      description: "提交后当前反馈状态(前端激活态回显用)。",
    },
    FeedbackPageItemOut: {
      properties: {
        message_id: { type: "string", title: "Message Id" },
        session_id: { type: "string", title: "Session Id" },
        user_id: { type: "string", title: "User Id" },
        user_nickname: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Nickname",
        },
        user_username: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Username",
        },
        tenant_id: { type: "string", title: "Tenant Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        datasource_key: { type: "string", title: "Datasource Key" },
        rating: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Rating",
        },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        sql_text: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Sql Text",
        },
        corrected_sql: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Corrected Sql",
        },
        feedback_comment: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Feedback Comment",
        },
        feedback_status: { type: "string", title: "Feedback Status" },
        feedback_time: {
          anyOf: [{ type: "string", format: "date-time" }, { type: "null" }],
          title: "Feedback Time",
        },
      },
      type: "object",
      required: [
        "message_id",
        "session_id",
        "user_id",
        "user_nickname",
        "tenant_id",
        "tenant_key",
        "datasource_key",
        "rating",
        "question",
        "sql_text",
        "corrected_sql",
        "feedback_comment",
        "feedback_status",
        "feedback_time",
      ],
      title: "FeedbackPageItemOut",
    },
    FeedbackPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/FeedbackPageItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "FeedbackPageOut",
    },
    FetchOut: {
      properties: {
        found: { type: "integer", title: "Found" },
        enriched: { type: "integer", title: "Enriched" },
        skipped: { type: "integer", title: "Skipped" },
        removed: { type: "integer", title: "Removed" },
      },
      type: "object",
      required: ["found", "enriched", "skipped", "removed"],
      title: "FetchOut",
    },
    FinishIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        epoch: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Epoch",
        },
        status: { type: "string", title: "Status" },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "status"],
      title: "FinishIn",
    },
    GenerateIn: {
      properties: {
        instruction: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Instruction",
        },
        datasource_key: {
          type: "string",
          maxLength: 64,
          title: "Datasource Key",
          default: "default",
        },
        session_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Session Id",
        },
        context_message_limit: {
          type: "integer",
          maximum: 50,
          minimum: 1,
          title: "Context Message Limit",
          default: 10,
        },
        title: {
          anyOf: [{ type: "string", maxLength: 200 }, { type: "null" }],
          title: "Title",
        },
      },
      type: "object",
      required: ["instruction"],
      title: "GenerateIn",
    },
    GenerateOut: {
      properties: {
        report_id: { type: "string", title: "Report Id" },
        status: { type: "string", title: "Status" },
        phase: { type: "string", title: "Phase" },
      },
      type: "object",
      required: ["report_id", "status", "phase"],
      title: "GenerateOut",
    },
    GraphCheckpointDeleteIn: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        thread_id: {
          type: "string",
          maxLength: 200,
          minLength: 1,
          title: "Thread Id",
        },
      },
      type: "object",
      required: ["tenant_key", "thread_id"],
      title: "GraphCheckpointDeleteIn",
    },
    GraphCheckpointIn: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        thread_id: {
          type: "string",
          maxLength: 200,
          minLength: 1,
          title: "Thread Id",
        },
        checkpoint_ns: { type: "string", title: "Checkpoint Ns", default: "" },
        kind: {
          type: "string",
          pattern: "^(checkpoint|write)$",
          title: "Kind",
          default: "checkpoint",
        },
        checkpoint_id: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Checkpoint Id",
        },
        task_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Task Id",
        },
        step_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Step Id",
        },
        payload_type: {
          type: "string",
          maxLength: 64,
          title: "Payload Type",
          default: "",
        },
        payload_b64: { type: "string", minLength: 1, title: "Payload B64" },
      },
      type: "object",
      required: ["tenant_key", "thread_id", "payload_b64"],
      title: "GraphCheckpointIn",
      description:
        "检查点写入载荷(blob 对控制面不透明;agent 序列化/反序列化)。",
    },
    GraphConfirmOut: {
      properties: {
        confirm_id: { type: "string", title: "Confirm Id" },
        step_id: { type: "string", title: "Step Id" },
        tool_name: { type: "string", title: "Tool Name" },
        summary: { type: "string", title: "Summary" },
        status: { type: "string", title: "Status" },
        expires_at: { type: "string", title: "Expires At" },
        created_at: { type: "string", title: "Created At" },
      },
      type: "object",
      required: [
        "confirm_id",
        "step_id",
        "tool_name",
        "summary",
        "status",
        "expires_at",
        "created_at",
      ],
      title: "GraphConfirmOut",
      description: "图内确认行(挂起卡片数据源;graph-checkpoint)。",
    },
    GuardrailAuditItem: {
      properties: {
        id: { type: "string", title: "Id" },
        trace_id: { type: "string", title: "Trace Id" },
        user_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Id",
        },
        tenant_id: { type: "string", title: "Tenant Id" },
        guardrail_type: { type: "string", title: "Guardrail Type" },
        rule_name: { type: "string", title: "Rule Name" },
        action: { type: "string", title: "Action" },
        content_digest: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Content Digest",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "trace_id",
        "user_id",
        "tenant_id",
        "guardrail_type",
        "rule_name",
        "action",
        "content_digest",
        "created_at",
      ],
      title: "GuardrailAuditItem",
    },
    GuardrailAuditPageOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/GuardrailAuditItem" },
          type: "array",
          title: "Items",
        },
        total: { type: "integer", title: "Total" },
        page: { type: "integer", title: "Page" },
        size: { type: "integer", title: "Size" },
      },
      type: "object",
      required: ["items", "total", "page", "size"],
      title: "GuardrailAuditPageOut",
    },
    GuardrailRuleOut: {
      properties: {
        name: { type: "string", title: "Name" },
        guardrail_type: { type: "string", title: "Guardrail Type" },
        action: { type: "string", title: "Action" },
        description: { type: "string", title: "Description" },
        enabled: { type: "boolean", title: "Enabled" },
        threshold: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Threshold",
        },
        overridden: { type: "boolean", title: "Overridden" },
        maskable: { type: "boolean", title: "Maskable" },
      },
      type: "object",
      required: [
        "name",
        "guardrail_type",
        "action",
        "description",
        "enabled",
        "threshold",
        "overridden",
        "maskable",
      ],
      title: "GuardrailRuleOut",
    },
    HTTPValidationError: {
      properties: {
        detail: {
          items: { $ref: "#/$defs/ValidationError" },
          type: "array",
          title: "Detail",
        },
      },
      type: "object",
      title: "HTTPValidationError",
    },
    HandleIn: {
      properties: {
        status: {
          type: "string",
          maxLength: 16,
          minLength: 1,
          title: "Status",
        },
        note: {
          anyOf: [{ type: "string", maxLength: 500 }, { type: "null" }],
          title: "Note",
        },
      },
      type: "object",
      required: ["status"],
      title: "HandleIn",
    },
    HandleOut: {
      properties: {
        status: { type: "string", title: "Status" },
        note: { anyOf: [{ type: "string" }, { type: "null" }], title: "Note" },
      },
      type: "object",
      required: ["status", "note"],
      title: "HandleOut",
    },
    HistoryOverviewOut: {
      properties: {
        success_count: { type: "integer", title: "Success Count" },
        distinct_questions: { type: "integer", title: "Distinct Questions" },
        cap: { type: "integer", title: "Cap" },
      },
      type: "object",
      required: ["success_count", "distinct_questions", "cap"],
      title: "HistoryOverviewOut",
    },
    IntrospectIn: {
      properties: {
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
      },
      type: "object",
      required: ["datasource_key"],
      title: "IntrospectIn",
    },
    IntrospectOut: {
      properties: { relations: { type: "integer", title: "Relations" } },
      type: "object",
      required: ["relations"],
      title: "IntrospectOut",
    },
    InvolvedTableOut: {
      properties: {
        table_name: { type: "string", title: "Table Name" },
        schema_name: { type: "string", title: "Schema Name" },
        datasource_key: { type: "string", title: "Datasource Key" },
        has_knowledge: { type: "boolean", title: "Has Knowledge" },
        knowledge_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Knowledge Id",
        },
      },
      type: "object",
      required: [
        "table_name",
        "schema_name",
        "datasource_key",
        "has_knowledge",
        "knowledge_id",
      ],
      title: "InvolvedTableOut",
    },
    InvolvedTablesOut: {
      properties: {
        tables: {
          items: { $ref: "#/$defs/InvolvedTableOut" },
          type: "array",
          title: "Tables",
        },
        parse_error: { type: "boolean", title: "Parse Error" },
      },
      type: "object",
      required: ["tables", "parse_error"],
      title: "InvolvedTablesOut",
    },
    KnowledgeItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        datasource_key: { type: "string", title: "Datasource Key" },
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        table_comment: { type: "string", title: "Table Comment" },
        columns_comment: {
          additionalProperties: { type: "string" },
          type: "object",
          title: "Columns Comment",
        },
        sample_queries: {
          items: { type: "string" },
          type: "array",
          title: "Sample Queries",
        },
        source_type: { type: "string", title: "Source Type" },
        updated_at: {
          type: "string",
          format: "date-time",
          title: "Updated At",
        },
        alias: { type: "string", title: "Alias", default: "" },
        team_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Team Id",
        },
        team_name: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Team Name",
        },
      },
      type: "object",
      required: [
        "id",
        "datasource_key",
        "schema_name",
        "table_name",
        "table_comment",
        "columns_comment",
        "sample_queries",
        "source_type",
        "updated_at",
      ],
      title: "KnowledgeItemOut",
    },
    KnowledgePageOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/KnowledgeItemOut" },
          type: "array",
          title: "Items",
        },
        total: { type: "integer", title: "Total" },
        page: { type: "integer", title: "Page" },
        size: { type: "integer", title: "Size" },
      },
      type: "object",
      required: ["items", "total", "page", "size"],
      title: "KnowledgePageOut",
    },
    LocalCommandIn: {
      properties: {
        action: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Action",
        },
        action_version: {
          type: "integer",
          minimum: 1,
          title: "Action Version",
          default: 1,
        },
        params: { additionalProperties: true, type: "object", title: "Params" },
        reason: {
          type: "string",
          maxLength: 500,
          minLength: 1,
          title: "Reason",
        },
      },
      type: "object",
      required: ["action", "reason"],
      title: "LocalCommandIn",
    },
    LocalCommandOut: {
      properties: {
        command_id: { type: "string", title: "Command Id" },
        idempotency_key: { type: "string", title: "Idempotency Key" },
        status: { type: "string", title: "Status" },
        result: { additionalProperties: true, type: "object", title: "Result" },
        error: { type: "string", title: "Error" },
      },
      type: "object",
      required: ["command_id", "idempotency_key", "status", "result", "error"],
      title: "LocalCommandOut",
    },
    McpPromptArgOut: {
      properties: {
        name: { type: "string", title: "Name" },
        description: { type: "string", title: "Description", default: "" },
        required: { type: "boolean", title: "Required", default: false },
      },
      type: "object",
      required: ["name"],
      title: "McpPromptArgOut",
      description: "模板参数声明。",
    },
    McpPromptEntryOut: {
      properties: {
        name: { type: "string", title: "Name" },
        description: { type: "string", title: "Description", default: "" },
        arguments: {
          items: { $ref: "#/$defs/McpPromptArgOut" },
          type: "array",
          title: "Arguments",
          default: [],
        },
      },
      type: "object",
      required: ["name"],
      title: "McpPromptEntryOut",
      description: "提示模板清单条目(仅声明,渲染预览实时拉取)。",
    },
    McpPromptPreviewIn: {
      properties: {
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        arguments: {
          additionalProperties: { type: "string" },
          type: "object",
          title: "Arguments",
          default: {},
        },
      },
      type: "object",
      required: ["name"],
      title: "McpPromptPreviewIn",
      description: "模板预览入参(按名 + 渲染 arguments)。",
    },
    McpPromptPreviewOut: {
      properties: {
        ok: { type: "boolean", title: "Ok" },
        text: { type: "string", title: "Text", default: "" },
        truncated: { type: "boolean", title: "Truncated", default: false },
        injection_hits: {
          items: { type: "string" },
          type: "array",
          title: "Injection Hits",
          default: [],
        },
        kind: { anyOf: [{ type: "string" }, { type: "null" }], title: "Kind" },
        detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Detail",
        },
      },
      type: "object",
      required: ["ok"],
      title: "McpPromptPreviewOut",
      description: "模板预览出参:净化后渲染文本 + 检测/截断标注。",
    },
    McpResourceEntryOut: {
      properties: {
        uri: { type: "string", title: "Uri" },
        name: { type: "string", title: "Name", default: "" },
        description: { type: "string", title: "Description", default: "" },
        media_type: { type: "string", title: "Media Type", default: "" },
      },
      type: "object",
      required: ["uri"],
      title: "McpResourceEntryOut",
      description: "资源清单条目(快照面,白名单与详情展示同源)。",
    },
    McpServerIn: {
      properties: {
        server_key: {
          type: "string",
          maxLength: 64,
          minLength: 2,
          title: "Server Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        endpoint: {
          type: "string",
          maxLength: 512,
          minLength: 8,
          title: "Endpoint",
        },
        headers: {
          additionalProperties: { type: "string" },
          type: "object",
          title: "Headers",
          default: {},
        },
      },
      type: "object",
      required: ["server_key", "name", "endpoint"],
      title: "McpServerIn",
    },
    McpServerOut: {
      properties: {
        id: { type: "string", title: "Id" },
        server_key: { type: "string", title: "Server Key" },
        name: { type: "string", title: "Name" },
        endpoint: { type: "string", title: "Endpoint" },
        enabled: { type: "boolean", title: "Enabled" },
        header_keys: {
          items: { type: "string" },
          type: "array",
          title: "Header Keys",
          default: [],
        },
        tools: {
          items: { $ref: "#/$defs/McpToolEntryOut" },
          type: "array",
          title: "Tools",
          default: [],
        },
        resources: {
          items: { $ref: "#/$defs/McpResourceEntryOut" },
          type: "array",
          title: "Resources",
          default: [],
        },
        prompts: {
          items: { $ref: "#/$defs/McpPromptEntryOut" },
          type: "array",
          title: "Prompts",
          default: [],
        },
        last_sync_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Last Sync At",
        },
        last_sync_error: {
          type: "string",
          title: "Last Sync Error",
          default: "",
        },
      },
      type: "object",
      required: ["id", "server_key", "name", "endpoint", "enabled"],
      title: "McpServerOut",
    },
    McpServerUpdateIn: {
      properties: {
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        endpoint: {
          type: "string",
          maxLength: 512,
          minLength: 8,
          title: "Endpoint",
        },
        headers: {
          additionalProperties: {
            anyOf: [{ type: "string" }, { type: "null" }],
          },
          type: "object",
          title: "Headers",
          default: {},
        },
      },
      type: "object",
      required: ["name", "endpoint"],
      title: "McpServerUpdateIn",
    },
    McpSyncOut: {
      properties: {
        ok: { type: "boolean", title: "Ok" },
        kind: { anyOf: [{ type: "string" }, { type: "null" }], title: "Kind" },
        detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Detail",
        },
        tools: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Tools",
        },
        resources: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Resources",
        },
        prompts: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Prompts",
        },
        conflicts: {
          anyOf: [
            { items: { type: "string" }, type: "array" },
            { type: "null" },
          ],
          title: "Conflicts",
        },
      },
      type: "object",
      required: ["ok"],
      title: "McpSyncOut",
    },
    McpTestOut: {
      properties: {
        ok: { type: "boolean", title: "Ok" },
        kind: { anyOf: [{ type: "string" }, { type: "null" }], title: "Kind" },
        detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Detail",
        },
        tools: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Tools",
        },
        resources: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Resources",
        },
        prompts: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Prompts",
        },
      },
      type: "object",
      required: ["ok"],
      title: "McpTestOut",
    },
    McpToolEntryOut: {
      properties: {
        name: { type: "string", title: "Name" },
        qualified: { type: "string", title: "Qualified" },
        description: { type: "string", title: "Description", default: "" },
        input_schema: {
          additionalProperties: true,
          type: "object",
          title: "Input Schema",
          default: {},
        },
        read_only: { type: "boolean", title: "Read Only", default: false },
      },
      type: "object",
      required: ["name", "qualified"],
      title: "McpToolEntryOut",
      description:
        "发现快照条目(掩码面随 server 返回);\ninput_schema 命名避 BaseModel.schema 阴影(pydantic 告警)。",
    },
    MeOut: {
      properties: {
        user_id: { type: "string", title: "User Id" },
        username: { type: "string", title: "Username" },
        nickname: { type: "string", title: "Nickname" },
        role_keys: {
          items: { type: "string" },
          type: "array",
          title: "Role Keys",
        },
        perm_codes: {
          items: { type: "string" },
          type: "array",
          title: "Perm Codes",
        },
        tenants: {
          items: { $ref: "#/$defs/TenantRefOut" },
          type: "array",
          title: "Tenants",
        },
      },
      type: "object",
      required: [
        "user_id",
        "username",
        "nickname",
        "role_keys",
        "perm_codes",
        "tenants",
      ],
      title: "MeOut",
    },
    MemberIn: {
      properties: {
        user_id: { type: "string", minLength: 1, title: "User Id" },
        role: {
          type: "string",
          pattern: "^(OWNER|MEMBER|VIEWER)$",
          title: "Role",
        },
      },
      type: "object",
      required: ["user_id", "role"],
      title: "MemberIn",
    },
    MemoryCreateIn: {
      properties: {
        content: {
          type: "string",
          maxLength: 500,
          minLength: 1,
          title: "Content",
        },
        team_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Team Id",
        },
      },
      type: "object",
      required: ["content"],
      title: "MemoryCreateIn",
    },
    MemoryItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        content: { type: "string", title: "Content" },
        category: { type: "string", title: "Category" },
        confidence: { type: "number", title: "Confidence" },
        source: { type: "string", title: "Source" },
        dormant: { type: "boolean", title: "Dormant" },
        last_hit_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Last Hit At",
        },
        created_at: { type: "string", title: "Created At" },
        team_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Team Id",
        },
        kind: { type: "string", title: "Kind", default: "memory" },
        legacy: { type: "boolean", title: "Legacy", default: false },
      },
      type: "object",
      required: [
        "id",
        "content",
        "category",
        "confidence",
        "source",
        "dormant",
        "last_hit_at",
        "created_at",
      ],
      title: "MemoryItemOut",
    },
    MemoryListOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/MemoryItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["items"],
      title: "MemoryListOut",
    },
    MemoryUpdateIn: {
      properties: {
        content: {
          type: "string",
          maxLength: 500,
          minLength: 1,
          title: "Content",
        },
        category: {
          type: "string",
          maxLength: 32,
          title: "Category",
          default: "general",
        },
      },
      type: "object",
      required: ["content"],
      title: "MemoryUpdateIn",
    },
    MessageResultOut: {
      properties: {
        columns: { items: { type: "string" }, type: "array", title: "Columns" },
        rows: {
          items: { items: {}, type: "array" },
          type: "array",
          title: "Rows",
        },
        insights: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          title: "Insights",
        },
        stats: { additionalProperties: true, type: "object", title: "Stats" },
        chart: { $ref: "#/$defs/ChartInfoOut" },
        chart_candidates: {
          items: { $ref: "#/$defs/ChartCandidateOut" },
          type: "array",
          title: "Chart Candidates",
          default: [],
        },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
        error_detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error Detail",
        },
        artifacts: {
          items: { $ref: "#/$defs/ArtifactOut" },
          type: "array",
          title: "Artifacts",
          default: [],
        },
        column_labels: {
          items: { type: "string" },
          type: "array",
          title: "Column Labels",
        },
        metric_hits: {
          items: { $ref: "#/$defs/MetricHitOut" },
          type: "array",
          title: "Metric Hits",
        },
        sources: {
          items: { $ref: "#/$defs/SourceOut" },
          type: "array",
          title: "Sources",
          description:
            "纯数据问答不返回来源;知识问答和融合问答会从净化后的回答正文提取来源。",
        },
      },
      type: "object",
      required: ["chart"],
      title: "MessageResultOut",
    },
    MetaColumnOut: {
      properties: {
        name: { type: "string", title: "Name" },
        data_type: { type: "string", title: "Data Type" },
        comment: { type: "string", title: "Comment", default: "" },
      },
      type: "object",
      required: ["name", "data_type"],
      title: "MetaColumnOut",
    },
    MetaSchemaIn: {
      properties: {
        tenant_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Tenant Key",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
      },
      type: "object",
      required: ["tenant_key"],
      title: "MetaSchemaIn",
    },
    MetaSchemaOut: {
      properties: {
        tables: {
          items: { $ref: "#/$defs/MetaTableOut" },
          type: "array",
          title: "Tables",
        },
        truncated: { type: "boolean", title: "Truncated" },
      },
      type: "object",
      required: ["tables", "truncated"],
      title: "MetaSchemaOut",
    },
    MetaTableOut: {
      properties: {
        schema: { type: "string", title: "Schema" },
        name: { type: "string", title: "Name" },
        comment: { type: "string", title: "Comment", default: "" },
        columns: {
          items: { $ref: "#/$defs/MetaColumnOut" },
          type: "array",
          title: "Columns",
        },
      },
      type: "object",
      required: ["schema", "name", "columns"],
      title: "MetaTableOut",
    },
    MetricHitOut: {
      properties: {
        metric_id: { type: "string", title: "Metric Id" },
        metric_name: { type: "string", title: "Metric Name" },
        sql_template: { type: "string", title: "Sql Template" },
        updated_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Updated At",
        },
      },
      type: "object",
      required: ["metric_id", "metric_name", "sql_template"],
      title: "MetricHitOut",
      description: "语义层口径命中(标识/名称/口径表达式/修订时间)。",
    },
    MetricIn: {
      properties: {
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
        metric_name: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Metric Name",
        },
        sql_template: {
          type: "string",
          maxLength: 4000,
          minLength: 1,
          title: "Sql Template",
        },
        business_terms: {
          items: { type: "string" },
          type: "array",
          maxItems: 16,
          title: "Business Terms",
        },
        enabled: { type: "boolean", title: "Enabled", default: true },
        visible_scope: {
          type: "string",
          pattern: "^(ALL|DEPT)$",
          title: "Visible Scope",
          default: "ALL",
        },
        dept_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Dept Id",
        },
      },
      type: "object",
      required: ["datasource_key", "metric_name", "sql_template"],
      title: "MetricIn",
    },
    MetricOut: {
      properties: {
        id: { type: "string", title: "Id" },
        metric_name: { type: "string", title: "Metric Name" },
        sql_template: { type: "string", title: "Sql Template" },
        business_terms: {
          items: { type: "string" },
          type: "array",
          title: "Business Terms",
        },
        enabled: { type: "boolean", title: "Enabled" },
        updated_at: { type: "string", title: "Updated At" },
        visible_scope: {
          type: "string",
          title: "Visible Scope",
          default: "ALL",
        },
        dept_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Dept Id",
        },
      },
      type: "object",
      required: [
        "id",
        "metric_name",
        "sql_template",
        "business_terms",
        "enabled",
        "updated_at",
      ],
      title: "MetricOut",
    },
    MissingColumnsOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        missing: { items: { type: "string" }, type: "array", title: "Missing" },
        missing_count: { type: "integer", title: "Missing Count" },
      },
      type: "object",
      required: ["schema_name", "table_name", "missing", "missing_count"],
      title: "MissingColumnsOut",
    },
    MutateOut: {
      properties: {
        report_id: { type: "string", title: "Report Id" },
        version: { type: "integer", title: "Version" },
        status: { type: "string", title: "Status" },
      },
      type: "object",
      required: ["report_id", "version", "status"],
      title: "MutateOut",
    },
    OpenAiChatIn: {
      properties: {
        model: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Model",
        },
        messages: {
          items: { $ref: "#/$defs/OpenAiMessage" },
          type: "array",
          title: "Messages",
        },
      },
      type: "object",
      required: ["messages"],
      title: "OpenAiChatIn",
    },
    OpenAiMessage: {
      properties: {
        role: { type: "string", title: "Role" },
        content: { type: "string", title: "Content" },
      },
      type: "object",
      required: ["role", "content"],
      title: "OpenAiMessage",
    },
    OperLogItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        user_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Id",
        },
        user_nickname: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Nickname",
        },
        user_username: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Username",
        },
        action: { type: "string", title: "Action" },
        target: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Target",
        },
        detail: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Detail",
        },
        ip: { anyOf: [{ type: "string" }, { type: "null" }], title: "Ip" },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "user_id",
        "user_nickname",
        "action",
        "target",
        "detail",
        "ip",
        "created_at",
      ],
      title: "OperLogItemOut",
    },
    OperLogPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/OperLogItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "OperLogPageOut",
    },
    OptionsOut: {
      properties: {
        providers: {
          items: { $ref: "#/$defs/ProviderOption" },
          type: "array",
          title: "Providers",
        },
      },
      type: "object",
      required: ["providers"],
      title: "OptionsOut",
    },
    OverviewOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        up_count: { type: "integer", title: "Up Count" },
        down_count: { type: "integer", title: "Down Count" },
        corrected_count: { type: "integer", title: "Corrected Count" },
        down_rate: { type: "number", title: "Down Rate" },
        trend: {
          items: { $ref: "#/$defs/TrendItemOut" },
          type: "array",
          title: "Trend",
        },
      },
      type: "object",
      required: [
        "total",
        "up_count",
        "down_count",
        "corrected_count",
        "down_rate",
        "trend",
      ],
      title: "OverviewOut",
    },
    OwnershipIn: {
      properties: {
        team_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Team Id",
        },
      },
      type: "object",
      title: "OwnershipIn",
      description: "归属变更请求(team_id 空=转公共;非空=转该团队专属)。",
    },
    PackCreateIn: {
      properties: {
        pack_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Pack Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        version: {
          type: "string",
          maxLength: 32,
          minLength: 1,
          title: "Version",
        },
        description: {
          type: "string",
          maxLength: 500,
          title: "Description",
          default: "",
        },
        glossary: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Glossary",
          default: [],
        },
        fewshots: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Fewshots",
          default: [],
        },
        persona: { type: "string", title: "Persona", default: "" },
        metrics: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Metrics",
          default: [],
        },
      },
      type: "object",
      required: ["pack_key", "name", "version"],
      title: "PackCreateIn",
    },
    PackDetailOut: {
      properties: {
        id: { type: "string", title: "Id" },
        pack_key: { type: "string", title: "Pack Key" },
        name: { type: "string", title: "Name" },
        version: { type: "string", title: "Version" },
        description: { type: "string", title: "Description" },
        status: { type: "string", title: "Status" },
        content: {
          additionalProperties: true,
          type: "object",
          title: "Content",
        },
        bindings: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Bindings",
        },
      },
      type: "object",
      required: [
        "id",
        "pack_key",
        "name",
        "version",
        "description",
        "status",
        "content",
        "bindings",
      ],
      title: "PackDetailOut",
    },
    PackListOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/PackOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["items"],
      title: "PackListOut",
    },
    PackOut: {
      properties: {
        id: { type: "string", title: "Id" },
        pack_key: { type: "string", title: "Pack Key" },
        name: { type: "string", title: "Name" },
        version: { type: "string", title: "Version" },
        description: { type: "string", title: "Description" },
        status: { type: "string", title: "Status" },
      },
      type: "object",
      required: ["id", "pack_key", "name", "version", "description", "status"],
      title: "PackOut",
    },
    ParamDeclIn: {
      properties: {
        name: { type: "string", maxLength: 64, minLength: 1, title: "Name" },
        type: { type: "string", maxLength: 16, minLength: 1, title: "Type" },
        required: { type: "boolean", title: "Required", default: false },
        description: {
          type: "string",
          maxLength: 200,
          title: "Description",
          default: "",
        },
      },
      type: "object",
      required: ["name", "type"],
      title: "ParamDeclIn",
    },
    PendingToolCallOut: {
      properties: {
        confirm_id: { type: "string", title: "Confirm Id" },
        tool_name: { type: "string", title: "Tool Name" },
        args_digest: { type: "string", title: "Args Digest", default: "" },
      },
      type: "object",
      required: ["confirm_id", "tool_name"],
      title: "PendingToolCallOut",
      description: "待确认写工具调用:确认卡片渲染要素。",
    },
    PermOut: {
      properties: {
        id: { type: "string", title: "Id" },
        perm_code: { type: "string", title: "Perm Code" },
        perm_name: { type: "string", title: "Perm Name" },
        sort: { type: "integer", title: "Sort" },
      },
      type: "object",
      required: ["id", "perm_code", "perm_name", "sort"],
      title: "PermOut",
    },
    PlanStepIn: {
      properties: {
        goal: { type: "string", maxLength: 2000, minLength: 1, title: "Goal" },
        require_confirm: {
          type: "boolean",
          title: "Require Confirm",
          default: false,
        },
      },
      type: "object",
      required: ["goal"],
      title: "PlanStepIn",
    },
    PrefsIn: {
      properties: {
        preferred_datasource: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Preferred Datasource",
        },
        default_time_range: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Default Time Range",
        },
        metric_aliases: {
          anyOf: [
            { items: { type: "string" }, type: "array" },
            { type: "null" },
          ],
          title: "Metric Aliases",
        },
        chart_pref: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Chart Pref",
        },
        language: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Language",
        },
        timezone: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Timezone",
        },
        display_style: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Display Style",
        },
      },
      additionalProperties: false,
      type: "object",
      title: "PrefsIn",
    },
    PrefsOut: {
      properties: {
        prefs: { additionalProperties: true, type: "object", title: "Prefs" },
      },
      type: "object",
      required: ["prefs"],
      title: "PrefsOut",
    },
    ProbeExpansionOut: {
      properties: {
        enabled: { type: "boolean", title: "Enabled" },
        tables: {
          items: { $ref: "#/$defs/ProbeExpansionTableOut" },
          type: "array",
          title: "Tables",
        },
        fused_after: {
          items: { $ref: "#/$defs/ProbeSchemaTableOut" },
          type: "array",
          title: "Fused After",
        },
      },
      type: "object",
      required: ["enabled", "tables", "fused_after"],
      title: "ProbeExpansionOut",
      description: "关系扩展明细:enabled=False 或零关系行时 tables 为空表。",
    },
    ProbeExpansionTableOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        via: { $ref: "#/$defs/ProbeExpansionViaOut" },
      },
      type: "object",
      required: ["schema_name", "table_name", "via"],
      title: "ProbeExpansionTableOut",
      description: "补入的扩展表 + 经由关系。",
    },
    ProbeExpansionViaOut: {
      properties: {
        source_table: { type: "string", title: "Source Table" },
        target_table: { type: "string", title: "Target Table" },
        join_keys: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          title: "Join Keys",
        },
        source: { type: "string", title: "Source" },
      },
      type: "object",
      required: ["source_table", "target_table", "join_keys", "source"],
      title: "ProbeExpansionViaOut",
      description: "扩展经由的关系行(rag.table_relations 行投影)。",
    },
    ProbeFusedHitOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        score: { type: "number", title: "Score" },
      },
      type: "object",
      required: ["schema_name", "table_name", "score"],
      title: "ProbeFusedHitOut",
      description: "融合序命中(RRF 融合分,只用于排序与观测,非相关度)。",
    },
    ProbeHitOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        score: { type: "number", title: "Score" },
        source: { type: "string", title: "Source" },
      },
      type: "object",
      required: ["schema_name", "table_name", "score", "source"],
      title: "ProbeHitOut",
      description:
        "单路命中项(score=该路原生分:向量路余弦相似度,全文路 ts_rank)。",
    },
    ProbeRouteOut: {
      properties: {
        hits: {
          items: { $ref: "#/$defs/ProbeHitOut" },
          type: "array",
          title: "Hits",
        },
        verdict: {
          anyOf: [{ $ref: "#/$defs/ProbeVerdictOut" }, { type: "null" }],
        },
        dropped: { type: "integer", title: "Dropped" },
      },
      type: "object",
      required: ["hits", "dropped"],
      title: "ProbeRouteOut",
      description:
        "单路(向量/全文)投影:命中明细 + 判定结论 + 被裁命中数。\n\ndropped>0 = 该路不过闸整路被裁(恒裁剪形态,命中共进退)。",
    },
    ProbeSchemaTableOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
      },
      type: "object",
      required: ["schema_name", "table_name"],
      title: "ProbeSchemaTableOut",
      description: "两段限定名(扩展后融合集合项)。",
    },
    ProbeVerdictOut: {
      properties: {
        passed: { type: "boolean", title: "Passed" },
        reason: { type: "string", title: "Reason" },
        top1: { type: "number", title: "Top1" },
        mean_top_n: { type: "number", title: "Mean Top N" },
        count: { type: "integer", title: "Count" },
      },
      type: "object",
      required: ["passed", "reason", "top1", "mean_top_n", "count"],
      title: "ProbeVerdictOut",
      description:
        "闸门判定结论(gate.GateVerdict.as_dict 投影:判定归因与分值观测)。",
    },
    ProgressIn: {
      properties: {
        report_id: { type: "string", title: "Report Id" },
        phase: { type: "string", title: "Phase" },
        tenant_key: { type: "string", title: "Tenant Key", default: "default" },
      },
      type: "object",
      required: ["report_id", "phase"],
      title: "ProgressIn",
    },
    ProgressOut: {
      properties: {
        report_id: { type: "string", title: "Report Id" },
        status: { type: "string", title: "Status" },
        phase: { type: "string", title: "Phase" },
      },
      type: "object",
      required: ["report_id", "status", "phase"],
      title: "ProgressOut",
    },
    ProviderOption: {
      properties: {
        provider: { type: "string", title: "Provider" },
        display_name: { type: "string", title: "Display Name" },
      },
      type: "object",
      required: ["provider", "display_name"],
      title: "ProviderOption",
    },
    PublishIn: {
      properties: {
        greeting: {
          type: "string",
          maxLength: 500,
          title: "Greeting",
          default: "",
        },
        suggestions: {
          items: { type: "string" },
          type: "array",
          maxItems: 5,
          title: "Suggestions",
        },
      },
      type: "object",
      title: "PublishIn",
    },
    QualityContextItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        status: { type: "string", title: "Status" },
        fallback_type: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fallback Type",
        },
        score: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Score",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: ["id", "status", "created_at"],
      title: "QualityContextItemOut",
      description: "同会话轮次轻量行(诊断标记按已合并字段携带)。",
    },
    QualityContextOut: {
      properties: {
        session_id: { type: "string", title: "Session Id" },
        items: {
          items: { $ref: "#/$defs/QualityContextItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["session_id", "items"],
      title: "QualityContextOut",
    },
    QualityOverviewOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        success: { type: "integer", title: "Success" },
        no_data: { type: "integer", title: "No Data" },
        blocked: { type: "integer", title: "Blocked" },
        failed: { type: "integer", title: "Failed" },
        no_data_rate: { type: "number", title: "No Data Rate" },
        chitchat_count: {
          type: "integer",
          title: "Chitchat Count",
          default: 0,
        },
        clarify_count: { type: "integer", title: "Clarify Count", default: 0 },
        blocked_rate: { type: "number", title: "Blocked Rate" },
        failed_rate: { type: "number", title: "Failed Rate" },
        selfheal_rate: { type: "number", title: "Selfheal Rate" },
        down_rate: { type: "number", title: "Down Rate" },
        avg_tokens: { type: "number", title: "Avg Tokens" },
        avg_score: {
          anyOf: [{ type: "number" }, { type: "null" }],
          title: "Avg Score",
        },
        low_score_count: {
          type: "integer",
          title: "Low Score Count",
          default: 0,
        },
        low_score_threshold: {
          type: "integer",
          title: "Low Score Threshold",
          default: 60,
        },
      },
      type: "object",
      required: [
        "total",
        "success",
        "no_data",
        "blocked",
        "failed",
        "no_data_rate",
        "blocked_rate",
        "failed_rate",
        "selfheal_rate",
        "down_rate",
        "avg_tokens",
      ],
      title: "QualityOverviewOut",
    },
    QualityPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/QualityRoundItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "QualityPageOut",
    },
    QualityPromptSliceRow: {
      properties: {
        prompt_version: { type: "string", title: "Prompt Version" },
        rounds: { type: "integer", title: "Rounds" },
        success: { type: "integer", title: "Success" },
        no_data: { type: "integer", title: "No Data" },
        blocked: { type: "integer", title: "Blocked" },
        failed: { type: "integer", title: "Failed" },
      },
      type: "object",
      required: [
        "prompt_version",
        "rounds",
        "success",
        "no_data",
        "blocked",
        "failed",
      ],
      title: "QualityPromptSliceRow",
    },
    QualityRoundItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        resolved_question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Resolved Question",
        },
        status: { type: "string", title: "Status" },
        retry_count: { type: "integer", title: "Retry Count" },
        rating: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Rating",
        },
        trace_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Trace Id",
        },
        prompt_version: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Prompt Version",
        },
        datasource_key: { type: "string", title: "Datasource Key" },
        session_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Session Id",
        },
        fallback_type: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fallback Type",
        },
        score: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Score",
        },
        grounding: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Grounding",
        },
        coverage: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Coverage",
        },
      },
      type: "object",
      required: ["id", "created_at", "status", "retry_count", "datasource_key"],
      title: "QualityRoundItemOut",
    },
    QualityScoreDetailOut: {
      properties: {
        message_id: { type: "string", title: "Message Id" },
        score: { type: "integer", title: "Score" },
        grounding: { type: "integer", title: "Grounding" },
        coverage: { type: "integer", title: "Coverage" },
        decomposition: {
          additionalProperties: true,
          type: "object",
          title: "Decomposition",
        },
        score_model: { type: "string", title: "Score Model" },
        scored_at: { type: "string", format: "date-time", title: "Scored At" },
      },
      type: "object",
      required: [
        "message_id",
        "score",
        "grounding",
        "coverage",
        "decomposition",
        "score_model",
        "scored_at",
      ],
      title: "QualityScoreDetailOut",
      description:
        "单轮评分详情(spec「轮次质量观测」审计分解;404=未评分或越权)。",
    },
    QualitySliceRow: {
      properties: {
        datasource_key: { type: "string", title: "Datasource Key" },
        rounds: { type: "integer", title: "Rounds" },
        success: { type: "integer", title: "Success" },
        no_data: { type: "integer", title: "No Data" },
        blocked: { type: "integer", title: "Blocked" },
        failed: { type: "integer", title: "Failed" },
      },
      type: "object",
      required: [
        "datasource_key",
        "rounds",
        "success",
        "no_data",
        "blocked",
        "failed",
      ],
      title: "QualitySliceRow",
    },
    QualityStageTraceOut: {
      properties: {
        message_id: { type: "string", title: "Message Id" },
        stage_trace: {
          anyOf: [
            {
              items: { additionalProperties: true, type: "object" },
              type: "array",
            },
            { type: "null" },
          ],
          title: "Stage Trace",
        },
      },
      type: "object",
      required: ["message_id"],
      title: "QualityStageTraceOut",
      description: "节点阶段轨迹(有序数组;null=同步轮/无帧流,如实为空)。",
    },
    QualitySummaryOut: {
      properties: {
        overview: { $ref: "#/$defs/QualityOverviewOut" },
        trend: {
          items: { $ref: "#/$defs/QualityTrendPoint" },
          type: "array",
          title: "Trend",
        },
        by_datasource: {
          items: { $ref: "#/$defs/QualitySliceRow" },
          type: "array",
          title: "By Datasource",
        },
        by_prompt_version: {
          items: { $ref: "#/$defs/QualityPromptSliceRow" },
          type: "array",
          title: "By Prompt Version",
        },
        days: { type: "integer", title: "Days" },
      },
      type: "object",
      required: [
        "overview",
        "trend",
        "by_datasource",
        "by_prompt_version",
        "days",
      ],
      title: "QualitySummaryOut",
    },
    QualityTrendPoint: {
      properties: {
        date: { type: "string", title: "Date" },
        rounds: { type: "integer", title: "Rounds" },
        success: { type: "integer", title: "Success" },
        no_data: { type: "integer", title: "No Data" },
        blocked: { type: "integer", title: "Blocked" },
        failed: { type: "integer", title: "Failed" },
      },
      type: "object",
      required: ["date", "rounds", "success", "no_data", "blocked", "failed"],
      title: "QualityTrendPoint",
    },
    RatingIn: {
      properties: {
        score: { type: "integer", maximum: 5, minimum: 1, title: "Score" },
      },
      type: "object",
      required: ["score"],
      title: "RatingIn",
    },
    RecallProbeOut: {
      properties: {
        question: { type: "string", title: "Question" },
        gate_skipped: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Gate Skipped",
        },
        expansion: { $ref: "#/$defs/ProbeExpansionOut" },
        vector: { $ref: "#/$defs/ProbeRouteOut" },
        fulltext: { $ref: "#/$defs/ProbeRouteOut" },
        fused: {
          items: { $ref: "#/$defs/ProbeFusedHitOut" },
          type: "array",
          title: "Fused",
        },
      },
      type: "object",
      required: ["question", "expansion", "vector", "fulltext", "fused"],
      title: "RecallProbeOut",
      description:
        "召回试跑响应(只读验证面;键集=service.recall_probe 现值锁定)。",
    },
    ReceiptItemOut: {
      properties: {
        idempotency_key: { type: "string", title: "Idempotency Key" },
        command_id: { type: "string", title: "Command Id" },
        action: { type: "string", title: "Action" },
        action_version: { type: "integer", title: "Action Version" },
        source: { type: "string", title: "Source" },
        status: { type: "string", title: "Status" },
        result: { additionalProperties: true, type: "object", title: "Result" },
        actor: { type: "string", title: "Actor" },
        updated_at: { type: "string", title: "Updated At" },
      },
      type: "object",
      required: [
        "idempotency_key",
        "command_id",
        "action",
        "action_version",
        "source",
        "status",
        "result",
        "actor",
        "updated_at",
      ],
      title: "ReceiptItemOut",
    },
    ReceiptOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/ReceiptItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["items"],
      title: "ReceiptOut",
    },
    ReindexOut: {
      properties: {
        deleted: { type: "integer", title: "Deleted" },
        rebuilt: { type: "integer", title: "Rebuilt" },
        enriched: { type: "integer", title: "Enriched" },
      },
      type: "object",
      required: ["deleted", "rebuilt", "enriched"],
      title: "ReindexOut",
    },
    RelationIn: {
      properties: {
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
        },
        source_table: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          pattern: "^[a-zA-Z_][a-zA-Z0-9_]*\\.[a-zA-Z_][a-zA-Z0-9_]*$",
          title: "Source Table",
        },
        target_table: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          pattern: "^[a-zA-Z_][a-zA-Z0-9_]*\\.[a-zA-Z_][a-zA-Z0-9_]*$",
          title: "Target Table",
        },
        join_keys: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          minItems: 1,
          title: "Join Keys",
        },
      },
      type: "object",
      required: ["datasource_key", "source_table", "target_table", "join_keys"],
      title: "RelationIn",
    },
    RelationOut: {
      properties: {
        id: { type: "string", title: "Id" },
        source_table: { type: "string", title: "Source Table" },
        target_table: { type: "string", title: "Target Table" },
        join_keys: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          title: "Join Keys",
        },
        source: { type: "string", title: "Source" },
        updated_at: { type: "string", title: "Updated At" },
      },
      type: "object",
      required: [
        "id",
        "source_table",
        "target_table",
        "join_keys",
        "source",
        "updated_at",
      ],
      title: "RelationOut",
    },
    RenameIn: {
      properties: {
        title: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Title",
        },
      },
      type: "object",
      required: ["title"],
      title: "RenameIn",
      description:
        "标题去除首尾空白后不能为空且最多 200 字;max_length=2000 仅限制过大的请求体。",
    },
    ReportDetailOut: {
      properties: {
        report: { $ref: "#/$defs/ReportItem" },
        version_meta: {
          anyOf: [{ $ref: "#/$defs/VersionMetaOut" }, { type: "null" }],
        },
        sections: {
          items: { $ref: "#/$defs/SectionOut" },
          type: "array",
          title: "Sections",
        },
        instruction: { type: "string", title: "Instruction", default: "" },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
      },
      type: "object",
      required: ["report", "version_meta", "sections"],
      title: "ReportDetailOut",
    },
    ReportItem: {
      properties: {
        id: { type: "string", title: "Id" },
        title: { type: "string", title: "Title" },
        status: { type: "string", title: "Status" },
        phase: { type: "string", title: "Phase" },
        datasource_key: { type: "string", title: "Datasource Key" },
        version: { type: "integer", title: "Version" },
        created_at: { type: "string", title: "Created At" },
        updated_at: { type: "string", title: "Updated At" },
      },
      type: "object",
      required: [
        "id",
        "title",
        "status",
        "phase",
        "datasource_key",
        "version",
        "created_at",
        "updated_at",
      ],
      title: "ReportItem",
    },
    ReportPageOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/ReportItem" },
          type: "array",
          title: "Items",
        },
        total: { type: "integer", title: "Total" },
        page: { type: "integer", title: "Page" },
        size: { type: "integer", title: "Size" },
      },
      type: "object",
      required: ["items", "total", "page", "size"],
      title: "ReportPageOut",
    },
    RerunIn: {
      properties: {
        message_id: { type: "string", format: "uuid", title: "Message Id" },
        sql: { type: "string", maxLength: 4000, minLength: 1, title: "Sql" },
      },
      type: "object",
      required: ["message_id", "sql"],
      title: "RerunIn",
      description:
        "SQL 卡再执行:message_id 引用 + 编辑后 SQL;归属与六层防御在\nservice/实现侧(与 EXPLAIN 同范式)。",
    },
    RetrievalProbeFusedOut: {
      properties: {
        child_id: { type: "string", title: "Child Id" },
        parent_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Parent Id",
        },
        rank: { type: "integer", title: "Rank" },
      },
      type: "object",
      required: ["child_id", "parent_id", "rank"],
      title: "RetrievalProbeFusedOut",
    },
    RetrievalProbeHitOut: {
      properties: {
        child_id: { type: "string", title: "Child Id" },
        parent_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Parent Id",
        },
        score: { type: "number", title: "Score" },
      },
      type: "object",
      required: ["child_id", "parent_id", "score"],
      title: "RetrievalProbeHitOut",
    },
    RetrievalProbeOut: {
      properties: {
        paths: {
          additionalProperties: {
            items: { $ref: "#/$defs/RetrievalProbeHitOut" },
            type: "array",
          },
          type: "object",
          title: "Paths",
        },
        fused_children: {
          items: { $ref: "#/$defs/RetrievalProbeFusedOut" },
          type: "array",
          title: "Fused Children",
        },
        parents: {
          items: { $ref: "#/$defs/RetrievalProbeParentOut" },
          type: "array",
          title: "Parents",
        },
      },
      type: "object",
      required: ["paths", "fused_children", "parents"],
      title: "RetrievalProbeOut",
    },
    RetrievalProbeParentOut: {
      properties: {
        parent_id: { type: "string", title: "Parent Id" },
        seq: { type: "integer", title: "Seq" },
        doc_name: { type: "string", title: "Doc Name" },
        source_ref: { type: "string", title: "Source Ref" },
        citation_key: { type: "string", title: "Citation Key" },
        content: { type: "string", title: "Content" },
        rank: { type: "integer", title: "Rank" },
      },
      type: "object",
      required: [
        "parent_id",
        "seq",
        "doc_name",
        "source_ref",
        "citation_key",
        "content",
        "rank",
      ],
      title: "RetrievalProbeParentOut",
    },
    RetrieveIn: {
      properties: {
        tenant_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Tenant Key",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
        question: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Question",
        },
        history: {
          items: { type: "string", maxLength: 2000 },
          type: "array",
          maxItems: 3,
          title: "History",
        },
        user_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "User Id",
        },
      },
      type: "object",
      required: ["tenant_key", "question"],
      title: "RetrieveIn",
    },
    RetrieveOut: {
      properties: {
        schema_hint: { type: "string", title: "Schema Hint" },
        history_sql: { type: "string", title: "History Sql" },
        relations_hint: {
          type: "string",
          title: "Relations Hint",
          default: "",
        },
        metric_hints: { type: "string", title: "Metric Hints", default: "" },
        metric_hits: {
          items: { $ref: "#/$defs/MetricHitOut" },
          type: "array",
          title: "Metric Hits",
          default: [],
        },
        schema_graph: {
          anyOf: [{ $ref: "#/$defs/SchemaGraphOut" }, { type: "null" }],
        },
        doc_hints: {
          items: { additionalProperties: { type: "string" }, type: "object" },
          type: "array",
          title: "Doc Hints",
          default: [],
        },
        skill_terms: { type: "string", title: "Skill Terms", default: "" },
        skill_examples: {
          type: "string",
          title: "Skill Examples",
          default: "",
        },
        skill_persona: { type: "string", title: "Skill Persona", default: "" },
      },
      type: "object",
      required: ["schema_hint", "history_sql"],
      title: "RetrieveOut",
    },
    RoleCreateIn: {
      properties: {
        role_key: {
          type: "string",
          maxLength: 100,
          minLength: 2,
          pattern: "^[A-Z][A-Z0-9_]+$",
          title: "Role Key",
        },
        role_name: {
          type: "string",
          maxLength: 100,
          minLength: 1,
          title: "Role Name",
        },
        data_scope: {
          type: "string",
          enum: ["ALL", "CUSTOM", "DEPT_AND_BELOW", "DEPT", "SELF"],
          title: "Data Scope",
          default: "SELF",
        },
        sql_scope: {
          type: "string",
          enum: ["SELECT_ONLY", "READ_DDL", "DML_DDL"],
          title: "Sql Scope",
          default: "SELECT_ONLY",
        },
        sort: { type: "integer", title: "Sort", default: 0 },
        data_scope_dept_ids: {
          anyOf: [
            { items: { type: "string", format: "uuid" }, type: "array" },
            { type: "null" },
          ],
          title: "Data Scope Dept Ids",
        },
      },
      type: "object",
      required: ["role_key", "role_name"],
      title: "RoleCreateIn",
    },
    RoleOut: {
      properties: {
        id: { type: "string", title: "Id" },
        role_key: { type: "string", title: "Role Key" },
        role_name: { type: "string", title: "Role Name" },
        data_scope: { type: "string", title: "Data Scope" },
        sql_scope: { type: "string", title: "Sql Scope" },
        sort: { type: "integer", title: "Sort" },
        builtin: { type: "boolean", title: "Builtin" },
        perm_ids: {
          items: { type: "string" },
          type: "array",
          title: "Perm Ids",
        },
        data_scope_dept_ids: {
          items: { type: "string" },
          type: "array",
          title: "Data Scope Dept Ids",
        },
      },
      type: "object",
      required: [
        "id",
        "role_key",
        "role_name",
        "data_scope",
        "sql_scope",
        "sort",
        "builtin",
        "perm_ids",
        "data_scope_dept_ids",
      ],
      title: "RoleOut",
    },
    RolePermsIn: {
      properties: {
        perm_ids: {
          anyOf: [
            { items: { type: "string", format: "uuid" }, type: "array" },
            { type: "null" },
          ],
          title: "Perm Ids",
        },
      },
      type: "object",
      title: "RolePermsIn",
    },
    RoleUpdateIn: {
      properties: {
        role_key: {
          type: "string",
          maxLength: 100,
          minLength: 2,
          pattern: "^[A-Z][A-Z0-9_]+$",
          title: "Role Key",
        },
        role_name: {
          type: "string",
          maxLength: 100,
          minLength: 1,
          title: "Role Name",
        },
        data_scope: {
          type: "string",
          enum: ["ALL", "CUSTOM", "DEPT_AND_BELOW", "DEPT", "SELF"],
          title: "Data Scope",
        },
        sql_scope: {
          type: "string",
          enum: ["SELECT_ONLY", "READ_DDL", "DML_DDL"],
          title: "Sql Scope",
        },
        sort: { type: "integer", title: "Sort" },
        data_scope_dept_ids: {
          anyOf: [
            { items: { type: "string", format: "uuid" }, type: "array" },
            { type: "null" },
          ],
          title: "Data Scope Dept Ids",
        },
      },
      type: "object",
      required: ["role_key", "role_name", "data_scope", "sql_scope", "sort"],
      title: "RoleUpdateIn",
      description: "role_key 创建后不可修改。",
    },
    RuleListOut: {
      properties: {
        rules: {
          items: { $ref: "#/$defs/GuardrailRuleOut" },
          type: "array",
          title: "Rules",
        },
      },
      type: "object",
      required: ["rules"],
      title: "RuleListOut",
    },
    RuleUpdateIn: {
      properties: {
        enabled: { type: "boolean", title: "Enabled" },
        action: {
          anyOf: [
            { type: "string", enum: ["blocked", "masked"] },
            { type: "null" },
          ],
          title: "Action",
        },
        threshold: {
          anyOf: [
            { type: "integer", maximum: 100, minimum: 1 },
            { type: "null" },
          ],
          title: "Threshold",
        },
      },
      type: "object",
      required: ["enabled"],
      title: "RuleUpdateIn",
    },
    SchemaGraphColumnOut: {
      properties: {
        name: { type: "string", title: "Name" },
        data_type: { type: "string", title: "Data Type" },
        comment: { type: "string", title: "Comment", default: "" },
      },
      type: "object",
      required: ["name", "data_type"],
      title: "SchemaGraphColumnOut",
      description: "结构化图列(名称/类型/注释;不含任何连接信息)。",
    },
    SchemaGraphForeignKeyOut: {
      properties: {
        source_schema: { type: "string", title: "Source Schema" },
        source_table: { type: "string", title: "Source Table" },
        source_columns: {
          items: { type: "string" },
          type: "array",
          title: "Source Columns",
          default: [],
        },
        target_schema: { type: "string", title: "Target Schema" },
        target_table: { type: "string", title: "Target Table" },
        target_columns: {
          items: { type: "string" },
          type: "array",
          title: "Target Columns",
          default: [],
        },
      },
      type: "object",
      required: [
        "source_schema",
        "source_table",
        "target_schema",
        "target_table",
      ],
      title: "SchemaGraphForeignKeyOut",
      description: "结构化图外键边(多列按序配对)。",
    },
    SchemaGraphOut: {
      properties: {
        tables: {
          items: { $ref: "#/$defs/SchemaGraphTableOut" },
          type: "array",
          title: "Tables",
          default: [],
        },
        foreign_keys: {
          items: { $ref: "#/$defs/SchemaGraphForeignKeyOut" },
          type: "array",
          title: "Foreign Keys",
          default: [],
        },
        complete: { type: "boolean", title: "Complete", default: true },
        known_tables: {
          items: { type: "string" },
          type: "array",
          title: "Known Tables",
          default: [],
        },
      },
      type: "object",
      title: "SchemaGraphOut",
      description:
        "召回响应携带的结构化 Schema 图;complete=False 时调用方不得做硬范围拒绝。",
    },
    SchemaGraphTableOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
        comment: { type: "string", title: "Comment", default: "" },
        columns: {
          items: { $ref: "#/$defs/SchemaGraphColumnOut" },
          type: "array",
          title: "Columns",
          default: [],
        },
        primary_key: {
          items: { type: "string" },
          type: "array",
          title: "Primary Key",
          default: [],
        },
        role: { type: "string", title: "Role", default: "unknown" },
        source: { type: "string", title: "Source", default: "introspection" },
      },
      type: "object",
      required: ["schema_name", "table_name"],
      title: "SchemaGraphTableOut",
      description: "结构化图表、列、主键与可确定角色。",
    },
    SchemaTableOut: {
      properties: {
        schema_name: { type: "string", title: "Schema Name" },
        table_name: { type: "string", title: "Table Name" },
      },
      type: "object",
      required: ["schema_name", "table_name"],
      title: "SchemaTableOut",
    },
    SchemasOut: {
      properties: {
        datasource_key: { type: "string", title: "Datasource Key" },
        schemas: { items: { type: "string" }, type: "array", title: "Schemas" },
      },
      type: "object",
      required: ["datasource_key", "schemas"],
      title: "SchemasOut",
    },
    SectionOut: {
      properties: {
        id: { type: "string", title: "Id" },
        parent_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Parent Id",
        },
        title: { type: "string", title: "Title" },
        narrative_md: { type: "string", title: "Narrative Md" },
        bindings: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Bindings",
        },
        chart: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Chart",
        },
        ord: { type: "integer", title: "Ord" },
        data_status: { type: "string", title: "Data Status" },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
      },
      type: "object",
      required: [
        "id",
        "parent_id",
        "title",
        "narrative_md",
        "bindings",
        "chart",
        "ord",
        "data_status",
        "fail_reason",
      ],
      title: "SectionOut",
    },
    SessionItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        title: { type: "string", title: "Title" },
        updated_at: {
          type: "string",
          format: "date-time",
          title: "Updated At",
        },
      },
      type: "object",
      required: ["id", "title", "updated_at"],
      title: "SessionItemOut",
    },
    SessionMessageOut: {
      properties: {
        id: { type: "string", title: "Id" },
        role: { type: "string", title: "Role" },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        summary: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Summary",
        },
        sql: { anyOf: [{ type: "string" }, { type: "null" }], title: "Sql" },
        status: { type: "string", title: "Status" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
        error_detail: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error Detail",
        },
        result: {
          anyOf: [{ $ref: "#/$defs/MessageResultOut" }, { type: "null" }],
        },
        rating: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Rating",
        },
        corrected_sql: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Corrected Sql",
        },
        feedback_comment: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Feedback Comment",
        },
        attachments: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Attachments",
          default: [],
        },
      },
      type: "object",
      required: ["id", "role", "status"],
      title: "SessionMessageOut",
    },
    SharedCatalogOut: {
      properties: {
        bot_key: { type: "string", title: "Bot Key" },
        listing: { type: "string", title: "Listing" },
        visibility: { type: "string", title: "Visibility" },
        entitlement_key: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Entitlement Key",
        },
      },
      type: "object",
      required: ["bot_key", "listing", "visibility", "entitlement_key"],
      title: "SharedCatalogOut",
    },
    SharedEntitlementOut: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        status: { type: "string", title: "Status" },
        features: {
          items: { type: "string" },
          type: "array",
          title: "Features",
        },
        entitlement_keys: {
          items: { type: "string" },
          type: "array",
          title: "Entitlement Keys",
        },
        quota_calls_daily: { type: "integer", title: "Quota Calls Daily" },
        quota_calls_monthly: { type: "integer", title: "Quota Calls Monthly" },
        quota_bot_calls_daily: {
          type: "integer",
          title: "Quota Bot Calls Daily",
        },
      },
      type: "object",
      required: [
        "tenant_key",
        "status",
        "features",
        "entitlement_keys",
        "quota_calls_daily",
        "quota_calls_monthly",
        "quota_bot_calls_daily",
      ],
      title: "SharedEntitlementOut",
    },
    SharedPackageOut: {
      properties: {
        package_key: { type: "string", title: "Package Key" },
        version: { type: "integer", title: "Version" },
        status: { type: "string", title: "Status" },
        rule_names: {
          items: { type: "string" },
          type: "array",
          title: "Rule Names",
        },
        tenant_keys: {
          items: { type: "string" },
          type: "array",
          title: "Tenant Keys",
        },
      },
      type: "object",
      required: [
        "package_key",
        "version",
        "status",
        "rule_names",
        "tenant_keys",
      ],
      title: "SharedPackageOut",
    },
    SharedSnapshotOut: {
      properties: {
        present: { type: "boolean", title: "Present" },
        version: { type: "integer", title: "Version" },
        observed_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Observed At",
        },
        stale: { type: "boolean", title: "Stale" },
        tenants: {
          items: { $ref: "#/$defs/SharedTenantOut" },
          type: "array",
          title: "Tenants",
        },
        entitlements: {
          items: { $ref: "#/$defs/SharedEntitlementOut" },
          type: "array",
          title: "Entitlements",
        },
        policy_packages: {
          items: { $ref: "#/$defs/SharedPackageOut" },
          type: "array",
          title: "Policy Packages",
        },
        catalog: {
          items: { $ref: "#/$defs/SharedCatalogOut" },
          type: "array",
          title: "Catalog",
        },
      },
      type: "object",
      required: [
        "present",
        "version",
        "observed_at",
        "stale",
        "tenants",
        "entitlements",
        "policy_packages",
        "catalog",
      ],
      title: "SharedSnapshotOut",
    },
    SharedTenantOut: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        tenant_name: { type: "string", title: "Tenant Name" },
        status: { type: "string", title: "Status" },
      },
      type: "object",
      required: ["tenant_key", "tenant_name", "status"],
      title: "SharedTenantOut",
    },
    SourceOut: {
      properties: {
        citation_key: { type: "string", title: "Citation Key" },
        source_ref: { type: "string", title: "Source Ref", default: "" },
        doc_name: { type: "string", title: "Doc Name", default: "" },
      },
      type: "object",
      required: ["citation_key"],
      title: "SourceOut",
      description:
        "结构化来源条目(引用键/来源标识/文档名快照)。\n语义:纯数据道恒为空;知识道与融合道由净化后正文单向投影,可非空。",
    },
    SqlAuditItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        trace_id: { type: "string", title: "Trace Id" },
        user_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Id",
        },
        user_nickname: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Nickname",
        },
        user_username: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "User Username",
        },
        tenant_id: { type: "string", title: "Tenant Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        datasource_key: { type: "string", title: "Datasource Key" },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        sql_text: { type: "string", title: "Sql Text" },
        verdict: { type: "string", title: "Verdict" },
        block_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Block Reason",
        },
        row_count: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Row Count",
        },
        duration_ms: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Duration Ms",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "trace_id",
        "user_id",
        "user_nickname",
        "tenant_id",
        "tenant_key",
        "datasource_key",
        "question",
        "sql_text",
        "verdict",
        "block_reason",
        "row_count",
        "duration_ms",
        "created_at",
      ],
      title: "SqlAuditItemOut",
    },
    SqlAuditPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/SqlAuditItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "SqlAuditPageOut",
    },
    SqlExecuteRequest: {
      properties: {
        trace_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Trace Id",
        },
        tenant_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Tenant Key",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
        sql_text: { type: "string", minLength: 1, title: "Sql Text" },
        user_id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "User Id",
        },
        question: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Question",
        },
        limit: {
          type: "integer",
          maximum: 10000,
          minimum: 1,
          title: "Limit",
          default: 1000,
        },
      },
      type: "object",
      required: ["trace_id", "tenant_key", "sql_text"],
      title: "SqlExecuteRequest",
    },
    SqlExecuteResponse: {
      properties: {
        verdict: {
          type: "string",
          enum: ["PASS", "BLOCKED", "ERROR"],
          title: "Verdict",
        },
        reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Reason",
        },
        columns: { items: { type: "string" }, type: "array", title: "Columns" },
        rows: {
          items: { items: {}, type: "array" },
          type: "array",
          title: "Rows",
        },
        row_count: { type: "integer", title: "Row Count" },
        duration_ms: { type: "integer", title: "Duration Ms" },
      },
      type: "object",
      required: ["verdict", "columns", "rows", "row_count", "duration_ms"],
      title: "SqlExecuteResponse",
    },
    StartOut: {
      properties: { authorize_url: { type: "string", title: "Authorize Url" } },
      type: "object",
      required: ["authorize_url"],
      title: "StartOut",
    },
    StepOut: {
      properties: {
        id: { type: "string", title: "Id" },
        ord: { type: "integer", title: "Ord" },
        goal: { type: "string", title: "Goal" },
        status: { type: "string", title: "Status" },
        require_confirm: { type: "boolean", title: "Require Confirm" },
        evidence: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Evidence",
        },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
        checkpoint: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Checkpoint",
        },
        collab_topology: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Collab Topology",
        },
      },
      type: "object",
      required: [
        "id",
        "ord",
        "goal",
        "status",
        "require_confirm",
        "evidence",
        "fail_reason",
        "checkpoint",
      ],
      title: "StepOut",
    },
    StepResultIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        step_id: { type: "string", title: "Step Id" },
        epoch: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Epoch",
        },
        status: { type: "string", title: "Status" },
        summary: { type: "string", title: "Summary", default: "" },
        evidence: {
          additionalProperties: true,
          type: "object",
          title: "Evidence",
        },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
        checkpoint: {
          additionalProperties: true,
          type: "object",
          title: "Checkpoint",
        },
        tokens_used: { type: "integer", title: "Tokens Used", default: 0 },
        appended_steps: {
          items: { $ref: "#/$defs/AppendedStepIn" },
          type: "array",
          title: "Appended Steps",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "step_id", "status"],
      title: "StepResultIn",
    },
    StepStartIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        step_id: { type: "string", title: "Step Id" },
        epoch: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Epoch",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "step_id"],
      title: "StepStartIn",
    },
    StepSuspendedIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        step_id: { type: "string", title: "Step Id" },
        epoch: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Epoch",
        },
        confirm_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Confirm Id",
        },
        tool_name: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Tool Name",
        },
        summary: { type: "string", title: "Summary", default: "" },
        thread_id: {
          type: "string",
          maxLength: 200,
          minLength: 1,
          title: "Thread Id",
        },
      },
      type: "object",
      required: [
        "task_id",
        "tenant_key",
        "step_id",
        "confirm_id",
        "tool_name",
        "thread_id",
      ],
      title: "StepSuspendedIn",
      description: "图内挂起上账载荷(编排回调;graph-checkpoint)。",
    },
    StoreBotDetailOut: {
      properties: {
        id: { type: "string", title: "Id" },
        bot_key: { type: "string", title: "Bot Key" },
        name: { type: "string", title: "Name" },
        greeting: { type: "string", title: "Greeting", default: "" },
        use_count: { type: "integer", title: "Use Count", default: 0 },
        avg_score: { type: "number", title: "Avg Score", default: 0 },
        rating_count: { type: "integer", title: "Rating Count", default: 0 },
        fav_count: { type: "integer", title: "Fav Count", default: 0 },
        favorited: { type: "boolean", title: "Favorited", default: false },
        published_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Published At",
        },
        suggestions: {
          items: { type: "string" },
          type: "array",
          title: "Suggestions",
          default: [],
        },
        tools: {
          items: { type: "string" },
          type: "array",
          title: "Tools",
          default: [],
        },
        my_rating: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "My Rating",
        },
        version_no: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Version No",
        },
        version_published_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Version Published At",
        },
      },
      type: "object",
      required: ["id", "bot_key", "name"],
      title: "StoreBotDetailOut",
    },
    StoreBotOut: {
      properties: {
        id: { type: "string", title: "Id" },
        bot_key: { type: "string", title: "Bot Key" },
        name: { type: "string", title: "Name" },
        greeting: { type: "string", title: "Greeting", default: "" },
        use_count: { type: "integer", title: "Use Count", default: 0 },
        avg_score: { type: "number", title: "Avg Score", default: 0 },
        rating_count: { type: "integer", title: "Rating Count", default: 0 },
        fav_count: { type: "integer", title: "Fav Count", default: 0 },
        favorited: { type: "boolean", title: "Favorited", default: false },
        published_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Published At",
        },
      },
      type: "object",
      required: ["id", "bot_key", "name"],
      title: "StoreBotOut",
      description:
        "商店列表项(J4 契约化;response_model 精确化,生成类型可消费)。",
    },
    StoreRunIn: {
      properties: {
        question: {
          type: "string",
          maxLength: 2000,
          minLength: 1,
          title: "Question",
        },
      },
      type: "object",
      required: ["question"],
      title: "StoreRunIn",
    },
    StoreRunOut: {
      properties: {
        success: { type: "boolean", title: "Success" },
        summary: { type: "string", title: "Summary", default: "" },
        error: { type: "string", title: "Error", default: "" },
      },
      type: "object",
      required: ["success"],
      title: "StoreRunOut",
    },
    StreamErrorEvent: {
      properties: {
        type: { type: "string", const: "error", title: "Type" },
        data: { $ref: "#/$defs/StreamErrorOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamErrorEvent",
      description: "type=error 信封:仅传输/输出失败。",
    },
    StreamErrorOut: {
      properties: {
        error: { type: "string", title: "Error" },
        reason_code: {
          type: "string",
          enum: ["AGENT_UNAVAILABLE", "STREAM_OUTPUT_FAILED"],
          title: "Reason Code",
        },
      },
      additionalProperties: false,
      type: "object",
      required: ["error", "reason_code"],
      title: "StreamErrorOut",
      description:
        "error 终态事件 data:仅传输层/意外失败(业务三态走 result 事件)。\n\nreason_code 必填且枚举两种流错误;公开信封据此构造,内部队列不可再发\nreason_code=None 的裸错误。",
    },
    StreamResultEvent: {
      properties: {
        type: { type: "string", const: "result", title: "Type" },
        data: { $ref: "#/$defs/ChatQueryOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamResultEvent",
      description: "type=result 信封:data 与同步问答响应逐字段一致。",
    },
    StreamSqlEvent: {
      properties: {
        type: { type: "string", const: "sql", title: "Type" },
        data: { $ref: "#/$defs/StreamSqlOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamSqlEvent",
      description: "type=sql 信封。",
    },
    StreamSqlOut: {
      properties: { sql: { type: "string", title: "Sql" } },
      additionalProperties: false,
      type: "object",
      required: ["sql"],
      title: "StreamSqlOut",
      description:
        "sql 事件 data:validate 通过后提前下发,内容与终态落库 SQL 一致。",
    },
    StreamStageEvent: {
      properties: {
        type: { type: "string", const: "stage", title: "Type" },
        data: { $ref: "#/$defs/StreamStageOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamStageEvent",
      description: "type=stage 信封。",
    },
    StreamStageOut: {
      properties: {
        node: { type: "string", title: "Node" },
        status: { type: "string", enum: ["running", "done"], title: "Status" },
        retry_count: { type: "integer", title: "Retry Count", default: 0 },
      },
      additionalProperties: false,
      type: "object",
      required: ["node", "status"],
      title: "StreamStageOut",
      description: "stage 事件 data:节点粒度进度(status ∈ running/done)。",
    },
    StreamSummaryEvent: {
      properties: {
        type: { type: "string", const: "summary", title: "Type" },
        data: { $ref: "#/$defs/StreamSummaryOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamSummaryEvent",
      description: "type=summary 信封。",
    },
    StreamSummaryOut: {
      properties: { delta: { type: "string", title: "Delta" } },
      additionalProperties: false,
      type: "object",
      required: ["delta"],
      title: "StreamSummaryOut",
      description:
        "summary 事件 data:总结增量(当前为单条完整文案,逐事件追加=打字机语义)。",
    },
    StreamToolCallEvent: {
      properties: {
        type: { type: "string", const: "tool_call", title: "Type" },
        data: { $ref: "#/$defs/StreamToolCallOut" },
      },
      additionalProperties: false,
      type: "object",
      required: ["type", "data"],
      title: "StreamToolCallEvent",
      description: "type=tool_call 信封。",
    },
    StreamToolCallOut: {
      properties: {
        tool_name: { type: "string", title: "Tool Name" },
        status: {
          type: "string",
          enum: ["running", "done", "rejected"],
          title: "Status",
        },
        args_digest: { type: "string", title: "Args Digest", default: "" },
        result_digest: { type: "string", title: "Result Digest", default: "" },
      },
      additionalProperties: false,
      type: "object",
      required: ["tool_name", "status"],
      title: "StreamToolCallOut",
      description:
        "tool_call 事件 data(tool-calling):工具调用进度。\n\nstatus ∈ running/done/rejected(confirm_required 以 done 下发,result_digest\n含「待确认」);args_digest/result_digest 为截断摘要(审计同源口径)。",
    },
    SuggestionsOut: {
      properties: {
        items: { items: { type: "string" }, type: "array", title: "Items" },
      },
      type: "object",
      required: ["items"],
      title: "SuggestionsOut",
      description:
        "GET /chat/suggestions 响应:建议问句清单(空=无建议,静默隐藏)。",
    },
    TaskCreateOut: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        status: { type: "string", title: "Status" },
      },
      type: "object",
      required: ["task_id", "status"],
      title: "TaskCreateOut",
    },
    TaskDetailOut: {
      properties: {
        task: { $ref: "#/$defs/TaskItem" },
        steps: {
          items: { $ref: "#/$defs/StepOut" },
          type: "array",
          title: "Steps",
        },
        events: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Events",
        },
        budget_config: {
          additionalProperties: true,
          type: "object",
          title: "Budget Config",
        },
        budget_used: {
          additionalProperties: true,
          type: "object",
          title: "Budget Used",
        },
        trace_root: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Trace Root",
        },
        graph_confirms: {
          items: { $ref: "#/$defs/GraphConfirmOut" },
          type: "array",
          title: "Graph Confirms",
        },
      },
      type: "object",
      required: ["task", "steps", "events", "budget_config", "budget_used"],
      title: "TaskDetailOut",
    },
    TaskItem: {
      properties: {
        id: { type: "string", title: "Id" },
        goal: { type: "string", title: "Goal" },
        status: { type: "string", title: "Status" },
        datasource_key: { type: "string", title: "Datasource Key" },
        fail_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Fail Reason",
        },
        created_at: { type: "string", title: "Created At" },
        updated_at: { type: "string", title: "Updated At" },
      },
      type: "object",
      required: [
        "id",
        "goal",
        "status",
        "datasource_key",
        "fail_reason",
        "created_at",
        "updated_at",
      ],
      title: "TaskItem",
    },
    TaskMutateOut: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        status: { type: "string", title: "Status" },
      },
      type: "object",
      required: ["task_id", "status"],
      title: "TaskMutateOut",
    },
    TaskPageOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/TaskItem" },
          type: "array",
          title: "Items",
        },
        total: { type: "integer", title: "Total" },
        page: { type: "integer", title: "Page" },
        size: { type: "integer", title: "Size" },
      },
      type: "object",
      required: ["items", "total", "page", "size"],
      title: "TaskPageOut",
    },
    TaskPlanIn: {
      properties: {
        task_id: { type: "string", title: "Task Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        steps: {
          items: { $ref: "#/$defs/PlanStepIn" },
          type: "array",
          maxItems: 32,
          minItems: 1,
          title: "Steps",
        },
      },
      type: "object",
      required: ["task_id", "tenant_key", "steps"],
      title: "TaskPlanIn",
    },
    TeamDetailOut: {
      properties: {
        team: { $ref: "#/$defs/TeamOut" },
        members: {
          items: { $ref: "#/$defs/TeamMemberOut" },
          type: "array",
          title: "Members",
        },
      },
      type: "object",
      required: ["team", "members"],
      title: "TeamDetailOut",
    },
    TeamIn: {
      properties: {
        team_key: {
          type: "string",
          maxLength: 64,
          minLength: 2,
          title: "Team Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
      },
      type: "object",
      required: ["team_key", "name"],
      title: "TeamIn",
    },
    TeamMemberOut: {
      properties: {
        user_id: { type: "string", title: "User Id" },
        username: { type: "string", title: "Username" },
        role: { type: "string", title: "Role" },
      },
      type: "object",
      required: ["user_id", "username", "role"],
      title: "TeamMemberOut",
    },
    TeamOut: {
      properties: {
        id: { type: "string", title: "Id" },
        team_key: { type: "string", title: "Team Key" },
        name: { type: "string", title: "Name" },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
        role: { anyOf: [{ type: "string" }, { type: "null" }], title: "Role" },
      },
      type: "object",
      required: ["id", "team_key", "name"],
      title: "TeamOut",
      description:
        "团队信息；个人团队列表包含当前用户角色，管理列表不返回该字段。",
    },
    TeamRenameIn: {
      properties: {
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
      },
      type: "object",
      required: ["name"],
      title: "TeamRenameIn",
    },
    TenantOut: {
      properties: {
        id: { type: "string", title: "Id" },
        tenant_key: { type: "string", title: "Tenant Key" },
        tenant_name: { type: "string", title: "Tenant Name" },
      },
      type: "object",
      required: ["id", "tenant_key", "tenant_name"],
      title: "TenantOut",
    },
    TenantRefOut: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        tenant_name: { type: "string", title: "Tenant Name" },
      },
      type: "object",
      required: ["tenant_key", "tenant_name"],
      title: "TenantRefOut",
    },
    TestCallIn: {
      properties: {
        key: { type: "string", maxLength: 64, minLength: 2, title: "Key" },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        description: {
          type: "string",
          maxLength: 512,
          minLength: 1,
          title: "Description",
        },
        method: { type: "string", maxLength: 8, minLength: 1, title: "Method" },
        path_template: {
          type: "string",
          maxLength: 1024,
          minLength: 1,
          title: "Path Template",
        },
        params: {
          items: { $ref: "#/$defs/ParamDeclIn" },
          type: "array",
          title: "Params",
          default: [],
        },
        allowed_hosts: {
          items: { type: "string" },
          type: "array",
          maxItems: 10,
          title: "Allowed Hosts",
        },
        auth_type: {
          type: "string",
          maxLength: 16,
          minLength: 1,
          title: "Auth Type",
        },
        auth_secret: {
          anyOf: [{ type: "string", maxLength: 2048 }, { type: "null" }],
          title: "Auth Secret",
        },
        requires_confirmation: {
          anyOf: [{ type: "boolean" }, { type: "null" }],
          title: "Requires Confirmation",
        },
        timeout_ms: { type: "integer", minimum: 1, title: "Timeout Ms" },
        args: {
          additionalProperties: true,
          type: "object",
          title: "Args",
          default: {},
        },
        use_saved_secret: {
          type: "boolean",
          title: "Use Saved Secret",
          default: false,
        },
      },
      type: "object",
      required: [
        "key",
        "name",
        "description",
        "method",
        "path_template",
        "allowed_hosts",
        "auth_type",
        "timeout_ms",
      ],
      title: "TestCallIn",
      description:
        "测试调用入参:当前表单声明 + 可选示例入参;use_saved_secret=True 且\n材料留空时用存量凭证(编辑态先测后存的空框语义)。",
    },
    TestCallOut: {
      properties: {
        ok: { type: "boolean", title: "Ok" },
        status_code: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Status Code",
        },
        body_summary: { type: "string", title: "Body Summary", default: "" },
        failure: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Failure",
        },
        duration_ms: { type: "integer", title: "Duration Ms" },
      },
      type: "object",
      required: ["ok", "duration_ms"],
      title: "TestCallOut",
    },
    TestConnectionIn: {
      properties: {
        id: {
          anyOf: [{ type: "string", format: "uuid" }, { type: "null" }],
          title: "Id",
        },
        host: { type: "string", maxLength: 255, minLength: 1, title: "Host" },
        port: {
          type: "integer",
          maximum: 65535,
          minimum: 1,
          title: "Port",
          default: 5432,
        },
        database: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Database",
        },
        username: {
          type: "string",
          maxLength: 128,
          minLength: 1,
          title: "Username",
        },
        password: {
          anyOf: [
            { type: "string", maxLength: 128, minLength: 1 },
            { type: "null" },
          ],
          title: "Password",
        },
      },
      type: "object",
      required: ["host", "database", "username"],
      title: "TestConnectionIn",
    },
    TestConnectionOut: {
      properties: {
        ok: { type: "boolean", title: "Ok" },
        reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Reason",
        },
      },
      type: "object",
      required: ["ok"],
      title: "TestConnectionOut",
    },
    TimelineEvent: {
      properties: {
        kind: { type: "string", enum: ["LLM", "TOOL", "SQL"], title: "Kind" },
        name: { type: "string", title: "Name" },
        status: { type: "string", title: "Status" },
        tokens: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Tokens",
        },
        latency_ms: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Latency Ms",
        },
        ts: { type: "string", format: "date-time", title: "Ts" },
      },
      type: "object",
      required: ["kind", "name", "status", "ts"],
      title: "TimelineEvent",
    },
    TokenPairOut: {
      properties: {
        access_token: { type: "string", title: "Access Token" },
        refresh_token: { type: "string", title: "Refresh Token" },
        token_type: { type: "string", title: "Token Type", default: "Bearer" },
      },
      type: "object",
      required: ["access_token", "refresh_token"],
      title: "TokenPairOut",
    },
    ToolAuditItem: {
      properties: {
        id: { type: "string", title: "Id" },
        trace_id: { type: "string", title: "Trace Id" },
        user_id: { type: "string", title: "User Id" },
        tenant_id: { type: "string", title: "Tenant Id" },
        tool_name: { type: "string", title: "Tool Name" },
        args_digest: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Args Digest",
        },
        status: { type: "string", title: "Status" },
        error_reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error Reason",
        },
        duration_ms: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Duration Ms",
        },
        datasource_key: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Datasource Key",
        },
        domain: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Domain",
        },
        idempotency_key: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Idempotency Key",
        },
        expires_at: {
          anyOf: [{ type: "string", format: "date-time" }, { type: "null" }],
          title: "Expires At",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "trace_id",
        "user_id",
        "tenant_id",
        "tool_name",
        "args_digest",
        "status",
        "error_reason",
        "duration_ms",
        "datasource_key",
        "idempotency_key",
        "expires_at",
        "created_at",
      ],
      title: "ToolAuditItem",
    },
    ToolAuditPageOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/ToolAuditItem" },
          type: "array",
          title: "Items",
        },
        total: { type: "integer", title: "Total" },
        page: { type: "integer", title: "Page" },
        size: { type: "integer", title: "Size" },
      },
      type: "object",
      required: ["items", "total", "page", "size"],
      title: "ToolAuditPageOut",
    },
    ToolConfirmIn: {
      properties: {
        confirm_id: { type: "string", format: "uuid", title: "Confirm Id" },
        decision: {
          type: "string",
          enum: ["confirm", "reject"],
          title: "Decision",
        },
        session_id: { type: "string", format: "uuid", title: "Session Id" },
      },
      type: "object",
      required: ["confirm_id", "decision", "session_id"],
      title: "ToolConfirmIn",
    },
    ToolConfirmOut: {
      properties: {
        status: {
          type: "string",
          enum: [
            "executed",
            "confirm_rejected",
            "confirm_expired",
            "rejected",
            "error",
          ],
          title: "Status",
        },
        reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Reason",
        },
        result: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Result",
        },
      },
      type: "object",
      required: ["status"],
      title: "ToolConfirmOut",
    },
    ToolExecuteIn: {
      properties: {
        trace_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Trace Id",
        },
        tenant_key: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Tenant Key",
        },
        datasource_key: {
          type: "string",
          pattern: "^[a-z][a-z0-9_]{1,63}$",
          title: "Datasource Key",
          default: "default",
        },
        user_id: { type: "string", format: "uuid", title: "User Id" },
        tool_name: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Tool Name",
        },
        args: {
          additionalProperties: true,
          type: "object",
          title: "Args",
          default: {},
        },
        idempotency_key: {
          anyOf: [{ type: "string", maxLength: 128 }, { type: "null" }],
          title: "Idempotency Key",
        },
        channel: {
          type: "string",
          maxLength: 24,
          title: "Channel",
          default: "chat",
        },
      },
      type: "object",
      required: ["trace_id", "tenant_key", "user_id", "tool_name"],
      title: "ToolExecuteIn",
      description:
        "Agent 面 → 控制面的工具执行回调(ADR-002:Agent 不持凭证,只传标识与文本)。",
    },
    ToolExecuteOut: {
      properties: {
        status: {
          type: "string",
          enum: [
            "executed",
            "rejected",
            "error",
            "rate_limited",
            "confirm_required",
          ],
          title: "Status",
        },
        reason: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Reason",
        },
        result: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Result",
        },
        confirm_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Confirm Id",
        },
        duration_ms: { type: "integer", title: "Duration Ms", default: 0 },
      },
      type: "object",
      required: ["status"],
      title: "ToolExecuteOut",
      description:
        "三态+确认挂起:executed(含结果)/ rejected(防御拒绝,含原因)/\nerror(执行异常)/ rate_limited(限流)/ confirm_required(写工具待确认)。",
    },
    ToolItemOut: {
      properties: {
        name: { type: "string", title: "Name" },
        description: { type: "string", title: "Description" },
        perm_code: { type: "string", title: "Perm Code" },
        write: { type: "boolean", title: "Write" },
        require_confirm: { type: "boolean", title: "Require Confirm" },
        enabled: { type: "boolean", title: "Enabled", default: true },
        server_key: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Server Key",
        },
      },
      type: "object",
      required: [
        "name",
        "description",
        "perm_code",
        "write",
        "require_confirm",
      ],
      title: "ToolItemOut",
    },
    ToolListOut: {
      properties: {
        tools: {
          items: { $ref: "#/$defs/ToolItemOut" },
          type: "array",
          title: "Tools",
        },
      },
      type: "object",
      required: ["tools"],
      title: "ToolListOut",
    },
    ToolResolveConfirmIn: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        user_id: { type: "string", format: "uuid", title: "User Id" },
        confirm_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Confirm Id",
        },
        decision: {
          type: "string",
          enum: ["approve", "reject"],
          title: "Decision",
        },
      },
      type: "object",
      required: ["tenant_key", "user_id", "confirm_id", "decision"],
      title: "ToolResolveConfirmIn",
      description:
        "任务通道确认兑现载荷(graph-checkpoint;决策权威=任务域确认行,\n本端点只做兑换与全量复校)。",
    },
    TrendItemOut: {
      properties: {
        day: { type: "string", title: "Day" },
        up: { type: "integer", title: "Up" },
        down: { type: "integer", title: "Down" },
      },
      type: "object",
      required: ["day", "up", "down"],
      title: "TrendItemOut",
    },
    TriggerIn: {
      properties: {
        tg_key: {
          type: "string",
          maxLength: 64,
          minLength: 2,
          title: "Tg Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        type: {
          type: "string",
          pattern: "^(CRON|WEBHOOK|EMAIL)$",
          title: "Type",
        },
        target_type: {
          type: "string",
          pattern: "^(workflow|bot|task)$",
          title: "Target Type",
        },
        target_key: {
          type: "string",
          maxLength: 64,
          title: "Target Key",
          default: "",
        },
        cron_expr: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Cron Expr",
        },
        min_interval_sec: {
          type: "integer",
          maximum: 86400,
          minimum: 0,
          title: "Min Interval Sec",
          default: 60,
        },
        adapter: {
          type: "string",
          pattern: "^(custom|github)$",
          title: "Adapter",
          default: "custom",
        },
        notify_url: {
          type: "string",
          maxLength: 512,
          title: "Notify Url",
          default: "",
        },
        secret: {
          type: "string",
          maxLength: 256,
          title: "Secret",
          default: "",
        },
        max_retries: {
          type: "integer",
          maximum: 5,
          minimum: 0,
          title: "Max Retries",
          default: 0,
        },
        task_template: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Task Template",
        },
        imap_host: {
          type: "string",
          maxLength: 255,
          title: "Imap Host",
          default: "",
        },
        imap_port: {
          type: "integer",
          maximum: 65535,
          minimum: 1,
          title: "Imap Port",
          default: 993,
        },
        imap_tls: { type: "boolean", title: "Imap Tls", default: true },
        imap_user: {
          type: "string",
          maxLength: 255,
          title: "Imap User",
          default: "",
        },
        imap_password: {
          type: "string",
          maxLength: 256,
          title: "Imap Password",
          default: "",
        },
        imap_folder: {
          type: "string",
          maxLength: 255,
          title: "Imap Folder",
          default: "INBOX",
        },
        imap_poll_interval_sec: {
          type: "integer",
          maximum: 86400,
          minimum: 1,
          title: "Imap Poll Interval Sec",
          default: 300,
        },
      },
      type: "object",
      required: ["tg_key", "name", "type", "target_type"],
      title: "TriggerIn",
    },
    TriggerOut: {
      properties: {
        id: { type: "string", title: "Id" },
        tg_key: { type: "string", title: "Tg Key" },
        name: { type: "string", title: "Name" },
        type: { type: "string", title: "Type" },
        target_type: { type: "string", title: "Target Type" },
        target_key: { type: "string", title: "Target Key" },
        cron_expr: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Cron Expr",
        },
        min_interval_sec: { type: "integer", title: "Min Interval Sec" },
        webhook_path: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Webhook Path",
        },
        adapter: { type: "string", title: "Adapter" },
        notify_url: { type: "string", title: "Notify Url", default: "" },
        enabled: { type: "boolean", title: "Enabled" },
        has_secret: { type: "boolean", title: "Has Secret" },
        last_fired_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Last Fired At",
        },
        next_fire_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Next Fire At",
        },
        max_retries: { type: "integer", title: "Max Retries", default: 0 },
        task_template: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Task Template",
        },
        imap_host: { type: "string", title: "Imap Host", default: "" },
        imap_port: { type: "integer", title: "Imap Port", default: 993 },
        imap_tls: { type: "boolean", title: "Imap Tls", default: true },
        imap_user: { type: "string", title: "Imap User", default: "" },
        imap_folder: { type: "string", title: "Imap Folder", default: "INBOX" },
        imap_poll_interval_sec: {
          type: "integer",
          title: "Imap Poll Interval Sec",
          default: 300,
        },
        has_imap_credential: {
          type: "boolean",
          title: "Has Imap Credential",
          default: false,
        },
      },
      type: "object",
      required: [
        "id",
        "tg_key",
        "name",
        "type",
        "target_type",
        "target_key",
        "min_interval_sec",
        "adapter",
        "enabled",
        "has_secret",
      ],
      title: "TriggerOut",
      description: "触发器列表中的条目。",
    },
    TriggerRunOut: {
      properties: {
        id: { type: "string", title: "Id" },
        status: { type: "string", title: "Status" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
        duration_ms: { type: "integer", title: "Duration Ms" },
        target_run_ref: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Target Run Ref",
        },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
        attempt: { type: "integer", title: "Attempt", default: 1 },
        retry_of_run_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Retry Of Run Id",
        },
        next_retry_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Next Retry At",
        },
        summary: { type: "string", title: "Summary", default: "" },
      },
      type: "object",
      required: ["id", "status", "duration_ms"],
      title: "TriggerRunOut",
    },
    TriggerRunsPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/TriggerRunOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "TriggerRunsPageOut",
    },
    TriggerUpdateIn: {
      properties: {
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        target_type: {
          type: "string",
          pattern: "^(workflow|bot|task)$",
          title: "Target Type",
        },
        target_key: {
          type: "string",
          maxLength: 64,
          title: "Target Key",
          default: "",
        },
        cron_expr: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Cron Expr",
        },
        min_interval_sec: {
          type: "integer",
          maximum: 86400,
          minimum: 0,
          title: "Min Interval Sec",
          default: 60,
        },
        adapter: {
          type: "string",
          pattern: "^(custom|github)$",
          title: "Adapter",
          default: "custom",
        },
        notify_url: {
          type: "string",
          maxLength: 512,
          title: "Notify Url",
          default: "",
        },
        secret: {
          type: "string",
          maxLength: 256,
          title: "Secret",
          default: "",
        },
        max_retries: {
          type: "integer",
          maximum: 5,
          minimum: 0,
          title: "Max Retries",
          default: 0,
        },
        task_template: {
          anyOf: [
            { additionalProperties: true, type: "object" },
            { type: "null" },
          ],
          title: "Task Template",
        },
        imap_host: {
          type: "string",
          maxLength: 255,
          title: "Imap Host",
          default: "",
        },
        imap_port: {
          type: "integer",
          maximum: 65535,
          minimum: 1,
          title: "Imap Port",
          default: 993,
        },
        imap_tls: { type: "boolean", title: "Imap Tls", default: true },
        imap_user: {
          type: "string",
          maxLength: 255,
          title: "Imap User",
          default: "",
        },
        imap_password: {
          type: "string",
          maxLength: 256,
          title: "Imap Password",
          default: "",
        },
        imap_folder: {
          type: "string",
          maxLength: 255,
          title: "Imap Folder",
          default: "INBOX",
        },
        imap_poll_interval_sec: {
          type: "integer",
          maximum: 86400,
          minimum: 1,
          title: "Imap Poll Interval Sec",
          default: 300,
        },
      },
      type: "object",
      required: ["name", "target_type"],
      title: "TriggerUpdateIn",
    },
    TurnCancelIn: {
      properties: {
        trace_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "Trace Id",
        },
        user_id: {
          type: "string",
          maxLength: 64,
          minLength: 1,
          title: "User Id",
        },
      },
      type: "object",
      required: ["trace_id", "user_id"],
      title: "TurnCancelIn",
      description:
        "跨实例取消转发入参:轮次标识 + 归属用户(转发方从已鉴权请求带入)。",
    },
    TurnCancelOut: {
      properties: {
        outcome: {
          type: "string",
          enum: ["CANCELLED", "COMPLETED"],
          title: "Outcome",
        },
        message_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Message Id",
        },
      },
      type: "object",
      required: ["outcome"],
      title: "TurnCancelOut",
      description:
        "取消端点响应:outcome=CANCELLED(本轮已取消)/ COMPLETED(取消到达前本轮已正常结束);\nmessage_id=该轮落库的 assistant 消息 id(persist=false 或落库失败时为空)。",
    },
    UploadOut: {
      properties: {
        tables: { items: { type: "string" }, type: "array", title: "Tables" },
      },
      type: "object",
      required: ["tables"],
      title: "UploadOut",
    },
    UsagePageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        rows: {
          items: { $ref: "#/$defs/UsageRow" },
          type: "array",
          title: "Rows",
        },
      },
      type: "object",
      required: ["total", "rows"],
      title: "UsagePageOut",
    },
    UsageRecordIn: {
      properties: {
        trace_id: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Trace Id",
        },
        tenant_key: {
          anyOf: [{ type: "string", maxLength: 64 }, { type: "null" }],
          title: "Tenant Key",
        },
        model: { type: "string", maxLength: 64, minLength: 1, title: "Model" },
        task: { type: "string", maxLength: 24, minLength: 1, title: "Task" },
        prompt_tokens: {
          type: "integer",
          minimum: 0,
          title: "Prompt Tokens",
          default: 0,
        },
        completion_tokens: {
          type: "integer",
          minimum: 0,
          title: "Completion Tokens",
          default: 0,
        },
        total_tokens: {
          type: "integer",
          minimum: 0,
          title: "Total Tokens",
          default: 0,
        },
        latency_ms: {
          anyOf: [{ type: "integer", minimum: 0 }, { type: "null" }],
          title: "Latency Ms",
        },
        status: {
          type: "string",
          enum: ["SUCCESS", "ERROR"],
          title: "Status",
          default: "SUCCESS",
        },
        protocol: {
          anyOf: [{ type: "string", maxLength: 16 }, { type: "null" }],
          title: "Protocol",
        },
      },
      type: "object",
      required: ["model", "task"],
      title: "UsageRecordIn",
    },
    UsageReportIn: {
      properties: {
        records: {
          items: { $ref: "#/$defs/UsageRecordIn" },
          type: "array",
          maxItems: 64,
          minItems: 1,
          title: "Records",
        },
      },
      type: "object",
      required: ["records"],
      title: "UsageReportIn",
    },
    UsageReportOut: {
      properties: { accepted: { type: "integer", title: "Accepted" } },
      type: "object",
      required: ["accepted"],
      title: "UsageReportOut",
    },
    UsageRow: {
      properties: {
        id: { type: "string", title: "Id" },
        trace_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Trace Id",
        },
        tenant_key: { type: "string", title: "Tenant Key" },
        model: { type: "string", title: "Model" },
        task: { type: "string", title: "Task" },
        status: { type: "string", title: "Status" },
        bot_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Bot Id",
        },
        protocol: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Protocol",
        },
        prompt_tokens: { type: "integer", title: "Prompt Tokens" },
        completion_tokens: { type: "integer", title: "Completion Tokens" },
        total_tokens: { type: "integer", title: "Total Tokens" },
        latency_ms: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Latency Ms",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "tenant_key",
        "model",
        "task",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "created_at",
      ],
      title: "UsageRow",
    },
    UsageSummaryItem: {
      properties: {
        tenant_key: { type: "string", title: "Tenant Key" },
        model: { type: "string", title: "Model" },
        task: { type: "string", title: "Task" },
        calls: { type: "integer", title: "Calls" },
        calls_error: { type: "integer", title: "Calls Error" },
        prompt_tokens: { type: "integer", title: "Prompt Tokens" },
        completion_tokens: { type: "integer", title: "Completion Tokens" },
        total_tokens: { type: "integer", title: "Total Tokens" },
        latency_ms_avg: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Latency Ms Avg",
        },
        latency_ms_max: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Latency Ms Max",
        },
      },
      type: "object",
      required: [
        "tenant_key",
        "model",
        "task",
        "calls",
        "calls_error",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
      ],
      title: "UsageSummaryItem",
    },
    UsageSummaryOut: {
      properties: {
        items: {
          items: { $ref: "#/$defs/UsageSummaryItem" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["items"],
      title: "UsageSummaryOut",
    },
    UsageTimelineOut: {
      properties: {
        trace_id: { type: "string", title: "Trace Id" },
        events: {
          items: { $ref: "#/$defs/TimelineEvent" },
          type: "array",
          title: "Events",
        },
      },
      type: "object",
      required: ["trace_id", "events"],
      title: "UsageTimelineOut",
    },
    UsageTrendOut: {
      properties: {
        points: {
          items: { $ref: "#/$defs/UsageTrendPoint" },
          type: "array",
          title: "Points",
        },
      },
      type: "object",
      required: ["points"],
      title: "UsageTrendOut",
    },
    UsageTrendPoint: {
      properties: {
        date: { type: "string", title: "Date" },
        calls: { type: "integer", title: "Calls" },
        calls_error: { type: "integer", title: "Calls Error" },
        total_tokens: { type: "integer", title: "Total Tokens" },
      },
      type: "object",
      required: ["date", "calls", "calls_error", "total_tokens"],
      title: "UsageTrendPoint",
    },
    UserItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        username: { type: "string", title: "Username" },
        nickname: { type: "string", title: "Nickname" },
        email: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Email",
        },
        status: { type: "string", title: "Status" },
        dept_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Dept Id",
        },
        dept_name: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Dept Name",
        },
        tenant_id: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Tenant Id",
        },
        role_keys: {
          items: { type: "string" },
          type: "array",
          title: "Role Keys",
        },
        created_at: {
          type: "string",
          format: "date-time",
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "username",
        "nickname",
        "email",
        "status",
        "dept_id",
        "dept_name",
        "tenant_id",
        "role_keys",
        "created_at",
      ],
      title: "UserItemOut",
    },
    UserPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/UserItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "UserPageOut",
    },
    ValidationError: {
      properties: {
        loc: {
          items: { anyOf: [{ type: "string" }, { type: "integer" }] },
          type: "array",
          title: "Location",
        },
        msg: { type: "string", title: "Message" },
        type: { type: "string", title: "Error Type" },
        input: { title: "Input" },
        ctx: { type: "object", title: "Context" },
      },
      type: "object",
      required: ["loc", "msg", "type"],
      title: "ValidationError",
    },
    VersionMetaOut: {
      properties: {
        id: { type: "string", title: "Id" },
        version: { type: "integer", title: "Version" },
        meta: { additionalProperties: true, type: "object", title: "Meta" },
        data_time_point: { type: "string", title: "Data Time Point" },
        created_at: { type: "string", title: "Created At" },
      },
      type: "object",
      required: ["id", "version", "meta", "data_time_point", "created_at"],
      title: "VersionMetaOut",
    },
    VoiceCapabilityOut: {
      properties: { enabled: { type: "boolean", title: "Enabled" } },
      type: "object",
      required: ["enabled"],
      title: "VoiceCapabilityOut",
      description: "通话能力探测响应。",
    },
    VoiceEstablishOut: {
      properties: {
        answer: { type: "string", title: "Answer" },
        ticket: { type: "string", title: "Ticket" },
        call_id: { type: "string", title: "Call Id" },
      },
      type: "object",
      required: ["answer", "ticket", "call_id"],
      title: "VoiceEstablishOut",
      description: "建交通话响应:连接应答、一次性通道票据与通话标识。",
    },
    VoiceHangUpOut: {
      properties: { ok: { type: "boolean", title: "Ok" } },
      type: "object",
      required: ["ok"],
      title: "VoiceHangUpOut",
      description: "挂断响应。",
    },
    WorkflowDefOut: {
      properties: {
        id: { type: "string", title: "Id" },
        wf_key: { type: "string", title: "Wf Key" },
        name: { type: "string", title: "Name" },
        version: { type: "integer", title: "Version" },
        enabled: { type: "boolean", title: "Enabled" },
        definition: {
          additionalProperties: true,
          type: "object",
          title: "Definition",
        },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
      },
      type: "object",
      required: ["id", "wf_key", "name", "version", "enabled", "definition"],
      title: "WorkflowDefOut",
      description: "工作流定义条目;definition 为自由 JSON。",
    },
    WorkflowDetailOut: {
      properties: {
        latest: { $ref: "#/$defs/WorkflowDefOut" },
        versions: {
          items: { $ref: "#/$defs/WorkflowVersionBriefOut" },
          type: "array",
          title: "Versions",
        },
      },
      type: "object",
      required: ["latest", "versions"],
      title: "WorkflowDetailOut",
    },
    WorkflowRunIn: {
      properties: {
        inputs: {
          additionalProperties: true,
          type: "object",
          title: "Inputs",
          default: {},
        },
        version: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          title: "Version",
        },
      },
      type: "object",
      title: "WorkflowRunIn",
    },
    WorkflowRunItemOut: {
      properties: {
        id: { type: "string", title: "Id" },
        version: { type: "integer", title: "Version" },
        trigger_source: { type: "string", title: "Trigger Source" },
        status: { type: "string", title: "Status" },
        error: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Error",
        },
        duration_ms: { type: "integer", title: "Duration Ms" },
        node_runs: {
          items: { additionalProperties: true, type: "object" },
          type: "array",
          title: "Node Runs",
        },
        inputs: { additionalProperties: true, type: "object", title: "Inputs" },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
      },
      type: "object",
      required: [
        "id",
        "version",
        "trigger_source",
        "status",
        "duration_ms",
        "node_runs",
        "inputs",
      ],
      title: "WorkflowRunItemOut",
    },
    WorkflowRunsPageOut: {
      properties: {
        total: { type: "integer", title: "Total" },
        items: {
          items: { $ref: "#/$defs/WorkflowRunItemOut" },
          type: "array",
          title: "Items",
        },
      },
      type: "object",
      required: ["total", "items"],
      title: "WorkflowRunsPageOut",
    },
    WorkflowSaveIn: {
      properties: {
        wf_key: {
          type: "string",
          maxLength: 64,
          minLength: 2,
          title: "Wf Key",
        },
        name: { type: "string", maxLength: 128, minLength: 1, title: "Name" },
        definition: {
          additionalProperties: true,
          type: "object",
          title: "Definition",
        },
      },
      type: "object",
      required: ["wf_key", "name", "definition"],
      title: "WorkflowSaveIn",
    },
    WorkflowVersionBriefOut: {
      properties: {
        version: { type: "integer", title: "Version" },
        enabled: { type: "boolean", title: "Enabled" },
        created_at: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Created At",
        },
      },
      type: "object",
      required: ["version", "enabled"],
      title: "WorkflowVersionBriefOut",
    },
  },
};
const schema32 = {
  properties: {
    type: { type: "string", const: "stage", title: "Type" },
    data: { $ref: "#/$defs/StreamStageOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamStageEvent",
  description: "type=stage 信封。",
};
const schema33 = {
  properties: {
    node: { type: "string", title: "Node" },
    status: { type: "string", enum: ["running", "done"], title: "Status" },
    retry_count: { type: "integer", title: "Retry Count", default: 0 },
  },
  additionalProperties: false,
  type: "object",
  required: ["node", "status"],
  title: "StreamStageOut",
  description: "stage 事件 data:节点粒度进度(status ∈ running/done)。",
};
function validate21(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate21.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate21.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate21.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate21.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("stage" !== data0) {
              validate21.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "stage" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              let data1 = data.data;
              const _errs4 = errors;
              const _errs5 = errors;
              if (errors === _errs5) {
                if (
                  data1 &&
                  typeof data1 == "object" &&
                  !Array.isArray(data1)
                ) {
                  let missing1;
                  if (
                    (data1.node === undefined && (missing1 = "node")) ||
                    (data1.status === undefined && (missing1 = "status"))
                  ) {
                    validate21.errors = [
                      {
                        instancePath: instancePath + "/data",
                        schemaPath: "#/$defs/StreamStageOut/required",
                        keyword: "required",
                        params: { missingProperty: missing1 },
                        message:
                          "must have required property '" + missing1 + "'",
                      },
                    ];
                    return false;
                  } else {
                    const _errs7 = errors;
                    for (const key1 in data1) {
                      if (!(
                        key1 === "node" ||
                        key1 === "status" ||
                        key1 === "retry_count"
                      )) {
                        validate21.errors = [
                          {
                            instancePath: instancePath + "/data",
                            schemaPath:
                              "#/$defs/StreamStageOut/additionalProperties",
                            keyword: "additionalProperties",
                            params: { additionalProperty: key1 },
                            message: "must NOT have additional properties",
                          },
                        ];
                        return false;
                        break;
                      }
                    }
                    if (_errs7 === errors) {
                      if (data1.node !== undefined) {
                        const _errs8 = errors;
                        if (typeof data1.node !== "string") {
                          validate21.errors = [
                            {
                              instancePath: instancePath + "/data/node",
                              schemaPath:
                                "#/$defs/StreamStageOut/properties/node/type",
                              keyword: "type",
                              params: { type: "string" },
                              message: "must be string",
                            },
                          ];
                          return false;
                        }
                        var valid2 = _errs8 === errors;
                      } else {
                        var valid2 = true;
                      }
                      if (valid2) {
                        if (data1.status !== undefined) {
                          let data3 = data1.status;
                          const _errs10 = errors;
                          if (typeof data3 !== "string") {
                            validate21.errors = [
                              {
                                instancePath: instancePath + "/data/status",
                                schemaPath:
                                  "#/$defs/StreamStageOut/properties/status/type",
                                keyword: "type",
                                params: { type: "string" },
                                message: "must be string",
                              },
                            ];
                            return false;
                          }
                          if (!(data3 === "running" || data3 === "done")) {
                            validate21.errors = [
                              {
                                instancePath: instancePath + "/data/status",
                                schemaPath:
                                  "#/$defs/StreamStageOut/properties/status/enum",
                                keyword: "enum",
                                params: {
                                  allowedValues:
                                    schema33.properties.status.enum,
                                },
                                message:
                                  "must be equal to one of the allowed values",
                              },
                            ];
                            return false;
                          }
                          var valid2 = _errs10 === errors;
                        } else {
                          var valid2 = true;
                        }
                        if (valid2) {
                          if (data1.retry_count !== undefined) {
                            let data4 = data1.retry_count;
                            const _errs12 = errors;
                            if (!(
                              typeof data4 == "number" &&
                              !(data4 % 1) &&
                              !isNaN(data4) &&
                              isFinite(data4)
                            )) {
                              validate21.errors = [
                                {
                                  instancePath:
                                    instancePath + "/data/retry_count",
                                  schemaPath:
                                    "#/$defs/StreamStageOut/properties/retry_count/type",
                                  keyword: "type",
                                  params: { type: "integer" },
                                  message: "must be integer",
                                },
                              ];
                              return false;
                            }
                            var valid2 = _errs12 === errors;
                          } else {
                            var valid2 = true;
                          }
                        }
                      }
                    }
                  }
                } else {
                  validate21.errors = [
                    {
                      instancePath: instancePath + "/data",
                      schemaPath: "#/$defs/StreamStageOut/type",
                      keyword: "type",
                      params: { type: "object" },
                      message: "must be object",
                    },
                  ];
                  return false;
                }
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate21.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate21.errors = vErrors;
  return errors === 0;
}
validate21.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
const schema34 = {
  properties: {
    type: { type: "string", const: "sql", title: "Type" },
    data: { $ref: "#/$defs/StreamSqlOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamSqlEvent",
  description: "type=sql 信封。",
};
const schema35 = {
  properties: { sql: { type: "string", title: "Sql" } },
  additionalProperties: false,
  type: "object",
  required: ["sql"],
  title: "StreamSqlOut",
  description:
    "sql 事件 data:validate 通过后提前下发,内容与终态落库 SQL 一致。",
};
function validate23(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate23.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate23.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate23.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate23.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("sql" !== data0) {
              validate23.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "sql" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              let data1 = data.data;
              const _errs4 = errors;
              const _errs5 = errors;
              if (errors === _errs5) {
                if (
                  data1 &&
                  typeof data1 == "object" &&
                  !Array.isArray(data1)
                ) {
                  let missing1;
                  if (data1.sql === undefined && (missing1 = "sql")) {
                    validate23.errors = [
                      {
                        instancePath: instancePath + "/data",
                        schemaPath: "#/$defs/StreamSqlOut/required",
                        keyword: "required",
                        params: { missingProperty: missing1 },
                        message:
                          "must have required property '" + missing1 + "'",
                      },
                    ];
                    return false;
                  } else {
                    const _errs7 = errors;
                    for (const key1 in data1) {
                      if (!(key1 === "sql")) {
                        validate23.errors = [
                          {
                            instancePath: instancePath + "/data",
                            schemaPath:
                              "#/$defs/StreamSqlOut/additionalProperties",
                            keyword: "additionalProperties",
                            params: { additionalProperty: key1 },
                            message: "must NOT have additional properties",
                          },
                        ];
                        return false;
                        break;
                      }
                    }
                    if (_errs7 === errors) {
                      if (data1.sql !== undefined) {
                        if (typeof data1.sql !== "string") {
                          validate23.errors = [
                            {
                              instancePath: instancePath + "/data/sql",
                              schemaPath:
                                "#/$defs/StreamSqlOut/properties/sql/type",
                              keyword: "type",
                              params: { type: "string" },
                              message: "must be string",
                            },
                          ];
                          return false;
                        }
                      }
                    }
                  }
                } else {
                  validate23.errors = [
                    {
                      instancePath: instancePath + "/data",
                      schemaPath: "#/$defs/StreamSqlOut/type",
                      keyword: "type",
                      params: { type: "object" },
                      message: "must be object",
                    },
                  ];
                  return false;
                }
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate23.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate23.errors = vErrors;
  return errors === 0;
}
validate23.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
const schema36 = {
  properties: {
    type: { type: "string", const: "summary", title: "Type" },
    data: { $ref: "#/$defs/StreamSummaryOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamSummaryEvent",
  description: "type=summary 信封。",
};
const schema37 = {
  properties: { delta: { type: "string", title: "Delta" } },
  additionalProperties: false,
  type: "object",
  required: ["delta"],
  title: "StreamSummaryOut",
  description:
    "summary 事件 data:总结增量(当前为单条完整文案,逐事件追加=打字机语义)。",
};
function validate25(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate25.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate25.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate25.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate25.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("summary" !== data0) {
              validate25.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "summary" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              let data1 = data.data;
              const _errs4 = errors;
              const _errs5 = errors;
              if (errors === _errs5) {
                if (
                  data1 &&
                  typeof data1 == "object" &&
                  !Array.isArray(data1)
                ) {
                  let missing1;
                  if (data1.delta === undefined && (missing1 = "delta")) {
                    validate25.errors = [
                      {
                        instancePath: instancePath + "/data",
                        schemaPath: "#/$defs/StreamSummaryOut/required",
                        keyword: "required",
                        params: { missingProperty: missing1 },
                        message:
                          "must have required property '" + missing1 + "'",
                      },
                    ];
                    return false;
                  } else {
                    const _errs7 = errors;
                    for (const key1 in data1) {
                      if (!(key1 === "delta")) {
                        validate25.errors = [
                          {
                            instancePath: instancePath + "/data",
                            schemaPath:
                              "#/$defs/StreamSummaryOut/additionalProperties",
                            keyword: "additionalProperties",
                            params: { additionalProperty: key1 },
                            message: "must NOT have additional properties",
                          },
                        ];
                        return false;
                        break;
                      }
                    }
                    if (_errs7 === errors) {
                      if (data1.delta !== undefined) {
                        if (typeof data1.delta !== "string") {
                          validate25.errors = [
                            {
                              instancePath: instancePath + "/data/delta",
                              schemaPath:
                                "#/$defs/StreamSummaryOut/properties/delta/type",
                              keyword: "type",
                              params: { type: "string" },
                              message: "must be string",
                            },
                          ];
                          return false;
                        }
                      }
                    }
                  }
                } else {
                  validate25.errors = [
                    {
                      instancePath: instancePath + "/data",
                      schemaPath: "#/$defs/StreamSummaryOut/type",
                      keyword: "type",
                      params: { type: "object" },
                      message: "must be object",
                    },
                  ];
                  return false;
                }
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate25.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate25.errors = vErrors;
  return errors === 0;
}
validate25.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
const schema38 = {
  properties: {
    type: { type: "string", const: "tool_call", title: "Type" },
    data: { $ref: "#/$defs/StreamToolCallOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamToolCallEvent",
  description: "type=tool_call 信封。",
};
const schema39 = {
  properties: {
    tool_name: { type: "string", title: "Tool Name" },
    status: {
      type: "string",
      enum: ["running", "done", "rejected"],
      title: "Status",
    },
    args_digest: { type: "string", title: "Args Digest", default: "" },
    result_digest: { type: "string", title: "Result Digest", default: "" },
  },
  additionalProperties: false,
  type: "object",
  required: ["tool_name", "status"],
  title: "StreamToolCallOut",
  description:
    "tool_call 事件 data(tool-calling):工具调用进度。\n\nstatus ∈ running/done/rejected(confirm_required 以 done 下发,result_digest\n含「待确认」);args_digest/result_digest 为截断摘要(审计同源口径)。",
};
function validate27(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate27.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate27.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate27.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate27.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("tool_call" !== data0) {
              validate27.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "tool_call" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              let data1 = data.data;
              const _errs4 = errors;
              const _errs5 = errors;
              if (errors === _errs5) {
                if (
                  data1 &&
                  typeof data1 == "object" &&
                  !Array.isArray(data1)
                ) {
                  let missing1;
                  if (
                    (data1.tool_name === undefined &&
                      (missing1 = "tool_name")) ||
                    (data1.status === undefined && (missing1 = "status"))
                  ) {
                    validate27.errors = [
                      {
                        instancePath: instancePath + "/data",
                        schemaPath: "#/$defs/StreamToolCallOut/required",
                        keyword: "required",
                        params: { missingProperty: missing1 },
                        message:
                          "must have required property '" + missing1 + "'",
                      },
                    ];
                    return false;
                  } else {
                    const _errs7 = errors;
                    for (const key1 in data1) {
                      if (!(
                        key1 === "tool_name" ||
                        key1 === "status" ||
                        key1 === "args_digest" ||
                        key1 === "result_digest"
                      )) {
                        validate27.errors = [
                          {
                            instancePath: instancePath + "/data",
                            schemaPath:
                              "#/$defs/StreamToolCallOut/additionalProperties",
                            keyword: "additionalProperties",
                            params: { additionalProperty: key1 },
                            message: "must NOT have additional properties",
                          },
                        ];
                        return false;
                        break;
                      }
                    }
                    if (_errs7 === errors) {
                      if (data1.tool_name !== undefined) {
                        const _errs8 = errors;
                        if (typeof data1.tool_name !== "string") {
                          validate27.errors = [
                            {
                              instancePath: instancePath + "/data/tool_name",
                              schemaPath:
                                "#/$defs/StreamToolCallOut/properties/tool_name/type",
                              keyword: "type",
                              params: { type: "string" },
                              message: "must be string",
                            },
                          ];
                          return false;
                        }
                        var valid2 = _errs8 === errors;
                      } else {
                        var valid2 = true;
                      }
                      if (valid2) {
                        if (data1.status !== undefined) {
                          let data3 = data1.status;
                          const _errs10 = errors;
                          if (typeof data3 !== "string") {
                            validate27.errors = [
                              {
                                instancePath: instancePath + "/data/status",
                                schemaPath:
                                  "#/$defs/StreamToolCallOut/properties/status/type",
                                keyword: "type",
                                params: { type: "string" },
                                message: "must be string",
                              },
                            ];
                            return false;
                          }
                          if (!(
                            data3 === "running" ||
                            data3 === "done" ||
                            data3 === "rejected"
                          )) {
                            validate27.errors = [
                              {
                                instancePath: instancePath + "/data/status",
                                schemaPath:
                                  "#/$defs/StreamToolCallOut/properties/status/enum",
                                keyword: "enum",
                                params: {
                                  allowedValues:
                                    schema39.properties.status.enum,
                                },
                                message:
                                  "must be equal to one of the allowed values",
                              },
                            ];
                            return false;
                          }
                          var valid2 = _errs10 === errors;
                        } else {
                          var valid2 = true;
                        }
                        if (valid2) {
                          if (data1.args_digest !== undefined) {
                            const _errs12 = errors;
                            if (typeof data1.args_digest !== "string") {
                              validate27.errors = [
                                {
                                  instancePath:
                                    instancePath + "/data/args_digest",
                                  schemaPath:
                                    "#/$defs/StreamToolCallOut/properties/args_digest/type",
                                  keyword: "type",
                                  params: { type: "string" },
                                  message: "must be string",
                                },
                              ];
                              return false;
                            }
                            var valid2 = _errs12 === errors;
                          } else {
                            var valid2 = true;
                          }
                          if (valid2) {
                            if (data1.result_digest !== undefined) {
                              const _errs14 = errors;
                              if (typeof data1.result_digest !== "string") {
                                validate27.errors = [
                                  {
                                    instancePath:
                                      instancePath + "/data/result_digest",
                                    schemaPath:
                                      "#/$defs/StreamToolCallOut/properties/result_digest/type",
                                    keyword: "type",
                                    params: { type: "string" },
                                    message: "must be string",
                                  },
                                ];
                                return false;
                              }
                              var valid2 = _errs14 === errors;
                            } else {
                              var valid2 = true;
                            }
                          }
                        }
                      }
                    }
                  }
                } else {
                  validate27.errors = [
                    {
                      instancePath: instancePath + "/data",
                      schemaPath: "#/$defs/StreamToolCallOut/type",
                      keyword: "type",
                      params: { type: "object" },
                      message: "must be object",
                    },
                  ];
                  return false;
                }
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate27.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate27.errors = vErrors;
  return errors === 0;
}
validate27.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
const schema40 = {
  properties: {
    type: { type: "string", const: "result", title: "Type" },
    data: { $ref: "#/$defs/ChatQueryOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamResultEvent",
  description: "type=result 信封:data 与同步问答响应逐字段一致。",
};
const schema41 = {
  properties: {
    session_id: {
      anyOf: [{ type: "string" }, { type: "null" }],
      title: "Session Id",
    },
    success: { type: "boolean", title: "Success" },
    verdict: {
      anyOf: [{ type: "string" }, { type: "null" }],
      title: "Verdict",
    },
    summary: { type: "string", title: "Summary" },
    insights: {
      items: { additionalProperties: { type: "string" }, type: "object" },
      type: "array",
      title: "Insights",
    },
    stats: { additionalProperties: true, type: "object", title: "Stats" },
    columns: { items: { type: "string" }, type: "array", title: "Columns" },
    rows: { items: { items: {}, type: "array" }, type: "array", title: "Rows" },
    sql: { type: "string", title: "Sql" },
    chart: { $ref: "#/$defs/ChartOut" },
    chart_candidates: {
      items: { $ref: "#/$defs/ChartCandidateOut" },
      type: "array",
      title: "Chart Candidates",
      default: [],
    },
    retry_count: { type: "integer", title: "Retry Count" },
    error: { anyOf: [{ type: "string" }, { type: "null" }], title: "Error" },
    error_detail: {
      anyOf: [{ type: "string" }, { type: "null" }],
      title: "Error Detail",
    },
    message_id: {
      anyOf: [{ type: "string" }, { type: "null" }],
      title: "Message Id",
    },
    datasource_key: {
      type: "string",
      title: "Datasource Key",
      default: "default",
    },
    pending_tool_calls: {
      items: { $ref: "#/$defs/PendingToolCallOut" },
      type: "array",
      title: "Pending Tool Calls",
      default: [],
    },
    artifacts: {
      items: { $ref: "#/$defs/ArtifactOut" },
      type: "array",
      title: "Artifacts",
      default: [],
    },
    attachments: {
      items: { additionalProperties: true, type: "object" },
      type: "array",
      title: "Attachments",
      default: [],
    },
    column_labels: {
      items: { type: "string" },
      type: "array",
      title: "Column Labels",
      default: [],
    },
    metric_hits: {
      items: { $ref: "#/$defs/MetricHitOut" },
      type: "array",
      title: "Metric Hits",
      default: [],
    },
    sources: {
      items: { $ref: "#/$defs/SourceOut" },
      type: "array",
      title: "Sources",
      description:
        "纯数据问答不返回来源;知识问答和融合问答会从净化后的回答正文提取来源。",
    },
  },
  type: "object",
  required: [
    "session_id",
    "success",
    "verdict",
    "summary",
    "insights",
    "stats",
    "columns",
    "rows",
    "sql",
    "chart",
    "retry_count",
    "error",
  ],
  title: "ChatQueryOut",
};
const schema42 = {
  properties: {
    type: { type: "string", title: "Type" },
    x: { anyOf: [{ type: "string" }, { type: "null" }], title: "X" },
    y: { anyOf: [{ type: "string" }, { type: "null" }], title: "Y" },
  },
  type: "object",
  required: ["type"],
  title: "ChartOut",
};
const schema43 = {
  properties: {
    type: { type: "string", title: "Type" },
    x: { anyOf: [{ type: "string" }, { type: "null" }], title: "X" },
    y: { items: { type: "string" }, type: "array", title: "Y", default: [] },
    series: { anyOf: [{ type: "string" }, { type: "null" }], title: "Series" },
  },
  type: "object",
  required: ["type"],
  title: "ChartCandidateOut",
  description:
    "图表候选:候选自带完整绑定,y 为值列(多度量多系列),\nseries 为系列维(分组柱/时序多系列)。既有 chart 字段保留=首候选展平。",
};
const schema44 = {
  properties: {
    confirm_id: { type: "string", title: "Confirm Id" },
    tool_name: { type: "string", title: "Tool Name" },
    args_digest: { type: "string", title: "Args Digest", default: "" },
  },
  type: "object",
  required: ["confirm_id", "tool_name"],
  title: "PendingToolCallOut",
  description: "待确认写工具调用:确认卡片渲染要素。",
};
const schema45 = {
  properties: {
    type: { type: "string", title: "Type" },
    content: { type: "string", title: "Content" },
    metadata: {
      additionalProperties: true,
      type: "object",
      title: "Metadata",
      default: {},
    },
  },
  type: "object",
  required: ["type", "content"],
  title: "ArtifactOut",
  description: "类型化产物:type ∈ sql/chart/document。",
};
const schema46 = {
  properties: {
    metric_id: { type: "string", title: "Metric Id" },
    metric_name: { type: "string", title: "Metric Name" },
    sql_template: { type: "string", title: "Sql Template" },
    updated_at: {
      anyOf: [{ type: "string" }, { type: "null" }],
      title: "Updated At",
    },
  },
  type: "object",
  required: ["metric_id", "metric_name", "sql_template"],
  title: "MetricHitOut",
  description: "语义层口径命中(标识/名称/口径表达式/修订时间)。",
};
const schema47 = {
  properties: {
    citation_key: { type: "string", title: "Citation Key" },
    source_ref: { type: "string", title: "Source Ref", default: "" },
    doc_name: { type: "string", title: "Doc Name", default: "" },
  },
  type: "object",
  required: ["citation_key"],
  title: "SourceOut",
  description:
    "结构化来源条目(引用键/来源标识/文档名快照)。\n语义:纯数据道恒为空;知识道与融合道由净化后正文单向投影,可非空。",
};
function validate30(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate30.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.session_id === undefined && (missing0 = "session_id")) ||
        (data.success === undefined && (missing0 = "success")) ||
        (data.verdict === undefined && (missing0 = "verdict")) ||
        (data.summary === undefined && (missing0 = "summary")) ||
        (data.insights === undefined && (missing0 = "insights")) ||
        (data.stats === undefined && (missing0 = "stats")) ||
        (data.columns === undefined && (missing0 = "columns")) ||
        (data.rows === undefined && (missing0 = "rows")) ||
        (data.sql === undefined && (missing0 = "sql")) ||
        (data.chart === undefined && (missing0 = "chart")) ||
        (data.retry_count === undefined && (missing0 = "retry_count")) ||
        (data.error === undefined && (missing0 = "error"))
      ) {
        validate30.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        if (data.session_id !== undefined) {
          let data0 = data.session_id;
          const _errs1 = errors;
          const _errs2 = errors;
          let valid1 = false;
          const _errs3 = errors;
          if (typeof data0 !== "string") {
            const err0 = {
              instancePath: instancePath + "/session_id",
              schemaPath: "#/properties/session_id/anyOf/0/type",
              keyword: "type",
              params: { type: "string" },
              message: "must be string",
            };
            if (vErrors === null) {
              vErrors = [err0];
            } else {
              vErrors.push(err0);
            }
            errors++;
          }
          var _valid0 = _errs3 === errors;
          valid1 = valid1 || _valid0;
          const _errs5 = errors;
          if (data0 !== null) {
            const err1 = {
              instancePath: instancePath + "/session_id",
              schemaPath: "#/properties/session_id/anyOf/1/type",
              keyword: "type",
              params: { type: "null" },
              message: "must be null",
            };
            if (vErrors === null) {
              vErrors = [err1];
            } else {
              vErrors.push(err1);
            }
            errors++;
          }
          var _valid0 = _errs5 === errors;
          valid1 = valid1 || _valid0;
          if (!valid1) {
            const err2 = {
              instancePath: instancePath + "/session_id",
              schemaPath: "#/properties/session_id/anyOf",
              keyword: "anyOf",
              params: {},
              message: "must match a schema in anyOf",
            };
            if (vErrors === null) {
              vErrors = [err2];
            } else {
              vErrors.push(err2);
            }
            errors++;
            validate30.errors = vErrors;
            return false;
          } else {
            errors = _errs2;
            if (vErrors !== null) {
              if (_errs2) {
                vErrors.length = _errs2;
              } else {
                vErrors = null;
              }
            }
          }
          var valid0 = _errs1 === errors;
        } else {
          var valid0 = true;
        }
        if (valid0) {
          if (data.success !== undefined) {
            const _errs7 = errors;
            if (typeof data.success !== "boolean") {
              validate30.errors = [
                {
                  instancePath: instancePath + "/success",
                  schemaPath: "#/properties/success/type",
                  keyword: "type",
                  params: { type: "boolean" },
                  message: "must be boolean",
                },
              ];
              return false;
            }
            var valid0 = _errs7 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.verdict !== undefined) {
              let data2 = data.verdict;
              const _errs9 = errors;
              const _errs10 = errors;
              let valid2 = false;
              const _errs11 = errors;
              if (typeof data2 !== "string") {
                const err3 = {
                  instancePath: instancePath + "/verdict",
                  schemaPath: "#/properties/verdict/anyOf/0/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                };
                if (vErrors === null) {
                  vErrors = [err3];
                } else {
                  vErrors.push(err3);
                }
                errors++;
              }
              var _valid1 = _errs11 === errors;
              valid2 = valid2 || _valid1;
              const _errs13 = errors;
              if (data2 !== null) {
                const err4 = {
                  instancePath: instancePath + "/verdict",
                  schemaPath: "#/properties/verdict/anyOf/1/type",
                  keyword: "type",
                  params: { type: "null" },
                  message: "must be null",
                };
                if (vErrors === null) {
                  vErrors = [err4];
                } else {
                  vErrors.push(err4);
                }
                errors++;
              }
              var _valid1 = _errs13 === errors;
              valid2 = valid2 || _valid1;
              if (!valid2) {
                const err5 = {
                  instancePath: instancePath + "/verdict",
                  schemaPath: "#/properties/verdict/anyOf",
                  keyword: "anyOf",
                  params: {},
                  message: "must match a schema in anyOf",
                };
                if (vErrors === null) {
                  vErrors = [err5];
                } else {
                  vErrors.push(err5);
                }
                errors++;
                validate30.errors = vErrors;
                return false;
              } else {
                errors = _errs10;
                if (vErrors !== null) {
                  if (_errs10) {
                    vErrors.length = _errs10;
                  } else {
                    vErrors = null;
                  }
                }
              }
              var valid0 = _errs9 === errors;
            } else {
              var valid0 = true;
            }
            if (valid0) {
              if (data.summary !== undefined) {
                const _errs15 = errors;
                if (typeof data.summary !== "string") {
                  validate30.errors = [
                    {
                      instancePath: instancePath + "/summary",
                      schemaPath: "#/properties/summary/type",
                      keyword: "type",
                      params: { type: "string" },
                      message: "must be string",
                    },
                  ];
                  return false;
                }
                var valid0 = _errs15 === errors;
              } else {
                var valid0 = true;
              }
              if (valid0) {
                if (data.insights !== undefined) {
                  let data4 = data.insights;
                  const _errs17 = errors;
                  if (errors === _errs17) {
                    if (Array.isArray(data4)) {
                      var valid3 = true;
                      const len0 = data4.length;
                      for (let i0 = 0; i0 < len0; i0++) {
                        let data5 = data4[i0];
                        const _errs19 = errors;
                        if (errors === _errs19) {
                          if (
                            data5 &&
                            typeof data5 == "object" &&
                            !Array.isArray(data5)
                          ) {
                            for (const key0 in data5) {
                              const _errs22 = errors;
                              if (typeof data5[key0] !== "string") {
                                validate30.errors = [
                                  {
                                    instancePath:
                                      instancePath +
                                      "/insights/" +
                                      i0 +
                                      "/" +
                                      key0
                                        .replace(/~/g, "~0")
                                        .replace(/\//g, "~1"),
                                    schemaPath:
                                      "#/properties/insights/items/additionalProperties/type",
                                    keyword: "type",
                                    params: { type: "string" },
                                    message: "must be string",
                                  },
                                ];
                                return false;
                              }
                              var valid4 = _errs22 === errors;
                              if (!valid4) {
                                break;
                              }
                            }
                          } else {
                            validate30.errors = [
                              {
                                instancePath: instancePath + "/insights/" + i0,
                                schemaPath: "#/properties/insights/items/type",
                                keyword: "type",
                                params: { type: "object" },
                                message: "must be object",
                              },
                            ];
                            return false;
                          }
                        }
                        var valid3 = _errs19 === errors;
                        if (!valid3) {
                          break;
                        }
                      }
                    } else {
                      validate30.errors = [
                        {
                          instancePath: instancePath + "/insights",
                          schemaPath: "#/properties/insights/type",
                          keyword: "type",
                          params: { type: "array" },
                          message: "must be array",
                        },
                      ];
                      return false;
                    }
                  }
                  var valid0 = _errs17 === errors;
                } else {
                  var valid0 = true;
                }
                if (valid0) {
                  if (data.stats !== undefined) {
                    let data7 = data.stats;
                    const _errs24 = errors;
                    if (errors === _errs24) {
                      if (
                        data7 &&
                        typeof data7 == "object" &&
                        !Array.isArray(data7)
                      ) {
                      } else {
                        validate30.errors = [
                          {
                            instancePath: instancePath + "/stats",
                            schemaPath: "#/properties/stats/type",
                            keyword: "type",
                            params: { type: "object" },
                            message: "must be object",
                          },
                        ];
                        return false;
                      }
                    }
                    var valid0 = _errs24 === errors;
                  } else {
                    var valid0 = true;
                  }
                  if (valid0) {
                    if (data.columns !== undefined) {
                      let data8 = data.columns;
                      const _errs27 = errors;
                      if (errors === _errs27) {
                        if (Array.isArray(data8)) {
                          var valid5 = true;
                          const len1 = data8.length;
                          for (let i1 = 0; i1 < len1; i1++) {
                            const _errs29 = errors;
                            if (typeof data8[i1] !== "string") {
                              validate30.errors = [
                                {
                                  instancePath: instancePath + "/columns/" + i1,
                                  schemaPath: "#/properties/columns/items/type",
                                  keyword: "type",
                                  params: { type: "string" },
                                  message: "must be string",
                                },
                              ];
                              return false;
                            }
                            var valid5 = _errs29 === errors;
                            if (!valid5) {
                              break;
                            }
                          }
                        } else {
                          validate30.errors = [
                            {
                              instancePath: instancePath + "/columns",
                              schemaPath: "#/properties/columns/type",
                              keyword: "type",
                              params: { type: "array" },
                              message: "must be array",
                            },
                          ];
                          return false;
                        }
                      }
                      var valid0 = _errs27 === errors;
                    } else {
                      var valid0 = true;
                    }
                    if (valid0) {
                      if (data.rows !== undefined) {
                        let data10 = data.rows;
                        const _errs31 = errors;
                        if (errors === _errs31) {
                          if (Array.isArray(data10)) {
                            var valid6 = true;
                            const len2 = data10.length;
                            for (let i2 = 0; i2 < len2; i2++) {
                              const _errs33 = errors;
                              if (errors === _errs33) {
                                if (!Array.isArray(data10[i2])) {
                                  validate30.errors = [
                                    {
                                      instancePath:
                                        instancePath + "/rows/" + i2,
                                      schemaPath:
                                        "#/properties/rows/items/type",
                                      keyword: "type",
                                      params: { type: "array" },
                                      message: "must be array",
                                    },
                                  ];
                                  return false;
                                }
                              }
                              var valid6 = _errs33 === errors;
                              if (!valid6) {
                                break;
                              }
                            }
                          } else {
                            validate30.errors = [
                              {
                                instancePath: instancePath + "/rows",
                                schemaPath: "#/properties/rows/type",
                                keyword: "type",
                                params: { type: "array" },
                                message: "must be array",
                              },
                            ];
                            return false;
                          }
                        }
                        var valid0 = _errs31 === errors;
                      } else {
                        var valid0 = true;
                      }
                      if (valid0) {
                        if (data.sql !== undefined) {
                          const _errs35 = errors;
                          if (typeof data.sql !== "string") {
                            validate30.errors = [
                              {
                                instancePath: instancePath + "/sql",
                                schemaPath: "#/properties/sql/type",
                                keyword: "type",
                                params: { type: "string" },
                                message: "must be string",
                              },
                            ];
                            return false;
                          }
                          var valid0 = _errs35 === errors;
                        } else {
                          var valid0 = true;
                        }
                        if (valid0) {
                          if (data.chart !== undefined) {
                            let data13 = data.chart;
                            const _errs37 = errors;
                            const _errs38 = errors;
                            if (errors === _errs38) {
                              if (
                                data13 &&
                                typeof data13 == "object" &&
                                !Array.isArray(data13)
                              ) {
                                let missing1;
                                if (
                                  data13.type === undefined &&
                                  (missing1 = "type")
                                ) {
                                  validate30.errors = [
                                    {
                                      instancePath: instancePath + "/chart",
                                      schemaPath: "#/$defs/ChartOut/required",
                                      keyword: "required",
                                      params: { missingProperty: missing1 },
                                      message:
                                        "must have required property '" +
                                        missing1 +
                                        "'",
                                    },
                                  ];
                                  return false;
                                } else {
                                  if (data13.type !== undefined) {
                                    const _errs40 = errors;
                                    if (typeof data13.type !== "string") {
                                      validate30.errors = [
                                        {
                                          instancePath:
                                            instancePath + "/chart/type",
                                          schemaPath:
                                            "#/$defs/ChartOut/properties/type/type",
                                          keyword: "type",
                                          params: { type: "string" },
                                          message: "must be string",
                                        },
                                      ];
                                      return false;
                                    }
                                    var valid8 = _errs40 === errors;
                                  } else {
                                    var valid8 = true;
                                  }
                                  if (valid8) {
                                    if (data13.x !== undefined) {
                                      let data15 = data13.x;
                                      const _errs42 = errors;
                                      const _errs43 = errors;
                                      let valid9 = false;
                                      const _errs44 = errors;
                                      if (typeof data15 !== "string") {
                                        const err6 = {
                                          instancePath:
                                            instancePath + "/chart/x",
                                          schemaPath:
                                            "#/$defs/ChartOut/properties/x/anyOf/0/type",
                                          keyword: "type",
                                          params: { type: "string" },
                                          message: "must be string",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err6];
                                        } else {
                                          vErrors.push(err6);
                                        }
                                        errors++;
                                      }
                                      var _valid2 = _errs44 === errors;
                                      valid9 = valid9 || _valid2;
                                      const _errs46 = errors;
                                      if (data15 !== null) {
                                        const err7 = {
                                          instancePath:
                                            instancePath + "/chart/x",
                                          schemaPath:
                                            "#/$defs/ChartOut/properties/x/anyOf/1/type",
                                          keyword: "type",
                                          params: { type: "null" },
                                          message: "must be null",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err7];
                                        } else {
                                          vErrors.push(err7);
                                        }
                                        errors++;
                                      }
                                      var _valid2 = _errs46 === errors;
                                      valid9 = valid9 || _valid2;
                                      if (!valid9) {
                                        const err8 = {
                                          instancePath:
                                            instancePath + "/chart/x",
                                          schemaPath:
                                            "#/$defs/ChartOut/properties/x/anyOf",
                                          keyword: "anyOf",
                                          params: {},
                                          message:
                                            "must match a schema in anyOf",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err8];
                                        } else {
                                          vErrors.push(err8);
                                        }
                                        errors++;
                                        validate30.errors = vErrors;
                                        return false;
                                      } else {
                                        errors = _errs43;
                                        if (vErrors !== null) {
                                          if (_errs43) {
                                            vErrors.length = _errs43;
                                          } else {
                                            vErrors = null;
                                          }
                                        }
                                      }
                                      var valid8 = _errs42 === errors;
                                    } else {
                                      var valid8 = true;
                                    }
                                    if (valid8) {
                                      if (data13.y !== undefined) {
                                        let data16 = data13.y;
                                        const _errs48 = errors;
                                        const _errs49 = errors;
                                        let valid10 = false;
                                        const _errs50 = errors;
                                        if (typeof data16 !== "string") {
                                          const err9 = {
                                            instancePath:
                                              instancePath + "/chart/y",
                                            schemaPath:
                                              "#/$defs/ChartOut/properties/y/anyOf/0/type",
                                            keyword: "type",
                                            params: { type: "string" },
                                            message: "must be string",
                                          };
                                          if (vErrors === null) {
                                            vErrors = [err9];
                                          } else {
                                            vErrors.push(err9);
                                          }
                                          errors++;
                                        }
                                        var _valid3 = _errs50 === errors;
                                        valid10 = valid10 || _valid3;
                                        const _errs52 = errors;
                                        if (data16 !== null) {
                                          const err10 = {
                                            instancePath:
                                              instancePath + "/chart/y",
                                            schemaPath:
                                              "#/$defs/ChartOut/properties/y/anyOf/1/type",
                                            keyword: "type",
                                            params: { type: "null" },
                                            message: "must be null",
                                          };
                                          if (vErrors === null) {
                                            vErrors = [err10];
                                          } else {
                                            vErrors.push(err10);
                                          }
                                          errors++;
                                        }
                                        var _valid3 = _errs52 === errors;
                                        valid10 = valid10 || _valid3;
                                        if (!valid10) {
                                          const err11 = {
                                            instancePath:
                                              instancePath + "/chart/y",
                                            schemaPath:
                                              "#/$defs/ChartOut/properties/y/anyOf",
                                            keyword: "anyOf",
                                            params: {},
                                            message:
                                              "must match a schema in anyOf",
                                          };
                                          if (vErrors === null) {
                                            vErrors = [err11];
                                          } else {
                                            vErrors.push(err11);
                                          }
                                          errors++;
                                          validate30.errors = vErrors;
                                          return false;
                                        } else {
                                          errors = _errs49;
                                          if (vErrors !== null) {
                                            if (_errs49) {
                                              vErrors.length = _errs49;
                                            } else {
                                              vErrors = null;
                                            }
                                          }
                                        }
                                        var valid8 = _errs48 === errors;
                                      } else {
                                        var valid8 = true;
                                      }
                                    }
                                  }
                                }
                              } else {
                                validate30.errors = [
                                  {
                                    instancePath: instancePath + "/chart",
                                    schemaPath: "#/$defs/ChartOut/type",
                                    keyword: "type",
                                    params: { type: "object" },
                                    message: "must be object",
                                  },
                                ];
                                return false;
                              }
                            }
                            var valid0 = _errs37 === errors;
                          } else {
                            var valid0 = true;
                          }
                          if (valid0) {
                            if (data.chart_candidates !== undefined) {
                              let data17 = data.chart_candidates;
                              const _errs54 = errors;
                              if (errors === _errs54) {
                                if (Array.isArray(data17)) {
                                  var valid11 = true;
                                  const len3 = data17.length;
                                  for (let i3 = 0; i3 < len3; i3++) {
                                    let data18 = data17[i3];
                                    const _errs56 = errors;
                                    const _errs57 = errors;
                                    if (errors === _errs57) {
                                      if (
                                        data18 &&
                                        typeof data18 == "object" &&
                                        !Array.isArray(data18)
                                      ) {
                                        let missing2;
                                        if (
                                          data18.type === undefined &&
                                          (missing2 = "type")
                                        ) {
                                          validate30.errors = [
                                            {
                                              instancePath:
                                                instancePath +
                                                "/chart_candidates/" +
                                                i3,
                                              schemaPath:
                                                "#/$defs/ChartCandidateOut/required",
                                              keyword: "required",
                                              params: {
                                                missingProperty: missing2,
                                              },
                                              message:
                                                "must have required property '" +
                                                missing2 +
                                                "'",
                                            },
                                          ];
                                          return false;
                                        } else {
                                          if (data18.type !== undefined) {
                                            const _errs59 = errors;
                                            if (
                                              typeof data18.type !== "string"
                                            ) {
                                              validate30.errors = [
                                                {
                                                  instancePath:
                                                    instancePath +
                                                    "/chart_candidates/" +
                                                    i3 +
                                                    "/type",
                                                  schemaPath:
                                                    "#/$defs/ChartCandidateOut/properties/type/type",
                                                  keyword: "type",
                                                  params: { type: "string" },
                                                  message: "must be string",
                                                },
                                              ];
                                              return false;
                                            }
                                            var valid13 = _errs59 === errors;
                                          } else {
                                            var valid13 = true;
                                          }
                                          if (valid13) {
                                            if (data18.x !== undefined) {
                                              let data20 = data18.x;
                                              const _errs61 = errors;
                                              const _errs62 = errors;
                                              let valid14 = false;
                                              const _errs63 = errors;
                                              if (typeof data20 !== "string") {
                                                const err12 = {
                                                  instancePath:
                                                    instancePath +
                                                    "/chart_candidates/" +
                                                    i3 +
                                                    "/x",
                                                  schemaPath:
                                                    "#/$defs/ChartCandidateOut/properties/x/anyOf/0/type",
                                                  keyword: "type",
                                                  params: { type: "string" },
                                                  message: "must be string",
                                                };
                                                if (vErrors === null) {
                                                  vErrors = [err12];
                                                } else {
                                                  vErrors.push(err12);
                                                }
                                                errors++;
                                              }
                                              var _valid4 = _errs63 === errors;
                                              valid14 = valid14 || _valid4;
                                              const _errs65 = errors;
                                              if (data20 !== null) {
                                                const err13 = {
                                                  instancePath:
                                                    instancePath +
                                                    "/chart_candidates/" +
                                                    i3 +
                                                    "/x",
                                                  schemaPath:
                                                    "#/$defs/ChartCandidateOut/properties/x/anyOf/1/type",
                                                  keyword: "type",
                                                  params: { type: "null" },
                                                  message: "must be null",
                                                };
                                                if (vErrors === null) {
                                                  vErrors = [err13];
                                                } else {
                                                  vErrors.push(err13);
                                                }
                                                errors++;
                                              }
                                              var _valid4 = _errs65 === errors;
                                              valid14 = valid14 || _valid4;
                                              if (!valid14) {
                                                const err14 = {
                                                  instancePath:
                                                    instancePath +
                                                    "/chart_candidates/" +
                                                    i3 +
                                                    "/x",
                                                  schemaPath:
                                                    "#/$defs/ChartCandidateOut/properties/x/anyOf",
                                                  keyword: "anyOf",
                                                  params: {},
                                                  message:
                                                    "must match a schema in anyOf",
                                                };
                                                if (vErrors === null) {
                                                  vErrors = [err14];
                                                } else {
                                                  vErrors.push(err14);
                                                }
                                                errors++;
                                                validate30.errors = vErrors;
                                                return false;
                                              } else {
                                                errors = _errs62;
                                                if (vErrors !== null) {
                                                  if (_errs62) {
                                                    vErrors.length = _errs62;
                                                  } else {
                                                    vErrors = null;
                                                  }
                                                }
                                              }
                                              var valid13 = _errs61 === errors;
                                            } else {
                                              var valid13 = true;
                                            }
                                            if (valid13) {
                                              if (data18.y !== undefined) {
                                                let data21 = data18.y;
                                                const _errs67 = errors;
                                                if (errors === _errs67) {
                                                  if (Array.isArray(data21)) {
                                                    var valid15 = true;
                                                    const len4 = data21.length;
                                                    for (
                                                      let i4 = 0;
                                                      i4 < len4;
                                                      i4++
                                                    ) {
                                                      const _errs69 = errors;
                                                      if (
                                                        typeof data21[i4] !==
                                                        "string"
                                                      ) {
                                                        validate30.errors = [
                                                          {
                                                            instancePath:
                                                              instancePath +
                                                              "/chart_candidates/" +
                                                              i3 +
                                                              "/y/" +
                                                              i4,
                                                            schemaPath:
                                                              "#/$defs/ChartCandidateOut/properties/y/items/type",
                                                            keyword: "type",
                                                            params: {
                                                              type: "string",
                                                            },
                                                            message:
                                                              "must be string",
                                                          },
                                                        ];
                                                        return false;
                                                      }
                                                      var valid15 =
                                                        _errs69 === errors;
                                                      if (!valid15) {
                                                        break;
                                                      }
                                                    }
                                                  } else {
                                                    validate30.errors = [
                                                      {
                                                        instancePath:
                                                          instancePath +
                                                          "/chart_candidates/" +
                                                          i3 +
                                                          "/y",
                                                        schemaPath:
                                                          "#/$defs/ChartCandidateOut/properties/y/type",
                                                        keyword: "type",
                                                        params: {
                                                          type: "array",
                                                        },
                                                        message:
                                                          "must be array",
                                                      },
                                                    ];
                                                    return false;
                                                  }
                                                }
                                                var valid13 =
                                                  _errs67 === errors;
                                              } else {
                                                var valid13 = true;
                                              }
                                              if (valid13) {
                                                if (
                                                  data18.series !== undefined
                                                ) {
                                                  let data23 = data18.series;
                                                  const _errs71 = errors;
                                                  const _errs72 = errors;
                                                  let valid16 = false;
                                                  const _errs73 = errors;
                                                  if (
                                                    typeof data23 !== "string"
                                                  ) {
                                                    const err15 = {
                                                      instancePath:
                                                        instancePath +
                                                        "/chart_candidates/" +
                                                        i3 +
                                                        "/series",
                                                      schemaPath:
                                                        "#/$defs/ChartCandidateOut/properties/series/anyOf/0/type",
                                                      keyword: "type",
                                                      params: {
                                                        type: "string",
                                                      },
                                                      message: "must be string",
                                                    };
                                                    if (vErrors === null) {
                                                      vErrors = [err15];
                                                    } else {
                                                      vErrors.push(err15);
                                                    }
                                                    errors++;
                                                  }
                                                  var _valid5 =
                                                    _errs73 === errors;
                                                  valid16 = valid16 || _valid5;
                                                  const _errs75 = errors;
                                                  if (data23 !== null) {
                                                    const err16 = {
                                                      instancePath:
                                                        instancePath +
                                                        "/chart_candidates/" +
                                                        i3 +
                                                        "/series",
                                                      schemaPath:
                                                        "#/$defs/ChartCandidateOut/properties/series/anyOf/1/type",
                                                      keyword: "type",
                                                      params: { type: "null" },
                                                      message: "must be null",
                                                    };
                                                    if (vErrors === null) {
                                                      vErrors = [err16];
                                                    } else {
                                                      vErrors.push(err16);
                                                    }
                                                    errors++;
                                                  }
                                                  var _valid5 =
                                                    _errs75 === errors;
                                                  valid16 = valid16 || _valid5;
                                                  if (!valid16) {
                                                    const err17 = {
                                                      instancePath:
                                                        instancePath +
                                                        "/chart_candidates/" +
                                                        i3 +
                                                        "/series",
                                                      schemaPath:
                                                        "#/$defs/ChartCandidateOut/properties/series/anyOf",
                                                      keyword: "anyOf",
                                                      params: {},
                                                      message:
                                                        "must match a schema in anyOf",
                                                    };
                                                    if (vErrors === null) {
                                                      vErrors = [err17];
                                                    } else {
                                                      vErrors.push(err17);
                                                    }
                                                    errors++;
                                                    validate30.errors = vErrors;
                                                    return false;
                                                  } else {
                                                    errors = _errs72;
                                                    if (vErrors !== null) {
                                                      if (_errs72) {
                                                        vErrors.length =
                                                          _errs72;
                                                      } else {
                                                        vErrors = null;
                                                      }
                                                    }
                                                  }
                                                  var valid13 =
                                                    _errs71 === errors;
                                                } else {
                                                  var valid13 = true;
                                                }
                                              }
                                            }
                                          }
                                        }
                                      } else {
                                        validate30.errors = [
                                          {
                                            instancePath:
                                              instancePath +
                                              "/chart_candidates/" +
                                              i3,
                                            schemaPath:
                                              "#/$defs/ChartCandidateOut/type",
                                            keyword: "type",
                                            params: { type: "object" },
                                            message: "must be object",
                                          },
                                        ];
                                        return false;
                                      }
                                    }
                                    var valid11 = _errs56 === errors;
                                    if (!valid11) {
                                      break;
                                    }
                                  }
                                } else {
                                  validate30.errors = [
                                    {
                                      instancePath:
                                        instancePath + "/chart_candidates",
                                      schemaPath:
                                        "#/properties/chart_candidates/type",
                                      keyword: "type",
                                      params: { type: "array" },
                                      message: "must be array",
                                    },
                                  ];
                                  return false;
                                }
                              }
                              var valid0 = _errs54 === errors;
                            } else {
                              var valid0 = true;
                            }
                            if (valid0) {
                              if (data.retry_count !== undefined) {
                                let data24 = data.retry_count;
                                const _errs77 = errors;
                                if (!(
                                  typeof data24 == "number" &&
                                  !(data24 % 1) &&
                                  !isNaN(data24) &&
                                  isFinite(data24)
                                )) {
                                  validate30.errors = [
                                    {
                                      instancePath:
                                        instancePath + "/retry_count",
                                      schemaPath:
                                        "#/properties/retry_count/type",
                                      keyword: "type",
                                      params: { type: "integer" },
                                      message: "must be integer",
                                    },
                                  ];
                                  return false;
                                }
                                var valid0 = _errs77 === errors;
                              } else {
                                var valid0 = true;
                              }
                              if (valid0) {
                                if (data.error !== undefined) {
                                  let data25 = data.error;
                                  const _errs79 = errors;
                                  const _errs80 = errors;
                                  let valid17 = false;
                                  const _errs81 = errors;
                                  if (typeof data25 !== "string") {
                                    const err18 = {
                                      instancePath: instancePath + "/error",
                                      schemaPath:
                                        "#/properties/error/anyOf/0/type",
                                      keyword: "type",
                                      params: { type: "string" },
                                      message: "must be string",
                                    };
                                    if (vErrors === null) {
                                      vErrors = [err18];
                                    } else {
                                      vErrors.push(err18);
                                    }
                                    errors++;
                                  }
                                  var _valid6 = _errs81 === errors;
                                  valid17 = valid17 || _valid6;
                                  const _errs83 = errors;
                                  if (data25 !== null) {
                                    const err19 = {
                                      instancePath: instancePath + "/error",
                                      schemaPath:
                                        "#/properties/error/anyOf/1/type",
                                      keyword: "type",
                                      params: { type: "null" },
                                      message: "must be null",
                                    };
                                    if (vErrors === null) {
                                      vErrors = [err19];
                                    } else {
                                      vErrors.push(err19);
                                    }
                                    errors++;
                                  }
                                  var _valid6 = _errs83 === errors;
                                  valid17 = valid17 || _valid6;
                                  if (!valid17) {
                                    const err20 = {
                                      instancePath: instancePath + "/error",
                                      schemaPath: "#/properties/error/anyOf",
                                      keyword: "anyOf",
                                      params: {},
                                      message: "must match a schema in anyOf",
                                    };
                                    if (vErrors === null) {
                                      vErrors = [err20];
                                    } else {
                                      vErrors.push(err20);
                                    }
                                    errors++;
                                    validate30.errors = vErrors;
                                    return false;
                                  } else {
                                    errors = _errs80;
                                    if (vErrors !== null) {
                                      if (_errs80) {
                                        vErrors.length = _errs80;
                                      } else {
                                        vErrors = null;
                                      }
                                    }
                                  }
                                  var valid0 = _errs79 === errors;
                                } else {
                                  var valid0 = true;
                                }
                                if (valid0) {
                                  if (data.error_detail !== undefined) {
                                    let data26 = data.error_detail;
                                    const _errs85 = errors;
                                    const _errs86 = errors;
                                    let valid18 = false;
                                    const _errs87 = errors;
                                    if (typeof data26 !== "string") {
                                      const err21 = {
                                        instancePath:
                                          instancePath + "/error_detail",
                                        schemaPath:
                                          "#/properties/error_detail/anyOf/0/type",
                                        keyword: "type",
                                        params: { type: "string" },
                                        message: "must be string",
                                      };
                                      if (vErrors === null) {
                                        vErrors = [err21];
                                      } else {
                                        vErrors.push(err21);
                                      }
                                      errors++;
                                    }
                                    var _valid7 = _errs87 === errors;
                                    valid18 = valid18 || _valid7;
                                    const _errs89 = errors;
                                    if (data26 !== null) {
                                      const err22 = {
                                        instancePath:
                                          instancePath + "/error_detail",
                                        schemaPath:
                                          "#/properties/error_detail/anyOf/1/type",
                                        keyword: "type",
                                        params: { type: "null" },
                                        message: "must be null",
                                      };
                                      if (vErrors === null) {
                                        vErrors = [err22];
                                      } else {
                                        vErrors.push(err22);
                                      }
                                      errors++;
                                    }
                                    var _valid7 = _errs89 === errors;
                                    valid18 = valid18 || _valid7;
                                    if (!valid18) {
                                      const err23 = {
                                        instancePath:
                                          instancePath + "/error_detail",
                                        schemaPath:
                                          "#/properties/error_detail/anyOf",
                                        keyword: "anyOf",
                                        params: {},
                                        message: "must match a schema in anyOf",
                                      };
                                      if (vErrors === null) {
                                        vErrors = [err23];
                                      } else {
                                        vErrors.push(err23);
                                      }
                                      errors++;
                                      validate30.errors = vErrors;
                                      return false;
                                    } else {
                                      errors = _errs86;
                                      if (vErrors !== null) {
                                        if (_errs86) {
                                          vErrors.length = _errs86;
                                        } else {
                                          vErrors = null;
                                        }
                                      }
                                    }
                                    var valid0 = _errs85 === errors;
                                  } else {
                                    var valid0 = true;
                                  }
                                  if (valid0) {
                                    if (data.message_id !== undefined) {
                                      let data27 = data.message_id;
                                      const _errs91 = errors;
                                      const _errs92 = errors;
                                      let valid19 = false;
                                      const _errs93 = errors;
                                      if (typeof data27 !== "string") {
                                        const err24 = {
                                          instancePath:
                                            instancePath + "/message_id",
                                          schemaPath:
                                            "#/properties/message_id/anyOf/0/type",
                                          keyword: "type",
                                          params: { type: "string" },
                                          message: "must be string",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err24];
                                        } else {
                                          vErrors.push(err24);
                                        }
                                        errors++;
                                      }
                                      var _valid8 = _errs93 === errors;
                                      valid19 = valid19 || _valid8;
                                      const _errs95 = errors;
                                      if (data27 !== null) {
                                        const err25 = {
                                          instancePath:
                                            instancePath + "/message_id",
                                          schemaPath:
                                            "#/properties/message_id/anyOf/1/type",
                                          keyword: "type",
                                          params: { type: "null" },
                                          message: "must be null",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err25];
                                        } else {
                                          vErrors.push(err25);
                                        }
                                        errors++;
                                      }
                                      var _valid8 = _errs95 === errors;
                                      valid19 = valid19 || _valid8;
                                      if (!valid19) {
                                        const err26 = {
                                          instancePath:
                                            instancePath + "/message_id",
                                          schemaPath:
                                            "#/properties/message_id/anyOf",
                                          keyword: "anyOf",
                                          params: {},
                                          message:
                                            "must match a schema in anyOf",
                                        };
                                        if (vErrors === null) {
                                          vErrors = [err26];
                                        } else {
                                          vErrors.push(err26);
                                        }
                                        errors++;
                                        validate30.errors = vErrors;
                                        return false;
                                      } else {
                                        errors = _errs92;
                                        if (vErrors !== null) {
                                          if (_errs92) {
                                            vErrors.length = _errs92;
                                          } else {
                                            vErrors = null;
                                          }
                                        }
                                      }
                                      var valid0 = _errs91 === errors;
                                    } else {
                                      var valid0 = true;
                                    }
                                    if (valid0) {
                                      if (data.datasource_key !== undefined) {
                                        const _errs97 = errors;
                                        if (
                                          typeof data.datasource_key !==
                                          "string"
                                        ) {
                                          validate30.errors = [
                                            {
                                              instancePath:
                                                instancePath +
                                                "/datasource_key",
                                              schemaPath:
                                                "#/properties/datasource_key/type",
                                              keyword: "type",
                                              params: { type: "string" },
                                              message: "must be string",
                                            },
                                          ];
                                          return false;
                                        }
                                        var valid0 = _errs97 === errors;
                                      } else {
                                        var valid0 = true;
                                      }
                                      if (valid0) {
                                        if (
                                          data.pending_tool_calls !== undefined
                                        ) {
                                          let data29 = data.pending_tool_calls;
                                          const _errs99 = errors;
                                          if (errors === _errs99) {
                                            if (Array.isArray(data29)) {
                                              var valid20 = true;
                                              const len5 = data29.length;
                                              for (
                                                let i5 = 0;
                                                i5 < len5;
                                                i5++
                                              ) {
                                                let data30 = data29[i5];
                                                const _errs101 = errors;
                                                const _errs102 = errors;
                                                if (errors === _errs102) {
                                                  if (
                                                    data30 &&
                                                    typeof data30 == "object" &&
                                                    !Array.isArray(data30)
                                                  ) {
                                                    let missing3;
                                                    if (
                                                      (data30.confirm_id ===
                                                        undefined &&
                                                        (missing3 =
                                                          "confirm_id")) ||
                                                      (data30.tool_name ===
                                                        undefined &&
                                                        (missing3 =
                                                          "tool_name"))
                                                    ) {
                                                      validate30.errors = [
                                                        {
                                                          instancePath:
                                                            instancePath +
                                                            "/pending_tool_calls/" +
                                                            i5,
                                                          schemaPath:
                                                            "#/$defs/PendingToolCallOut/required",
                                                          keyword: "required",
                                                          params: {
                                                            missingProperty:
                                                              missing3,
                                                          },
                                                          message:
                                                            "must have required property '" +
                                                            missing3 +
                                                            "'",
                                                        },
                                                      ];
                                                      return false;
                                                    } else {
                                                      if (
                                                        data30.confirm_id !==
                                                        undefined
                                                      ) {
                                                        const _errs104 = errors;
                                                        if (
                                                          typeof data30.confirm_id !==
                                                          "string"
                                                        ) {
                                                          validate30.errors = [
                                                            {
                                                              instancePath:
                                                                instancePath +
                                                                "/pending_tool_calls/" +
                                                                i5 +
                                                                "/confirm_id",
                                                              schemaPath:
                                                                "#/$defs/PendingToolCallOut/properties/confirm_id/type",
                                                              keyword: "type",
                                                              params: {
                                                                type: "string",
                                                              },
                                                              message:
                                                                "must be string",
                                                            },
                                                          ];
                                                          return false;
                                                        }
                                                        var valid22 =
                                                          _errs104 === errors;
                                                      } else {
                                                        var valid22 = true;
                                                      }
                                                      if (valid22) {
                                                        if (
                                                          data30.tool_name !==
                                                          undefined
                                                        ) {
                                                          const _errs106 =
                                                            errors;
                                                          if (
                                                            typeof data30.tool_name !==
                                                            "string"
                                                          ) {
                                                            validate30.errors =
                                                              [
                                                                {
                                                                  instancePath:
                                                                    instancePath +
                                                                    "/pending_tool_calls/" +
                                                                    i5 +
                                                                    "/tool_name",
                                                                  schemaPath:
                                                                    "#/$defs/PendingToolCallOut/properties/tool_name/type",
                                                                  keyword:
                                                                    "type",
                                                                  params: {
                                                                    type: "string",
                                                                  },
                                                                  message:
                                                                    "must be string",
                                                                },
                                                              ];
                                                            return false;
                                                          }
                                                          var valid22 =
                                                            _errs106 === errors;
                                                        } else {
                                                          var valid22 = true;
                                                        }
                                                        if (valid22) {
                                                          if (
                                                            data30.args_digest !==
                                                            undefined
                                                          ) {
                                                            const _errs108 =
                                                              errors;
                                                            if (
                                                              typeof data30.args_digest !==
                                                              "string"
                                                            ) {
                                                              validate30.errors =
                                                                [
                                                                  {
                                                                    instancePath:
                                                                      instancePath +
                                                                      "/pending_tool_calls/" +
                                                                      i5 +
                                                                      "/args_digest",
                                                                    schemaPath:
                                                                      "#/$defs/PendingToolCallOut/properties/args_digest/type",
                                                                    keyword:
                                                                      "type",
                                                                    params: {
                                                                      type: "string",
                                                                    },
                                                                    message:
                                                                      "must be string",
                                                                  },
                                                                ];
                                                              return false;
                                                            }
                                                            var valid22 =
                                                              _errs108 ===
                                                              errors;
                                                          } else {
                                                            var valid22 = true;
                                                          }
                                                        }
                                                      }
                                                    }
                                                  } else {
                                                    validate30.errors = [
                                                      {
                                                        instancePath:
                                                          instancePath +
                                                          "/pending_tool_calls/" +
                                                          i5,
                                                        schemaPath:
                                                          "#/$defs/PendingToolCallOut/type",
                                                        keyword: "type",
                                                        params: {
                                                          type: "object",
                                                        },
                                                        message:
                                                          "must be object",
                                                      },
                                                    ];
                                                    return false;
                                                  }
                                                }
                                                var valid20 =
                                                  _errs101 === errors;
                                                if (!valid20) {
                                                  break;
                                                }
                                              }
                                            } else {
                                              validate30.errors = [
                                                {
                                                  instancePath:
                                                    instancePath +
                                                    "/pending_tool_calls",
                                                  schemaPath:
                                                    "#/properties/pending_tool_calls/type",
                                                  keyword: "type",
                                                  params: { type: "array" },
                                                  message: "must be array",
                                                },
                                              ];
                                              return false;
                                            }
                                          }
                                          var valid0 = _errs99 === errors;
                                        } else {
                                          var valid0 = true;
                                        }
                                        if (valid0) {
                                          if (data.artifacts !== undefined) {
                                            let data34 = data.artifacts;
                                            const _errs110 = errors;
                                            if (errors === _errs110) {
                                              if (Array.isArray(data34)) {
                                                var valid23 = true;
                                                const len6 = data34.length;
                                                for (
                                                  let i6 = 0;
                                                  i6 < len6;
                                                  i6++
                                                ) {
                                                  let data35 = data34[i6];
                                                  const _errs112 = errors;
                                                  const _errs113 = errors;
                                                  if (errors === _errs113) {
                                                    if (
                                                      data35 &&
                                                      typeof data35 ==
                                                        "object" &&
                                                      !Array.isArray(data35)
                                                    ) {
                                                      let missing4;
                                                      if (
                                                        (data35.type ===
                                                          undefined &&
                                                          (missing4 =
                                                            "type")) ||
                                                        (data35.content ===
                                                          undefined &&
                                                          (missing4 =
                                                            "content"))
                                                      ) {
                                                        validate30.errors = [
                                                          {
                                                            instancePath:
                                                              instancePath +
                                                              "/artifacts/" +
                                                              i6,
                                                            schemaPath:
                                                              "#/$defs/ArtifactOut/required",
                                                            keyword: "required",
                                                            params: {
                                                              missingProperty:
                                                                missing4,
                                                            },
                                                            message:
                                                              "must have required property '" +
                                                              missing4 +
                                                              "'",
                                                          },
                                                        ];
                                                        return false;
                                                      } else {
                                                        if (
                                                          data35.type !==
                                                          undefined
                                                        ) {
                                                          const _errs115 =
                                                            errors;
                                                          if (
                                                            typeof data35.type !==
                                                            "string"
                                                          ) {
                                                            validate30.errors =
                                                              [
                                                                {
                                                                  instancePath:
                                                                    instancePath +
                                                                    "/artifacts/" +
                                                                    i6 +
                                                                    "/type",
                                                                  schemaPath:
                                                                    "#/$defs/ArtifactOut/properties/type/type",
                                                                  keyword:
                                                                    "type",
                                                                  params: {
                                                                    type: "string",
                                                                  },
                                                                  message:
                                                                    "must be string",
                                                                },
                                                              ];
                                                            return false;
                                                          }
                                                          var valid25 =
                                                            _errs115 === errors;
                                                        } else {
                                                          var valid25 = true;
                                                        }
                                                        if (valid25) {
                                                          if (
                                                            data35.content !==
                                                            undefined
                                                          ) {
                                                            const _errs117 =
                                                              errors;
                                                            if (
                                                              typeof data35.content !==
                                                              "string"
                                                            ) {
                                                              validate30.errors =
                                                                [
                                                                  {
                                                                    instancePath:
                                                                      instancePath +
                                                                      "/artifacts/" +
                                                                      i6 +
                                                                      "/content",
                                                                    schemaPath:
                                                                      "#/$defs/ArtifactOut/properties/content/type",
                                                                    keyword:
                                                                      "type",
                                                                    params: {
                                                                      type: "string",
                                                                    },
                                                                    message:
                                                                      "must be string",
                                                                  },
                                                                ];
                                                              return false;
                                                            }
                                                            var valid25 =
                                                              _errs117 ===
                                                              errors;
                                                          } else {
                                                            var valid25 = true;
                                                          }
                                                          if (valid25) {
                                                            if (
                                                              data35.metadata !==
                                                              undefined
                                                            ) {
                                                              let data38 =
                                                                data35.metadata;
                                                              const _errs119 =
                                                                errors;
                                                              if (
                                                                errors ===
                                                                _errs119
                                                              ) {
                                                                if (
                                                                  data38 &&
                                                                  typeof data38 ==
                                                                    "object" &&
                                                                  !Array.isArray(
                                                                    data38,
                                                                  )
                                                                ) {
                                                                } else {
                                                                  validate30.errors =
                                                                    [
                                                                      {
                                                                        instancePath:
                                                                          instancePath +
                                                                          "/artifacts/" +
                                                                          i6 +
                                                                          "/metadata",
                                                                        schemaPath:
                                                                          "#/$defs/ArtifactOut/properties/metadata/type",
                                                                        keyword:
                                                                          "type",
                                                                        params:
                                                                          {
                                                                            type: "object",
                                                                          },
                                                                        message:
                                                                          "must be object",
                                                                      },
                                                                    ];
                                                                  return false;
                                                                }
                                                              }
                                                              var valid25 =
                                                                _errs119 ===
                                                                errors;
                                                            } else {
                                                              var valid25 = true;
                                                            }
                                                          }
                                                        }
                                                      }
                                                    } else {
                                                      validate30.errors = [
                                                        {
                                                          instancePath:
                                                            instancePath +
                                                            "/artifacts/" +
                                                            i6,
                                                          schemaPath:
                                                            "#/$defs/ArtifactOut/type",
                                                          keyword: "type",
                                                          params: {
                                                            type: "object",
                                                          },
                                                          message:
                                                            "must be object",
                                                        },
                                                      ];
                                                      return false;
                                                    }
                                                  }
                                                  var valid23 =
                                                    _errs112 === errors;
                                                  if (!valid23) {
                                                    break;
                                                  }
                                                }
                                              } else {
                                                validate30.errors = [
                                                  {
                                                    instancePath:
                                                      instancePath +
                                                      "/artifacts",
                                                    schemaPath:
                                                      "#/properties/artifacts/type",
                                                    keyword: "type",
                                                    params: { type: "array" },
                                                    message: "must be array",
                                                  },
                                                ];
                                                return false;
                                              }
                                            }
                                            var valid0 = _errs110 === errors;
                                          } else {
                                            var valid0 = true;
                                          }
                                          if (valid0) {
                                            if (
                                              data.attachments !== undefined
                                            ) {
                                              let data39 = data.attachments;
                                              const _errs122 = errors;
                                              if (errors === _errs122) {
                                                if (Array.isArray(data39)) {
                                                  var valid26 = true;
                                                  const len7 = data39.length;
                                                  for (
                                                    let i7 = 0;
                                                    i7 < len7;
                                                    i7++
                                                  ) {
                                                    let data40 = data39[i7];
                                                    const _errs124 = errors;
                                                    if (errors === _errs124) {
                                                      if (
                                                        data40 &&
                                                        typeof data40 ==
                                                          "object" &&
                                                        !Array.isArray(data40)
                                                      ) {
                                                      } else {
                                                        validate30.errors = [
                                                          {
                                                            instancePath:
                                                              instancePath +
                                                              "/attachments/" +
                                                              i7,
                                                            schemaPath:
                                                              "#/properties/attachments/items/type",
                                                            keyword: "type",
                                                            params: {
                                                              type: "object",
                                                            },
                                                            message:
                                                              "must be object",
                                                          },
                                                        ];
                                                        return false;
                                                      }
                                                    }
                                                    var valid26 =
                                                      _errs124 === errors;
                                                    if (!valid26) {
                                                      break;
                                                    }
                                                  }
                                                } else {
                                                  validate30.errors = [
                                                    {
                                                      instancePath:
                                                        instancePath +
                                                        "/attachments",
                                                      schemaPath:
                                                        "#/properties/attachments/type",
                                                      keyword: "type",
                                                      params: { type: "array" },
                                                      message: "must be array",
                                                    },
                                                  ];
                                                  return false;
                                                }
                                              }
                                              var valid0 = _errs122 === errors;
                                            } else {
                                              var valid0 = true;
                                            }
                                            if (valid0) {
                                              if (
                                                data.column_labels !== undefined
                                              ) {
                                                let data41 = data.column_labels;
                                                const _errs127 = errors;
                                                if (errors === _errs127) {
                                                  if (Array.isArray(data41)) {
                                                    var valid27 = true;
                                                    const len8 = data41.length;
                                                    for (
                                                      let i8 = 0;
                                                      i8 < len8;
                                                      i8++
                                                    ) {
                                                      const _errs129 = errors;
                                                      if (
                                                        typeof data41[i8] !==
                                                        "string"
                                                      ) {
                                                        validate30.errors = [
                                                          {
                                                            instancePath:
                                                              instancePath +
                                                              "/column_labels/" +
                                                              i8,
                                                            schemaPath:
                                                              "#/properties/column_labels/items/type",
                                                            keyword: "type",
                                                            params: {
                                                              type: "string",
                                                            },
                                                            message:
                                                              "must be string",
                                                          },
                                                        ];
                                                        return false;
                                                      }
                                                      var valid27 =
                                                        _errs129 === errors;
                                                      if (!valid27) {
                                                        break;
                                                      }
                                                    }
                                                  } else {
                                                    validate30.errors = [
                                                      {
                                                        instancePath:
                                                          instancePath +
                                                          "/column_labels",
                                                        schemaPath:
                                                          "#/properties/column_labels/type",
                                                        keyword: "type",
                                                        params: {
                                                          type: "array",
                                                        },
                                                        message:
                                                          "must be array",
                                                      },
                                                    ];
                                                    return false;
                                                  }
                                                }
                                                var valid0 =
                                                  _errs127 === errors;
                                              } else {
                                                var valid0 = true;
                                              }
                                              if (valid0) {
                                                if (
                                                  data.metric_hits !== undefined
                                                ) {
                                                  let data43 = data.metric_hits;
                                                  const _errs131 = errors;
                                                  if (errors === _errs131) {
                                                    if (Array.isArray(data43)) {
                                                      var valid28 = true;
                                                      const len9 =
                                                        data43.length;
                                                      for (
                                                        let i9 = 0;
                                                        i9 < len9;
                                                        i9++
                                                      ) {
                                                        let data44 = data43[i9];
                                                        const _errs133 = errors;
                                                        const _errs134 = errors;
                                                        if (
                                                          errors === _errs134
                                                        ) {
                                                          if (
                                                            data44 &&
                                                            typeof data44 ==
                                                              "object" &&
                                                            !Array.isArray(
                                                              data44,
                                                            )
                                                          ) {
                                                            let missing5;
                                                            if (
                                                              (data44.metric_id ===
                                                                undefined &&
                                                                (missing5 =
                                                                  "metric_id")) ||
                                                              (data44.metric_name ===
                                                                undefined &&
                                                                (missing5 =
                                                                  "metric_name")) ||
                                                              (data44.sql_template ===
                                                                undefined &&
                                                                (missing5 =
                                                                  "sql_template"))
                                                            ) {
                                                              validate30.errors =
                                                                [
                                                                  {
                                                                    instancePath:
                                                                      instancePath +
                                                                      "/metric_hits/" +
                                                                      i9,
                                                                    schemaPath:
                                                                      "#/$defs/MetricHitOut/required",
                                                                    keyword:
                                                                      "required",
                                                                    params: {
                                                                      missingProperty:
                                                                        missing5,
                                                                    },
                                                                    message:
                                                                      "must have required property '" +
                                                                      missing5 +
                                                                      "'",
                                                                  },
                                                                ];
                                                              return false;
                                                            } else {
                                                              if (
                                                                data44.metric_id !==
                                                                undefined
                                                              ) {
                                                                const _errs136 =
                                                                  errors;
                                                                if (
                                                                  typeof data44.metric_id !==
                                                                  "string"
                                                                ) {
                                                                  validate30.errors =
                                                                    [
                                                                      {
                                                                        instancePath:
                                                                          instancePath +
                                                                          "/metric_hits/" +
                                                                          i9 +
                                                                          "/metric_id",
                                                                        schemaPath:
                                                                          "#/$defs/MetricHitOut/properties/metric_id/type",
                                                                        keyword:
                                                                          "type",
                                                                        params:
                                                                          {
                                                                            type: "string",
                                                                          },
                                                                        message:
                                                                          "must be string",
                                                                      },
                                                                    ];
                                                                  return false;
                                                                }
                                                                var valid30 =
                                                                  _errs136 ===
                                                                  errors;
                                                              } else {
                                                                var valid30 = true;
                                                              }
                                                              if (valid30) {
                                                                if (
                                                                  data44.metric_name !==
                                                                  undefined
                                                                ) {
                                                                  const _errs138 =
                                                                    errors;
                                                                  if (
                                                                    typeof data44.metric_name !==
                                                                    "string"
                                                                  ) {
                                                                    validate30.errors =
                                                                      [
                                                                        {
                                                                          instancePath:
                                                                            instancePath +
                                                                            "/metric_hits/" +
                                                                            i9 +
                                                                            "/metric_name",
                                                                          schemaPath:
                                                                            "#/$defs/MetricHitOut/properties/metric_name/type",
                                                                          keyword:
                                                                            "type",
                                                                          params:
                                                                            {
                                                                              type: "string",
                                                                            },
                                                                          message:
                                                                            "must be string",
                                                                        },
                                                                      ];
                                                                    return false;
                                                                  }
                                                                  var valid30 =
                                                                    _errs138 ===
                                                                    errors;
                                                                } else {
                                                                  var valid30 = true;
                                                                }
                                                                if (valid30) {
                                                                  if (
                                                                    data44.sql_template !==
                                                                    undefined
                                                                  ) {
                                                                    const _errs140 =
                                                                      errors;
                                                                    if (
                                                                      typeof data44.sql_template !==
                                                                      "string"
                                                                    ) {
                                                                      validate30.errors =
                                                                        [
                                                                          {
                                                                            instancePath:
                                                                              instancePath +
                                                                              "/metric_hits/" +
                                                                              i9 +
                                                                              "/sql_template",
                                                                            schemaPath:
                                                                              "#/$defs/MetricHitOut/properties/sql_template/type",
                                                                            keyword:
                                                                              "type",
                                                                            params:
                                                                              {
                                                                                type: "string",
                                                                              },
                                                                            message:
                                                                              "must be string",
                                                                          },
                                                                        ];
                                                                      return false;
                                                                    }
                                                                    var valid30 =
                                                                      _errs140 ===
                                                                      errors;
                                                                  } else {
                                                                    var valid30 = true;
                                                                  }
                                                                  if (valid30) {
                                                                    if (
                                                                      data44.updated_at !==
                                                                      undefined
                                                                    ) {
                                                                      let data48 =
                                                                        data44.updated_at;
                                                                      const _errs142 =
                                                                        errors;
                                                                      const _errs143 =
                                                                        errors;
                                                                      let valid31 = false;
                                                                      const _errs144 =
                                                                        errors;
                                                                      if (
                                                                        typeof data48 !==
                                                                        "string"
                                                                      ) {
                                                                        const err27 =
                                                                          {
                                                                            instancePath:
                                                                              instancePath +
                                                                              "/metric_hits/" +
                                                                              i9 +
                                                                              "/updated_at",
                                                                            schemaPath:
                                                                              "#/$defs/MetricHitOut/properties/updated_at/anyOf/0/type",
                                                                            keyword:
                                                                              "type",
                                                                            params:
                                                                              {
                                                                                type: "string",
                                                                              },
                                                                            message:
                                                                              "must be string",
                                                                          };
                                                                        if (
                                                                          vErrors ===
                                                                          null
                                                                        ) {
                                                                          vErrors =
                                                                            [
                                                                              err27,
                                                                            ];
                                                                        } else {
                                                                          vErrors.push(
                                                                            err27,
                                                                          );
                                                                        }
                                                                        errors++;
                                                                      }
                                                                      var _valid9 =
                                                                        _errs144 ===
                                                                        errors;
                                                                      valid31 =
                                                                        valid31 ||
                                                                        _valid9;
                                                                      const _errs146 =
                                                                        errors;
                                                                      if (
                                                                        data48 !==
                                                                        null
                                                                      ) {
                                                                        const err28 =
                                                                          {
                                                                            instancePath:
                                                                              instancePath +
                                                                              "/metric_hits/" +
                                                                              i9 +
                                                                              "/updated_at",
                                                                            schemaPath:
                                                                              "#/$defs/MetricHitOut/properties/updated_at/anyOf/1/type",
                                                                            keyword:
                                                                              "type",
                                                                            params:
                                                                              {
                                                                                type: "null",
                                                                              },
                                                                            message:
                                                                              "must be null",
                                                                          };
                                                                        if (
                                                                          vErrors ===
                                                                          null
                                                                        ) {
                                                                          vErrors =
                                                                            [
                                                                              err28,
                                                                            ];
                                                                        } else {
                                                                          vErrors.push(
                                                                            err28,
                                                                          );
                                                                        }
                                                                        errors++;
                                                                      }
                                                                      var _valid9 =
                                                                        _errs146 ===
                                                                        errors;
                                                                      valid31 =
                                                                        valid31 ||
                                                                        _valid9;
                                                                      if (
                                                                        !valid31
                                                                      ) {
                                                                        const err29 =
                                                                          {
                                                                            instancePath:
                                                                              instancePath +
                                                                              "/metric_hits/" +
                                                                              i9 +
                                                                              "/updated_at",
                                                                            schemaPath:
                                                                              "#/$defs/MetricHitOut/properties/updated_at/anyOf",
                                                                            keyword:
                                                                              "anyOf",
                                                                            params:
                                                                              {},
                                                                            message:
                                                                              "must match a schema in anyOf",
                                                                          };
                                                                        if (
                                                                          vErrors ===
                                                                          null
                                                                        ) {
                                                                          vErrors =
                                                                            [
                                                                              err29,
                                                                            ];
                                                                        } else {
                                                                          vErrors.push(
                                                                            err29,
                                                                          );
                                                                        }
                                                                        errors++;
                                                                        validate30.errors =
                                                                          vErrors;
                                                                        return false;
                                                                      } else {
                                                                        errors =
                                                                          _errs143;
                                                                        if (
                                                                          vErrors !==
                                                                          null
                                                                        ) {
                                                                          if (
                                                                            _errs143
                                                                          ) {
                                                                            vErrors.length =
                                                                              _errs143;
                                                                          } else {
                                                                            vErrors =
                                                                              null;
                                                                          }
                                                                        }
                                                                      }
                                                                      var valid30 =
                                                                        _errs142 ===
                                                                        errors;
                                                                    } else {
                                                                      var valid30 = true;
                                                                    }
                                                                  }
                                                                }
                                                              }
                                                            }
                                                          } else {
                                                            validate30.errors =
                                                              [
                                                                {
                                                                  instancePath:
                                                                    instancePath +
                                                                    "/metric_hits/" +
                                                                    i9,
                                                                  schemaPath:
                                                                    "#/$defs/MetricHitOut/type",
                                                                  keyword:
                                                                    "type",
                                                                  params: {
                                                                    type: "object",
                                                                  },
                                                                  message:
                                                                    "must be object",
                                                                },
                                                              ];
                                                            return false;
                                                          }
                                                        }
                                                        var valid28 =
                                                          _errs133 === errors;
                                                        if (!valid28) {
                                                          break;
                                                        }
                                                      }
                                                    } else {
                                                      validate30.errors = [
                                                        {
                                                          instancePath:
                                                            instancePath +
                                                            "/metric_hits",
                                                          schemaPath:
                                                            "#/properties/metric_hits/type",
                                                          keyword: "type",
                                                          params: {
                                                            type: "array",
                                                          },
                                                          message:
                                                            "must be array",
                                                        },
                                                      ];
                                                      return false;
                                                    }
                                                  }
                                                  var valid0 =
                                                    _errs131 === errors;
                                                } else {
                                                  var valid0 = true;
                                                }
                                                if (valid0) {
                                                  if (
                                                    data.sources !== undefined
                                                  ) {
                                                    let data49 = data.sources;
                                                    const _errs148 = errors;
                                                    if (errors === _errs148) {
                                                      if (
                                                        Array.isArray(data49)
                                                      ) {
                                                        var valid32 = true;
                                                        const len10 =
                                                          data49.length;
                                                        for (
                                                          let i10 = 0;
                                                          i10 < len10;
                                                          i10++
                                                        ) {
                                                          let data50 =
                                                            data49[i10];
                                                          const _errs150 =
                                                            errors;
                                                          const _errs151 =
                                                            errors;
                                                          if (
                                                            errors === _errs151
                                                          ) {
                                                            if (
                                                              data50 &&
                                                              typeof data50 ==
                                                                "object" &&
                                                              !Array.isArray(
                                                                data50,
                                                              )
                                                            ) {
                                                              let missing6;
                                                              if (
                                                                data50.citation_key ===
                                                                  undefined &&
                                                                (missing6 =
                                                                  "citation_key")
                                                              ) {
                                                                validate30.errors =
                                                                  [
                                                                    {
                                                                      instancePath:
                                                                        instancePath +
                                                                        "/sources/" +
                                                                        i10,
                                                                      schemaPath:
                                                                        "#/$defs/SourceOut/required",
                                                                      keyword:
                                                                        "required",
                                                                      params: {
                                                                        missingProperty:
                                                                          missing6,
                                                                      },
                                                                      message:
                                                                        "must have required property '" +
                                                                        missing6 +
                                                                        "'",
                                                                    },
                                                                  ];
                                                                return false;
                                                              } else {
                                                                if (
                                                                  data50.citation_key !==
                                                                  undefined
                                                                ) {
                                                                  const _errs153 =
                                                                    errors;
                                                                  if (
                                                                    typeof data50.citation_key !==
                                                                    "string"
                                                                  ) {
                                                                    validate30.errors =
                                                                      [
                                                                        {
                                                                          instancePath:
                                                                            instancePath +
                                                                            "/sources/" +
                                                                            i10 +
                                                                            "/citation_key",
                                                                          schemaPath:
                                                                            "#/$defs/SourceOut/properties/citation_key/type",
                                                                          keyword:
                                                                            "type",
                                                                          params:
                                                                            {
                                                                              type: "string",
                                                                            },
                                                                          message:
                                                                            "must be string",
                                                                        },
                                                                      ];
                                                                    return false;
                                                                  }
                                                                  var valid34 =
                                                                    _errs153 ===
                                                                    errors;
                                                                } else {
                                                                  var valid34 = true;
                                                                }
                                                                if (valid34) {
                                                                  if (
                                                                    data50.source_ref !==
                                                                    undefined
                                                                  ) {
                                                                    const _errs155 =
                                                                      errors;
                                                                    if (
                                                                      typeof data50.source_ref !==
                                                                      "string"
                                                                    ) {
                                                                      validate30.errors =
                                                                        [
                                                                          {
                                                                            instancePath:
                                                                              instancePath +
                                                                              "/sources/" +
                                                                              i10 +
                                                                              "/source_ref",
                                                                            schemaPath:
                                                                              "#/$defs/SourceOut/properties/source_ref/type",
                                                                            keyword:
                                                                              "type",
                                                                            params:
                                                                              {
                                                                                type: "string",
                                                                              },
                                                                            message:
                                                                              "must be string",
                                                                          },
                                                                        ];
                                                                      return false;
                                                                    }
                                                                    var valid34 =
                                                                      _errs155 ===
                                                                      errors;
                                                                  } else {
                                                                    var valid34 = true;
                                                                  }
                                                                  if (valid34) {
                                                                    if (
                                                                      data50.doc_name !==
                                                                      undefined
                                                                    ) {
                                                                      const _errs157 =
                                                                        errors;
                                                                      if (
                                                                        typeof data50.doc_name !==
                                                                        "string"
                                                                      ) {
                                                                        validate30.errors =
                                                                          [
                                                                            {
                                                                              instancePath:
                                                                                instancePath +
                                                                                "/sources/" +
                                                                                i10 +
                                                                                "/doc_name",
                                                                              schemaPath:
                                                                                "#/$defs/SourceOut/properties/doc_name/type",
                                                                              keyword:
                                                                                "type",
                                                                              params:
                                                                                {
                                                                                  type: "string",
                                                                                },
                                                                              message:
                                                                                "must be string",
                                                                            },
                                                                          ];
                                                                        return false;
                                                                      }
                                                                      var valid34 =
                                                                        _errs157 ===
                                                                        errors;
                                                                    } else {
                                                                      var valid34 = true;
                                                                    }
                                                                  }
                                                                }
                                                              }
                                                            } else {
                                                              validate30.errors =
                                                                [
                                                                  {
                                                                    instancePath:
                                                                      instancePath +
                                                                      "/sources/" +
                                                                      i10,
                                                                    schemaPath:
                                                                      "#/$defs/SourceOut/type",
                                                                    keyword:
                                                                      "type",
                                                                    params: {
                                                                      type: "object",
                                                                    },
                                                                    message:
                                                                      "must be object",
                                                                  },
                                                                ];
                                                              return false;
                                                            }
                                                          }
                                                          var valid32 =
                                                            _errs150 === errors;
                                                          if (!valid32) {
                                                            break;
                                                          }
                                                        }
                                                      } else {
                                                        validate30.errors = [
                                                          {
                                                            instancePath:
                                                              instancePath +
                                                              "/sources",
                                                            schemaPath:
                                                              "#/properties/sources/type",
                                                            keyword: "type",
                                                            params: {
                                                              type: "array",
                                                            },
                                                            message:
                                                              "must be array",
                                                          },
                                                        ];
                                                        return false;
                                                      }
                                                    }
                                                    var valid0 =
                                                      _errs148 === errors;
                                                  } else {
                                                    var valid0 = true;
                                                  }
                                                }
                                              }
                                            }
                                          }
                                        }
                                      }
                                    }
                                  }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    } else {
      validate30.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate30.errors = vErrors;
  return errors === 0;
}
validate30.evaluated = {
  props: {
    session_id: true,
    success: true,
    verdict: true,
    summary: true,
    insights: true,
    stats: true,
    columns: true,
    rows: true,
    sql: true,
    chart: true,
    chart_candidates: true,
    retry_count: true,
    error: true,
    error_detail: true,
    message_id: true,
    datasource_key: true,
    pending_tool_calls: true,
    artifacts: true,
    attachments: true,
    column_labels: true,
    metric_hits: true,
    sources: true,
  },
  dynamicProps: false,
  dynamicItems: false,
};
function validate29(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate29.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate29.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate29.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate29.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("result" !== data0) {
              validate29.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "result" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              const _errs4 = errors;
              if (
                !validate30(data.data, {
                  instancePath: instancePath + "/data",
                  parentData: data,
                  parentDataProperty: "data",
                  rootData,
                  dynamicAnchors,
                })
              ) {
                vErrors =
                  vErrors === null
                    ? validate30.errors
                    : vErrors.concat(validate30.errors);
                errors = vErrors.length;
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate29.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate29.errors = vErrors;
  return errors === 0;
}
validate29.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
const schema48 = {
  properties: {
    type: { type: "string", const: "error", title: "Type" },
    data: { $ref: "#/$defs/StreamErrorOut" },
  },
  additionalProperties: false,
  type: "object",
  required: ["type", "data"],
  title: "StreamErrorEvent",
  description: "type=error 信封:仅传输/输出失败。",
};
const schema49 = {
  properties: {
    error: { type: "string", title: "Error" },
    reason_code: {
      type: "string",
      enum: ["AGENT_UNAVAILABLE", "STREAM_OUTPUT_FAILED"],
      title: "Reason Code",
    },
  },
  additionalProperties: false,
  type: "object",
  required: ["error", "reason_code"],
  title: "StreamErrorOut",
  description:
    "error 终态事件 data:仅传输层/意外失败(业务三态走 result 事件)。\n\nreason_code 必填且枚举两种流错误;公开信封据此构造,内部队列不可再发\nreason_code=None 的裸错误。",
};
function validate33(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate33.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  if (errors === 0) {
    if (data && typeof data == "object" && !Array.isArray(data)) {
      let missing0;
      if (
        (data.type === undefined && (missing0 = "type")) ||
        (data.data === undefined && (missing0 = "data"))
      ) {
        validate33.errors = [
          {
            instancePath,
            schemaPath: "#/required",
            keyword: "required",
            params: { missingProperty: missing0 },
            message: "must have required property '" + missing0 + "'",
          },
        ];
        return false;
      } else {
        const _errs1 = errors;
        for (const key0 in data) {
          if (!(key0 === "type" || key0 === "data")) {
            validate33.errors = [
              {
                instancePath,
                schemaPath: "#/additionalProperties",
                keyword: "additionalProperties",
                params: { additionalProperty: key0 },
                message: "must NOT have additional properties",
              },
            ];
            return false;
            break;
          }
        }
        if (_errs1 === errors) {
          if (data.type !== undefined) {
            let data0 = data.type;
            const _errs2 = errors;
            if (typeof data0 !== "string") {
              validate33.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/type",
                  keyword: "type",
                  params: { type: "string" },
                  message: "must be string",
                },
              ];
              return false;
            }
            if ("error" !== data0) {
              validate33.errors = [
                {
                  instancePath: instancePath + "/type",
                  schemaPath: "#/properties/type/const",
                  keyword: "const",
                  params: { allowedValue: "error" },
                  message: "must be equal to constant",
                },
              ];
              return false;
            }
            var valid0 = _errs2 === errors;
          } else {
            var valid0 = true;
          }
          if (valid0) {
            if (data.data !== undefined) {
              let data1 = data.data;
              const _errs4 = errors;
              const _errs5 = errors;
              if (errors === _errs5) {
                if (
                  data1 &&
                  typeof data1 == "object" &&
                  !Array.isArray(data1)
                ) {
                  let missing1;
                  if (
                    (data1.error === undefined && (missing1 = "error")) ||
                    (data1.reason_code === undefined &&
                      (missing1 = "reason_code"))
                  ) {
                    validate33.errors = [
                      {
                        instancePath: instancePath + "/data",
                        schemaPath: "#/$defs/StreamErrorOut/required",
                        keyword: "required",
                        params: { missingProperty: missing1 },
                        message:
                          "must have required property '" + missing1 + "'",
                      },
                    ];
                    return false;
                  } else {
                    const _errs7 = errors;
                    for (const key1 in data1) {
                      if (!(key1 === "error" || key1 === "reason_code")) {
                        validate33.errors = [
                          {
                            instancePath: instancePath + "/data",
                            schemaPath:
                              "#/$defs/StreamErrorOut/additionalProperties",
                            keyword: "additionalProperties",
                            params: { additionalProperty: key1 },
                            message: "must NOT have additional properties",
                          },
                        ];
                        return false;
                        break;
                      }
                    }
                    if (_errs7 === errors) {
                      if (data1.error !== undefined) {
                        const _errs8 = errors;
                        if (typeof data1.error !== "string") {
                          validate33.errors = [
                            {
                              instancePath: instancePath + "/data/error",
                              schemaPath:
                                "#/$defs/StreamErrorOut/properties/error/type",
                              keyword: "type",
                              params: { type: "string" },
                              message: "must be string",
                            },
                          ];
                          return false;
                        }
                        var valid2 = _errs8 === errors;
                      } else {
                        var valid2 = true;
                      }
                      if (valid2) {
                        if (data1.reason_code !== undefined) {
                          let data3 = data1.reason_code;
                          const _errs10 = errors;
                          if (typeof data3 !== "string") {
                            validate33.errors = [
                              {
                                instancePath:
                                  instancePath + "/data/reason_code",
                                schemaPath:
                                  "#/$defs/StreamErrorOut/properties/reason_code/type",
                                keyword: "type",
                                params: { type: "string" },
                                message: "must be string",
                              },
                            ];
                            return false;
                          }
                          if (!(
                            data3 === "AGENT_UNAVAILABLE" ||
                            data3 === "STREAM_OUTPUT_FAILED"
                          )) {
                            validate33.errors = [
                              {
                                instancePath:
                                  instancePath + "/data/reason_code",
                                schemaPath:
                                  "#/$defs/StreamErrorOut/properties/reason_code/enum",
                                keyword: "enum",
                                params: {
                                  allowedValues:
                                    schema49.properties.reason_code.enum,
                                },
                                message:
                                  "must be equal to one of the allowed values",
                              },
                            ];
                            return false;
                          }
                          var valid2 = _errs10 === errors;
                        } else {
                          var valid2 = true;
                        }
                      }
                    }
                  }
                } else {
                  validate33.errors = [
                    {
                      instancePath: instancePath + "/data",
                      schemaPath: "#/$defs/StreamErrorOut/type",
                      keyword: "type",
                      params: { type: "object" },
                      message: "must be object",
                    },
                  ];
                  return false;
                }
              }
              var valid0 = _errs4 === errors;
            } else {
              var valid0 = true;
            }
          }
        }
      }
    } else {
      validate33.errors = [
        {
          instancePath,
          schemaPath: "#/type",
          keyword: "type",
          params: { type: "object" },
          message: "must be object",
        },
      ];
      return false;
    }
  }
  validate33.errors = vErrors;
  return errors === 0;
}
validate33.evaluated = {
  props: true,
  dynamicProps: false,
  dynamicItems: false,
};
function validate20(
  data,
  {
    instancePath = "",
    parentData,
    parentDataProperty,
    rootData = data,
    dynamicAnchors = {},
  } = {},
) {
  let vErrors = null;
  let errors = 0;
  const evaluated0 = validate20.evaluated;
  if (evaluated0.dynamicProps) {
    evaluated0.props = undefined;
  }
  if (evaluated0.dynamicItems) {
    evaluated0.items = undefined;
  }
  const _errs0 = errors;
  let valid0 = false;
  let passing0 = null;
  const _errs1 = errors;
  if (
    !validate21(data, {
      instancePath,
      parentData,
      parentDataProperty,
      rootData,
      dynamicAnchors,
    })
  ) {
    vErrors =
      vErrors === null ? validate21.errors : vErrors.concat(validate21.errors);
    errors = vErrors.length;
  }
  var _valid0 = _errs1 === errors;
  if (_valid0) {
    valid0 = true;
    passing0 = 0;
    var props0 = true;
  }
  const _errs2 = errors;
  if (
    !validate23(data, {
      instancePath,
      parentData,
      parentDataProperty,
      rootData,
      dynamicAnchors,
    })
  ) {
    vErrors =
      vErrors === null ? validate23.errors : vErrors.concat(validate23.errors);
    errors = vErrors.length;
  }
  var _valid0 = _errs2 === errors;
  if (_valid0 && valid0) {
    valid0 = false;
    passing0 = [passing0, 1];
  } else {
    if (_valid0) {
      valid0 = true;
      passing0 = 1;
      if (props0 !== true) {
        props0 = true;
      }
    }
    const _errs3 = errors;
    if (
      !validate25(data, {
        instancePath,
        parentData,
        parentDataProperty,
        rootData,
        dynamicAnchors,
      })
    ) {
      vErrors =
        vErrors === null
          ? validate25.errors
          : vErrors.concat(validate25.errors);
      errors = vErrors.length;
    }
    var _valid0 = _errs3 === errors;
    if (_valid0 && valid0) {
      valid0 = false;
      passing0 = [passing0, 2];
    } else {
      if (_valid0) {
        valid0 = true;
        passing0 = 2;
        if (props0 !== true) {
          props0 = true;
        }
      }
      const _errs4 = errors;
      if (
        !validate27(data, {
          instancePath,
          parentData,
          parentDataProperty,
          rootData,
          dynamicAnchors,
        })
      ) {
        vErrors =
          vErrors === null
            ? validate27.errors
            : vErrors.concat(validate27.errors);
        errors = vErrors.length;
      }
      var _valid0 = _errs4 === errors;
      if (_valid0 && valid0) {
        valid0 = false;
        passing0 = [passing0, 3];
      } else {
        if (_valid0) {
          valid0 = true;
          passing0 = 3;
          if (props0 !== true) {
            props0 = true;
          }
        }
        const _errs5 = errors;
        if (
          !validate29(data, {
            instancePath,
            parentData,
            parentDataProperty,
            rootData,
            dynamicAnchors,
          })
        ) {
          vErrors =
            vErrors === null
              ? validate29.errors
              : vErrors.concat(validate29.errors);
          errors = vErrors.length;
        }
        var _valid0 = _errs5 === errors;
        if (_valid0 && valid0) {
          valid0 = false;
          passing0 = [passing0, 4];
        } else {
          if (_valid0) {
            valid0 = true;
            passing0 = 4;
            if (props0 !== true) {
              props0 = true;
            }
          }
          const _errs6 = errors;
          if (
            !validate33(data, {
              instancePath,
              parentData,
              parentDataProperty,
              rootData,
              dynamicAnchors,
            })
          ) {
            vErrors =
              vErrors === null
                ? validate33.errors
                : vErrors.concat(validate33.errors);
            errors = vErrors.length;
          }
          var _valid0 = _errs6 === errors;
          if (_valid0 && valid0) {
            valid0 = false;
            passing0 = [passing0, 5];
          } else {
            if (_valid0) {
              valid0 = true;
              passing0 = 5;
              if (props0 !== true) {
                props0 = true;
              }
            }
          }
        }
      }
    }
  }
  if (!valid0) {
    const err0 = {
      instancePath,
      schemaPath: "#/oneOf",
      keyword: "oneOf",
      params: { passingSchemas: passing0 },
      message: "must match exactly one schema in oneOf",
    };
    if (vErrors === null) {
      vErrors = [err0];
    } else {
      vErrors.push(err0);
    }
    errors++;
    validate20.errors = vErrors;
    return false;
  } else {
    errors = _errs0;
    if (vErrors !== null) {
      if (_errs0) {
        vErrors.length = _errs0;
      } else {
        vErrors = null;
      }
    }
  }
  validate20.errors = vErrors;
  evaluated0.props = props0;
  return errors === 0;
}
validate20.evaluated = { dynamicProps: true, dynamicItems: false };
