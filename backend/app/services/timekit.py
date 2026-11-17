"""时区与 Cron 工具：调度时间口径迁移的底层依据。

设计要点（对应迁移需求）：
- 全程使用 IANA 时区名（``Asia/Shanghai`` 等），不再依赖服务器本地时间；
- ``croniter`` 负责在指定时区上产出 Cron 时间序列；
- ``zoneinfo`` 负责识别夏令时：
  - 春跳（gap）：某个墙钟时间在目标时区根本不存在，该执行窗口必须跳过，不能下发；
  - 秋退（fold）：同一个墙钟时间出现两个瞬时点，读取端只保留一个带时区依据的批次。
- 所有对外的时间字符串都带 UTC 偏移，确保「带时区依据」可被下游核对。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter

UTC = timezone.utc


class TimezoneError(ValueError):
    """时区名无法解析。"""


class CronError(ValueError):
    """Cron 表达式非法。"""


def get_zone(name: str) -> ZoneInfo:
    """按 IANA 名取时区，非法名抛 :class:`TimezoneError`。"""
    if not name or not str(name).strip():
        raise TimezoneError("时区名为空，缺少时区依据")
    try:
        return ZoneInfo(str(name).strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - 防御
        raise TimezoneError(f"无法识别的时区：{name}") from exc


def validate_cron(expr: str) -> str:
    """校验标准 5 段 Cron，合法则原样返回，非法抛 :class:`CronError`。"""
    text = (expr or "").strip()
    if not text:
        raise CronError("Cron 表达式为空")
    try:
        if not croniter.is_valid(text):
            raise CroniterBadCronError("croniter 判定为非法")
    except (CroniterBadCronError, CroniterBadDateError) as exc:
        raise CronError(f"非法 Cron 表达式「{expr}」：{exc}") from exc
    if len(text.split()) != 5:
        raise CronError(f"仅支持标准 5 段 Cron（分 时 日 月 周），收到「{expr}」")
    return text


@dataclass(frozen=True)
class CronEvent:
    """一条带时区依据的执行窗口。"""

    wall_time: str          # 目标时区墙钟时间（含偏移），例如 2026-11-01T01:30:00-04:00
    utc_time: str           # 同一瞬时的 UTC 时间，例如 2026-11-01T05:30:00+00:00
    offset_minutes: int     # 该瞬时相对 UTC 的偏移分钟（夏令时会变化）
    dst: bool               # 该瞬时是否处于夏令时
    fold_occurrence: int    # 秋退歧义时：1=首个瞬时，2=被合并的第二个瞬时
    note: str               # 备注：normal / dst-gap-skip / dst-fold-merged


def is_gap(wall: datetime) -> bool:
    """春跳缺口：把墙钟时间转 UTC 再转回，若对不上说明该墙钟时间不存在。"""
    tz = wall.tzinfo
    roundtrip = wall.astimezone(UTC).astimezone(tz)
    return roundtrip.replace(tzinfo=None) != wall.replace(tzinfo=None)


def is_fold_ambiguous(wall_naive: datetime, tz: ZoneInfo) -> bool:
    """秋退歧义：fold=0 与 fold=1 解析出不同偏移，即同一墙钟出现两次。"""
    first = wall_naive.replace(tzinfo=tz, fold=0)
    second = wall_naive.replace(tzinfo=tz, fold=1)
    return first.utcoffset() != second.utcoffset()


def _to_event(wall: datetime, occurrence: int, note: str) -> CronEvent:
    utc_dt = wall.astimezone(UTC)
    offset = wall.utcoffset() or timedelta(0)
    return CronEvent(
        wall_time=wall.isoformat(),
        utc_time=utc_dt.isoformat(),
        offset_minutes=int(offset.total_seconds() // 60),
        dst=bool(wall.dst() and wall.dst() != timedelta(0)),
        fold_occurrence=occurrence,
        note=note,
    )


def _naive_wall_series(expr: str, start_naive: datetime, count: int) -> list[datetime]:
    """用 UTC 上的 croniter 产出均匀间隔的「朴素墙钟时间」序列。

    不让 croniter 接触目标时区，避免它在春跳日自动把缺口时间挪后；gap/fold 的
    判定全部交给 zoneinfo，保证口径单一、可解释。
    """
    base = start_naive.replace(tzinfo=UTC)
    itr = croniter(expr, base)
    return [itr.get_next(datetime).replace(tzinfo=None) for _ in range(count)]


def iter_cron_events(
    expr: str,
    tz_name: str,
    start: datetime,
    limit: int,
) -> list[CronEvent]:
    """在指定时区上展开 Cron 执行窗口，并处理夏令时冲突。

    - 春跳缺口（墙钟时间在目标时区不存在）的窗口直接跳过，不计入返回清单；
    - 秋退重复的墙钟只保留 fold=0 的第一个瞬时（``note=dst-fold-merged``）；
    - 因此返回值始终是「一个带时区依据的完整调度批次清单」，不会有重复批次。
    """
    validate_cron(expr)
    tz = get_zone(tz_name)
    start_naive = start.replace(tzinfo=None) if start.tzinfo is None \
        else start.astimezone(tz).replace(tzinfo=None)

    events: list[CronEvent] = []
    skipped_gaps: list[str] = []
    # 多取一些以补偿被 gap 跳过的窗口，避免 limit 不足导致清单缺项。
    for wall_naive in _naive_wall_series(expr, start_naive, limit * 4 + 10):
        if len(events) >= limit:
            break
        probe = wall_naive.replace(tzinfo=tz, fold=0)
        if is_gap(probe):
            skipped_gaps.append(wall_naive.isoformat())
            continue
        if is_fold_ambiguous(wall_naive, tz):
            # 秋退歧义墙钟：该墙钟在本地会出现两次，只保留 fold=0 的第一个瞬时，
            # 读取端因此始终只有一个带时区依据的批次。
            events.append(_to_event(probe, occurrence=1, note="dst-fold-merged"))
            continue
        events.append(_to_event(probe, occurrence=1, note="normal"))
    return events


def gap_summary(expr: str, tz_name: str, start: datetime, limit: int) -> list[dict[str, str]]:
    """统计展开区间内因春跳缺口被跳过的墙钟时间，供页面提示冲突。"""
    validate_cron(expr)
    tz = get_zone(tz_name)
    start_naive = start.replace(tzinfo=None) if start.tzinfo is None \
        else start.astimezone(tz).replace(tzinfo=None)
    gaps: list[dict[str, str]] = []
    for wall_naive in _naive_wall_series(expr, start_naive, limit * 4 + 10):
        if len(gaps) >= limit:
            break
        if is_gap(wall_naive.replace(tzinfo=tz, fold=0)):
            gaps.append({
                "wall_time": wall_naive.isoformat(),
                "timezone": tz_name,
                "reason": "spring-forward-gap",
            })
    return gaps


def convert_instant(dt: datetime, target_tz_name: str) -> datetime:
    """把一个带时区的瞬时换算到目标时区（瞬时不变，仅换表示）。"""
    if dt.tzinfo is None:
        raise ValueError("待换算时间必须带时区信息")
    return dt.astimezone(get_zone(target_tz_name))


def conversion_sample(expr: str, source_tz_name: str, target_tz_name: str) -> dict[str, object]:
    """给出下一次触发在源时区与目标时区的对照，用于迁移报告记录换算依据。

    不重写 Cron：原表达式始终是权威口径，跨时区只做「瞬时换算」，
    夏令时由 zoneinfo 在换算时自动处理，避免把每日任务误改成每年任务。
    """
    validate_cron(expr)
    src_tz = get_zone(source_tz_name)
    tgt_tz = get_zone(target_tz_name)
    naive = _naive_wall_series(expr, datetime(2026, 1, 1, 0, 0), count=1)[0]
    src_event = naive.replace(tzinfo=src_tz)
    tgt_event = src_event.astimezone(tgt_tz)
    return {
        "source_sample": src_event.isoformat(),
        "target_sample": tgt_event.isoformat(),
        "source_offset_minutes": int((src_event.utcoffset() or timedelta(0)).total_seconds() // 60),
        "target_offset_minutes": int((tgt_event.utcoffset() or timedelta(0)).total_seconds() // 60),
    }


def convert_events(events: list[CronEvent], target_tz_name: str) -> list[dict[str, object]]:
    """把源时区展开出的执行窗口换算到目标时区，供爬虫引擎下发使用。

    输出同时保留源时区墙钟与目标时区墙钟，两侧都带 UTC 偏移，即「带时区依据」。
    输入若含秋退合并标记则原样透传，保证下游只看到一个批次。
    """
    tgt_tz = get_zone(target_tz_name)
    converted: list[dict[str, object]] = []
    for event in events:
        instant = datetime.fromisoformat(event.utc_time)
        target_wall = instant.astimezone(tgt_tz)
        converted.append({
            "source_wall_time": event.wall_time,
            "target_wall_time": target_wall.isoformat(),
            "utc_time": event.utc_time,
            "target_offset_minutes": int((target_wall.utcoffset() or timedelta(0)).total_seconds() // 60),
            "dst": event.dst,
            "note": event.note,
        })
    return converted
