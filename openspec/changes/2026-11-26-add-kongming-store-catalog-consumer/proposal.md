# Proposal: 2026-11-26-add-kongming-store-catalog-consumer（商店目录消费者（store-catalog-consumer））

## Why

商店目录/权益改由控制面拥有；kongming 保留 Bot 定义、版本与运行，但货架与权益必须消费控制面目录。

## What Changes

- 消费控制面目录/权益/可见性快照。
- 驱动货架展示与运行前权益校验。
- 移除本地目录控制。

## Non-goals

- 不拥有目录/权益。
- 不改变 Bot 定义/版本模型。
- 不做跨租户推荐。

## Capabilities

### New Capabilities

- `store-catalog-consumer`：控制面商店目录/权益的本地消费

### Modified Capabilities

无

## Impact

store/bot 读侧；货架/运行前校验；移除本地目录控制；不写控制面。
