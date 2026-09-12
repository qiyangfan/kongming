"""trigger 领域模型(031,零框架):cron 五字段解析与 next_fire 纯函数。

支持形态:*、*/n、a-b、a-b/n、单值、逗号列表;036⑦ 补 Vixie `a/b`
形态(从 a 起步进 b 至段上限);dom/dow 双非 * 直接拒(Vixie 并集
语义易误解,简化立约);dow 0/7 皆周日;时区固定 Asia/Shanghai。
036⑥:next_fire 改日历字段跳跃(月→日→时→分逐级推进),计算耗时
与表达式跨度无关——原逐分钟迭代对远未来表达式(如每年一次)是
52 万次迭代 0.3-0.5s 的请求路径阻塞。
"""

import datetime as dt

_CRON_BOUNDS = [
    (0, 59),  # 分
    (0, 23),  # 时
    (1, 31),  # 日
    (1, 12),  # 月
    (0, 7),  # 周(0/7=周日)
]


class CronError(Exception):
    """cron 表达式非法(保存期 400 语义)。"""


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"字段片段为空: {field}")
        step = 1
        has_step = "/" in part
        if has_step:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"步长非法: {part}")
            step = int(step_s)
            part = base
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise CronError(f"范围非法: {part}")
            start, end = int(a_s), int(b_s)
            if start > end:
                raise CronError(f"范围倒置: {part}")
        elif part.isdigit():
            start = int(part)
            # 036⑦:Vixie `a/b`=从 a 起步进 b 至段上限(原单值形态
            # 无步长时 start=end 不变)
            end = hi if has_step else start
        else:
            raise CronError(f"片段非法: {part}")
        if start < lo or start > hi or end > hi:
            raise CronError(f"越界 [{lo},{hi}]: {part}")
        out.update(range(start, end + 1, step))
    return out


def parse_cron(expr: str) -> list[set[int]]:
    """五字段解析;非法抛 CronError。返回 [分,时,日,月,周] 集合表。"""
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"须为五字段(分 时 日 月 周),当前 {len(fields)} 段")
    if fields[2] != "*" and fields[4] != "*":
        raise CronError("日与周仅支持其一为 *(双指定语义易误解,不立约)")
    parsed = [_parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _CRON_BOUNDS, strict=True)]
    # dow 7=周日归一为 0(_dow 输出域 0-6)
    if 7 in parsed[4]:
        parsed[4].add(0)
        parsed[4].discard(7)
    return parsed


def _dow(dt_: dt.datetime) -> int:
    # Python weekday():周一=0..周日=6;cron:周日=0/7,周一=1
    return (dt_.weekday() + 1) % 7


def _ceil(s: set[int], cur: int) -> int | None:
    """集合中 ≥cur 的最小值(无则 None=需进位)。"""
    for v in sorted(s):
        if v >= cur:
            return v
    return None


def next_fire(expr: str, after: dt.datetime) -> dt.datetime:
    """下一次触发时刻(036⑥:日历字段跳跃,耗时与跨度无关;
    日级上界 4 年防死循环;after 须带时区)。"""
    minute, hour, dom, month, dow = parse_cron(expr)
    if after.tzinfo is None:
        raise CronError("after 须带时区")
    cur = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(366 * 4):
        if cur.month not in month:
            nxt = _ceil(month, cur.month)
            if nxt is not None:
                cur = cur.replace(month=nxt, day=1, hour=0, minute=0)
            else:  # 回绕到次年最小月
                cur = cur.replace(year=cur.year + 1, month=min(month), day=1, hour=0, minute=0)
            continue
        if not (cur.day in dom and _dow(cur) in dow):
            cur = (cur + dt.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if cur.hour not in hour:
            nxt = _ceil(hour, cur.hour)
            if nxt is not None:
                cur = cur.replace(hour=nxt, minute=0)
            else:
                cur = (cur + dt.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if cur.minute not in minute:
            nxt = _ceil(minute, cur.minute)
            if nxt is not None:
                cur = cur.replace(minute=nxt)
            else:
                cur = (cur + dt.timedelta(hours=1)).replace(minute=0)
            continue
        return cur
    raise CronError("4 年内无触发时刻")
