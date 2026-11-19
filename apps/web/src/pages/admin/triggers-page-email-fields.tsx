/* 触发器表单的邮件与 webhook 形态区块。 */

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { FormRow } from "@/components/form-row";
import type { FormState } from "./triggers-page-model";

type OnChange = (form: FormState) => void;

export function EmailFields({ form, onChange }: { form: FormState; onChange: OnChange }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="IMAP 主机" htmlFor="tg-imap-host">
          <Input
            id="tg-imap-host"
            value={form.imapHost}
            onChange={(e) => onChange({ ...form, imapHost: e.target.value })}
            className="font-mono"
            placeholder="imap.example.com"
          />
        </FormRow>
        <FormRow label="端口" htmlFor="tg-imap-port">
          <Input
            id="tg-imap-port"
            value={form.imapPort}
            onChange={(e) => onChange({ ...form, imapPort: e.target.value })}
            className="w-28 font-mono"
            inputMode="numeric"
          />
        </FormRow>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="账号" htmlFor="tg-imap-user">
          <Input
            id="tg-imap-user"
            value={form.imapUser}
            onChange={(e) => onChange({ ...form, imapUser: e.target.value })}
            className="font-mono"
            placeholder="ops@example.com"
          />
        </FormRow>
        <FormRow
          label={form.id === null ? "凭证(密码)" : "凭证(留空保持现值)"}
          htmlFor="tg-imap-password"
        >
          {/* 凭证任何接口不回显(webhook secret 同口径);编辑空框=保持现值 */}
          <Input
            id="tg-imap-password"
            value={form.imapPassword}
            onChange={(e) => onChange({ ...form, imapPassword: e.target.value })}
            className="font-mono"
            type="password"
          />
        </FormRow>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="文件夹" htmlFor="tg-imap-folder">
          <Input
            id="tg-imap-folder"
            value={form.imapFolder}
            onChange={(e) => onChange({ ...form, imapFolder: e.target.value })}
            className="font-mono"
            placeholder="INBOX"
          />
        </FormRow>
        <FormRow label="轮询间隔(秒)" htmlFor="tg-imap-interval">
          <Input
            id="tg-imap-interval"
            value={form.imapPollInterval}
            onChange={(e) => onChange({ ...form, imapPollInterval: e.target.value })}
            className="w-32 font-mono"
            inputMode="numeric"
          />
          <p className="text-xs text-muted-foreground">到期即拉取新邮件;低于 30 秒按 30 秒封顶。</p>
        </FormRow>
      </div>
      <FormRow label="TLS 加密连接" htmlFor="tg-imap-tls">
        <Switch
          id="tg-imap-tls"
          checked={form.imapTls}
          onCheckedChange={(checked) => onChange({ ...form, imapTls: checked })}
        />
        <p className="text-xs text-muted-foreground">
          默认开启(993 端口);关闭则走明文 143 端口,仅限内网自建服务。
        </p>
      </FormRow>
      <p className="text-xs text-muted-foreground">
        新邮件经标准化摘要(发件人/主题/日期/正文预览,过安全护栏)触发目标; 已处理邮件按 UID
        游标续传,停用期间的邮件不丢失。
      </p>
    </div>
  );
}

export function WebhookFields({ form, onChange }: { form: FormState; onChange: OnChange }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <FormRow label="签名密钥(留空自动生成)" htmlFor="tg-secret">
        <Input
          id="tg-secret"
          value={form.secret}
          onChange={(e) => onChange({ ...form, secret: e.target.value })}
          className="font-mono"
          type="password"
        />
      </FormRow>
      <FormRow label="请求数据格式">
        <Select
          value={form.adapter}
          onValueChange={(v) => onChange({ ...form, adapter: v as "custom" | "github" })}
        >
          <SelectTrigger aria-label="请求数据格式">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="custom">自定义(手写 JSON)</SelectItem>
            <SelectItem value="github">GitHub(Webhook 载荷)</SelectItem>
          </SelectContent>
        </Select>
      </FormRow>
    </div>
  );
}
