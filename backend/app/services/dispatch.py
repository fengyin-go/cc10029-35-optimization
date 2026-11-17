"""爬虫引擎待下发清单：只从调度时间兼容数据报告的 active 版本取数。

- 下发时间一律先换算到目标时区（报告里每条 upcoming 已带 target_wall_time）；
- 缺少时区依据、被准入拦截的计划不会出现在清单里；
- 夏令时冲突（春跳缺口/秋退重复）在报告生成阶段已处理，这里只透传一个完整清单；
- 提供与报告的差异核对，顺着数据流能定位「报告有/清单无」的计划及原因。
"""
from __future__ import annotations

from typing import Any

from app.services.report import report_service


class DispatchService:
    def get_dispatch(self) -> dict[str, Any]:
        report = report_service.get_active()
        if report is None:
            return {
                "report_status": "absent",
                "message": "尚无生效的调度时间兼容数据报告，待下发清单为空",
                "target_timezone": None,
                "active_report_id": None,
                "items": [],
                "total": 0,
            }

        items: list[dict[str, Any]] = []
        for entry in report["entries"]:
            if not entry["eligible"]:
                continue  # 缺少时区依据等被拦截计划不进入自动下发
            for window in entry["upcoming"]:
                items.append({
                    "plan_code": entry["plan_code"],
                    "task": entry["task"],
                    "cron": entry["cron"],
                    "source_timezone": entry["source_timezone"],
                    "target_timezone": entry["target_timezone"],
                    # 下发时间：目标时区墙钟（已完成换算），并保留 UTC 与源时区依据。
                    "dispatch_time": window["target_wall_time"],
                    "source_wall_time": window["source_wall_time"],
                    "utc_time": window["utc_time"],
                    "dst": window["dst"],
                    "note": window["note"],
                    "report_id": report["report_id"],
                })

        return {
            "report_status": "active",
            "message": "已按 active 报告生成待下发清单",
            "target_timezone": report["target_timezone"],
            "active_report_id": report["report_id"],
            "items": items,
            "total": len(items),
        }

    def reconcile(self) -> dict[str, Any]:
        """差异核对：报告计划项 vs 实际进入待下发清单的项。"""
        report = report_service.get_active()
        if report is None:
            return {"report_status": "absent", "differences": [], "blocked": []}

        dispatch = self.get_dispatch()
        dispatched_codes = {item["plan_code"] for item in dispatch["items"]}
        report_codes = {entry["plan_code"] for entry in report["entries"]}

        differences = []
        for entry in report["entries"]:
            in_dispatch = entry["plan_code"] in dispatched_codes
            if entry["eligible"] and not in_dispatch:
                differences.append({
                    "plan_code": entry["plan_code"],
                    "type": "eligible_but_not_dispatched",
                    "detail": "报告准入通过却未进入下发清单（理论不应出现）",
                })
            if not entry["eligible"] and in_dispatch:
                differences.append({
                    "plan_code": entry["plan_code"],
                    "type": "blocked_but_dispatched",
                    "detail": "被拦截计划却进入下发清单（数据不一致）",
                })

        return {
            "report_status": "active",
            "active_report_id": report["report_id"],
            "report_plan_codes": sorted(report_codes),
            "dispatched_plan_codes": sorted(dispatched_codes),
            "differences": differences,
            "blocked": report["blocked"],
            "summary": report["summary"],
        }


dispatch_service = DispatchService()
