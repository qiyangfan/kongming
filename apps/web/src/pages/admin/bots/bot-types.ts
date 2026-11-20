/* 助手页列表行类型单源:页面与列定义/状态 hook 共用,避免子模块反向依赖页面。 */

import type { components } from "@/lib/api-types.gen";

export type BotItem = components["schemas"]["BotOut"];
