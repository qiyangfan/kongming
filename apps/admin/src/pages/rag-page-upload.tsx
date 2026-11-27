/* 知识库上传区:数据源/schema/归属选择与文件上传入口。 */

import type { RefObject } from "react";

import { Button } from "@kongming/ui/button";
import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@kongming/ui/tooltip";
import { CircleHelp } from "lucide-react";
import type { DatasourceOptionOut } from "@kongming/api-client/datasource-api";
import type { SchemasOut } from "@kongming/api-client/rag-api";
import type { TeamItem } from "@kongming/api-client/team-api";

type QueryLike<T> = {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
};

type RagUploadSectionProps = {
  options: DatasourceOptionOut[] | undefined;
  uploadDs: string;
  onUploadDsChange: (v: string) => void;
  uploadSchema: string;
  onUploadSchemaChange: (v: string) => void;
  schemas: QueryLike<SchemasOut>;
  uploadTeamId: string;
  onUploadTeamIdChange: (v: string) => void;
  writableTeams: TeamItem[];
  fileRef: RefObject<HTMLInputElement | null>;
  uploading: boolean;
  onUploadFile: (file: File) => void;
};

function DatasourceOptions({ options }: { options: DatasourceOptionOut[] | undefined }) {
  return (
    <>
      {(options ?? []).map((o) => (
        <SelectItem key={o.datasource_key} value={o.datasource_key}>
          {o.name}
        </SelectItem>
      ))}
    </>
  );
}

// schema 下拉占位随查询状态变化;候选=可扫描集合,与扫描/上传校验同源
function schemaPlaceholder(schemas: QueryLike<SchemasOut>): string {
  if (schemas.isError) return "候选加载失败";
  if (schemas.isPending) return "加载中…";
  return "选择 schema";
}

export function RagUploadSection(props: RagUploadSectionProps) {
  const { schemas } = props;
  return (
    <section className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center gap-1">
        <h2 className="text-sm font-medium">表说明</h2>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-5 text-muted-foreground hover:text-foreground"
              aria-label="什么是表说明"
            >
              <CircleHelp className="size-3.5" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">
            {
              "教 AI 认识数据表——字段含义、业务别名、示例问题。可上传文档补充,或用「重建索引」自动扫描生成。"
            }
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="rag-upload-ds">数据源</Label>
          <Select value={props.uploadDs} onValueChange={props.onUploadDsChange}>
            <SelectTrigger id="rag-upload-ds" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <DatasourceOptions options={props.options} />
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="rag-upload-schema">schema</Label>
          {/* schema 候选=可扫描集合,与扫描/上传校验同源 */}
          <Select
            value={props.uploadSchema}
            onValueChange={props.onUploadSchemaChange}
            disabled={schemas.isPending || schemas.isError}
          >
            <SelectTrigger id="rag-upload-schema" className="w-40">
              <SelectValue placeholder={schemaPlaceholder(schemas)} />
            </SelectTrigger>
            <SelectContent>
              {(schemas.data?.schemas ?? []).map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {schemas.isError && (
            <p className="text-xs text-destructive" role="alert">
              schema 候选加载失败,请刷新重试
            </p>
          )}
        </div>
        {/* 上传归属(默认公共;可选我作为 OWNER/MEMBER 的团队——VIEWER 只读不可选) */}
        <div className="space-y-1">
          <Label htmlFor="rag-upload-team">归属</Label>
          <Select
            value={props.uploadTeamId || "public"}
            onValueChange={(v) => props.onUploadTeamIdChange(v === "public" ? "" : v)}
          >
            <SelectTrigger id="rag-upload-team" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="public">租户公共</SelectItem>
              {props.writableTeams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="rag-upload-file" className="flex items-center gap-1">
            文件(Markdown/TXT/PDF/Word)
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 text-muted-foreground hover:text-foreground"
                  aria-label="支持的文件格式与大小限制"
                >
                  <CircleHelp className="size-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">
                支持 Markdown / TXT / PDF / Word 文档,文本类不超过 1MB,PDF 和 Word 不超过 10MB。
              </TooltipContent>
            </Tooltip>
          </Label>
          {/* 隐藏 file input 仅作 Button 触发的管道,不作为可见交互件(样式铁律豁免) */}
          <input
            ref={props.fileRef}
            id="rag-upload-file"
            type="file"
            accept=".md,.markdown,.txt,.pdf,.docx"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              // 清空 input value,确保失败后再次选择同一文件仍会触发 change。
              e.target.value = "";
              if (f) props.onUploadFile(f);
            }}
          />
          <Button
            variant="outline"
            onClick={() => props.fileRef.current?.click()}
            disabled={props.uploading || schemas.isPending || !props.uploadSchema}
          >
            {props.uploading ? "上传中…" : "选择文件并上传"}
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        上传后文档会按表格自动整理拆分;同一张表重复上传,新内容会覆盖旧内容。
      </p>
    </section>
  );
}
