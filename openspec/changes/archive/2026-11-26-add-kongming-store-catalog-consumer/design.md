# Design: 商店目录消费者（store-catalog-consumer）

## Context

现有 bot-store 从租户内上架快照派生目录；控制面将拥有目录/权益。

## Goals / Non-Goals

**Goals:**
- 目录/权益来自控制面。
- 定义/运行仍本地。

**Non-Goals:**
- 不做目录编辑。
- 不做交易。

## Decisions

1. 目录快照驱动货架；本地只存 Bot 定义/版本。
2. 运行前校验权益；缺失/非法拒绝。
3. 控制数据只读；移除本地目录编辑。

## Risks / Trade-offs

- 目录引用失效 → 显示不可用。
- 权益不一致 → 快照版本/契约测试。

## Migration Plan

greenfield：删除本地目录控制；从快照加载。
