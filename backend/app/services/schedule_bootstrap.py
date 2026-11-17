"""调度存量数据引导：给示例计划补全 Cron 与时区口径。

- 存量计划逐条补「原时区」和真实可解析的 Cron，作为迁移前的基线；
- 额外构造一条缺少时区依据的计划，用于验证「缺少时区依据不能进入自动执行」；
- 历史已执行批次单独建表，固化其原时区解释口径，迁移不得改写。

只在内存仓库初始化时调用一次，不改动其它模块的示例数据。
"""
from __future__ import annotations

from typing import Any

# 存量计划的迁移基线：计划编号 -> (真实 Cron, 原时区)
# SCHE-0002 选 01:30：纽约秋退日（11/01）该墙钟出现两次，春跳日（03/08）01:30 仍存在，
# 便于在同一条计划上演示夏令时歧义合并；春跳缺口由报告的 gap 统计单独覆盖。
PLAN_BASELINE: dict[str, tuple[str, str]] = {
    "SCHE-0001": ("0 9 * * *", "Asia/Shanghai"),
    "SCHE-0002": ("30 1 * * *", "America/New_York"),
    "SCHE-0003": ("0 18 * * 1-5", "Europe/London"),
}

# 一条故意缺少时区依据的计划：Cron 在，但原时区缺失，必须被拦在自动执行之外。
MISSING_TZ_PLAN = {
    "id": 4,
    "status": "待校验",
    "pending": True,
    "abnormal": True,
    "计划编号": "SCHE-0004",
    "关联任务": "定时调度样例4",
    "Cron表达式": "0 10 * * *",
    "执行窗口": "定时调度样例4",
    "重试策略": "定时调度样例4",
    "失败通知": "定时调度样例4",
    "最大并发": "定时调度样例4",
    "是否启用": "定时调度样例4",
    "source_timezone": "",
}

# 春跳缺口演示计划：纽约 02:30 在 2026-03-08 春跳日不存在，用于验证缺口窗口被跳过。
GAP_DEMO_PLAN = {
    "id": 5,
    "status": "已启用",
    "pending": False,
    "abnormal": False,
    "计划编号": "SCHE-0005",
    "关联任务": "春跳缺口演示任务",
    "Cron表达式": "30 2 * * *",
    "执行窗口": "每日 02:30",
    "重试策略": "缺口跳过",
    "失败通知": "定时调度样例5",
    "最大并发": "1",
    "是否启用": "是",
    "source_timezone": "America/New_York",
    "timezone_basis": "legacy-local",
}

# 历史已执行批次：固化在原时区，迁移只影响尚未生成的窗口，不回写这些记录。
HISTORY_BATCHES = [
    {
        "id": 1,
        "plan_code": "SCHE-0001",
        "batch_no": "HIST-20260901-0930",
        "cron": "0 9 * * *",
        "source_timezone": "Asia/Shanghai",
        "executed_at": "2026-09-01T09:30:00+08:00",
        "note": "历史批次，按原时区 Asia/Shanghai 解释，迁移不回写",
    },
    {
        "id": 2,
        "plan_code": "SCHE-0002",
        "batch_no": "HIST-20261015-0230",
        "cron": "30 2 * * *",
        "source_timezone": "America/New_York",
        "executed_at": "2026-10-15T02:30:00-04:00",
        "note": "历史批次，按原时区 America/New_York 解释，迁移不回写",
    },
]


def enrich_schedule_data(tables: dict[str, list[dict[str, Any]]]) -> None:
    """在内存仓库上补全调度时区口径与历史批次。"""
    schedules = tables.setdefault("schedule", [])
    for row in schedules:
        code = str(row.get("计划编号", ""))
        baseline = PLAN_BASELINE.get(code)
        if baseline is None:
            continue
        cron_expr, source_tz = baseline
        row["Cron表达式"] = cron_expr
        row["source_timezone"] = source_tz
        row["timezone_basis"] = "legacy-local"

    if not any(str(r.get("计划编号")) == "SCHE-0004" for r in schedules):
        schedules.append(dict(MISSING_TZ_PLAN))
    if not any(str(r.get("计划编号")) == "SCHE-0005" for r in schedules):
        schedules.append(dict(GAP_DEMO_PLAN))

    tables["schedule_history"] = [dict(batch) for batch in HISTORY_BATCHES]
