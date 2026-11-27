/* 提问附件：选择图片即上传，维护 chips 列表与上传中状态。 */

import { useRef, useState } from "react";
import type React from "react";

import { uploadAttachment } from "@kongming/api-client/chat-api";

export interface AttachmentChip {
  id: string;
  filename: string;
}

interface AttachmentsApi {
  attachments: AttachmentChip[];
  setAttachments: React.Dispatch<React.SetStateAction<AttachmentChip[]>>;
  uploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onPickFile: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
}

/** 附件选择与上传；上限 3 张，失败经 onError 呈现。 */
export function useChatAttachments(onError: (message: string | null) => void): AttachmentsApi {
  const [attachments, setAttachments] = useState<AttachmentChip[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同文件可重选
    if (!file) return;
    if (attachments.length >= 3) {
      onError("一次最多引用 3 张图片。");
      return;
    }
    setUploading(true);
    onError(null);
    try {
      const out = await uploadAttachment(file);
      setAttachments((prev) => [...prev, { id: out.attachment_id, filename: out.filename }]);
    } catch (err) {
      onError(err instanceof Error ? err.message : "附件上传未成,请稍后再试。");
    } finally {
      setUploading(false);
    }
  }

  return { attachments, setAttachments, uploading, fileRef, onPickFile };
}
