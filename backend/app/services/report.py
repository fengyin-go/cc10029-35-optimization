"""调度时间兼容数据报告服务。

口径要点（对应迁移需求）：
- 每条计划的报告项必须同时具备「原时区 source_timezone / 目标时区 target_timezone /
  Cron 表达式 cron」三要素，缺一即 blocked，不能进入自动执行；
- 历史已执行批次按原时区固化在 schedule_history，报告只透传、不回写，迁移仅影响
  尚未生成的执行窗口；
- 报告带版本号并用线程锁串行化提交：同一幂等键（或相同内容）的并发/连续两次提交
  只保留一个成功版本，旧版本标记 superseded，爬虫引擎始终只读取 active 版本；
- 报告项保留调度计划原有字段（计划编号、关联任务等），不改变既有调用契约。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Any

from app.config import settings
from app.services.timekit import (
    CronError,
    TimezoneError,
    conversion_sample,
    convert_events,
    get_zone,
    iter_cron_events,
    gap_summary,
    validate_cron,
)
from app.store import store

MODULE = "schedule"
HISTORY_MODULE = "schedule_history"

# 准入阻断错误码：保持稳定字符串，供前端与下游按码处理。
ERR_MISSING_TZ = "MISSING_SOURCE_TIMEZONE"
ERR_INVALID_CRON = "INVALID_CRON"
ERR_UNKNOWN_TZ = "UNKNOWN_TIMEZONE"


class CompatibilityReportService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._versions: list[dict[str, Any]] = []
        self._idempotency: dict[str, str] = {}  # key -> report_id

    # ---------- 内部：单条计划的准入判定与窗口展开 ----------
    def _build_entry(
        self,
        row: dict[str, Any],
        target_tz: str,
        start: datetime,
        limit: int,
    ) -> dict[str, Any]:
        plan_code = str(row.get("计划编号", ""))
        cron_expr = str(row.get("Cron表达式", "")).strip()
        source_tz = str(row.get("source_timezone", "")).strip()

        entry: dict[str, Any] = {
            "plan_id": int(row.get("id", 0)),
            "plan_code": plan_code,
            "task": row.get("关联任务", ""),
            # 兼容报告三要素：原时区、目标时区、Cron 表达式。
            "source_timezone": source_tz,
            "target_timezone": target_tz,
            "cron": cron_expr,
            "timezone_basis": row.get("timezone_basis", "manual"),
            "eligible": False,
            "block_reason": None,
            "error_code": None,
            "conversion": None,
            "upcoming": [],
            "gaps": [],
            "fold_merged": 0,
            "gap_skipped": 0,
            # 保留调度计划原始字段，沿用既有契约。
            "raw": {
                "计划编号": row.get("计划编号"),
                "关联任务": row.get("关联任务"),
                "Cron表达式": row.get("Cron表达式"),
                "执行窗口": row.get("执行窗口"),
                "status": row.get("status"),
            },
        }

        # —— 准入校验：三要素缺一不可 ——
        if not source_tz:
            entry["block_reason"] = "缺少原时区依据，不能进入自动执行"
            entry["error_code"] = ERR_MISSING_TZ
            return entry
        try:
            get_zone(source_tz)
        except TimezoneError:
            entry["block_reason"] = f"原时区 {source_tz} 无法识别"
            entry["error_code"] = ERR_UNKNOWN_TZ
            return entry
        try:
            validate_cron(cron_expr)
        except CronError as exc:
            entry["block_reason"] = str(exc)
            entry["error_code"] = ERR_INVALID_CRON
            return entry

        # —— 准入通过：在原时区展开窗口，再换算到目标时区 ——
        entry["eligible"] = True
        entry["conversion"] = conversion_sample(cron_expr, source_tz, target_tz)
        events = iter_cron_events(cron_expr, source_tz, start, limit)
        entry["upcoming"] = convert_events(events, target_tz)
        entry["fold_merged"] = sum(1 for e in events if e.note == "dst-fold-merged")
        gaps = gap_summary(cron_expr, source_tz, start, limit)
        entry["gaps"] = gaps
        entry["gap_skipped"] = len(gaps)
        return entry

    def _build_report_locked(
        self,
        target_tz: str,
        start: datetime,
        limit: int,
    ) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entries = [self._build_entry(row, target_tz, start, limit) for row in rows]
        eligible = [e for e in entries if e["eligible"]]
        blocked = [
            {"plan_code": e["plan_code"], "reason": e["block_reason"], "error_code": e["error_code"]}
            for e in entries if not e["eligible"]
        ]
        history = [dict(batch) for batch in store.rows(HISTORY_MODULE)]
        version = len(self._versions) + 1
        report = {
            "report_id": f"SCR-{version:04d}",
            "version": version,
            "status": "active",
            "target_timezone": target_tz,
            "generated_at": datetime.now().astimezone().isoformat(),
            "migration_scope": "仅影响尚未生成的执行窗口；历史批次仍按原时区解释",
            "summary": {
                "total": len(entries),
                "eligible": len(eligible),
                "blocked": len(blocked),
                "fold_merged": sum(e["fold_merged"] for e in entries),
                "gap_skipped": sum(e["gap_skipped"] for e in entries),
                "history_batches": len(history),
            },
            "entries": entries,
            "blocked": blocked,
            "history_batches": history,
        }
        return report

    # ---------- 对外：提交（并发互斥 + 幂等去重） ----------
    def submit(
        self,
        target_tz: str | None = None,
        start_iso: str | None = None,
        limit: int = 10,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """提交一份兼容报告，返回 (报告, 是否为被去重的重复提交)。

        并发或连续两次相同提交只保留一个成功版本：第二次直接返回已存在版本，
        并把 deduped 置 True，不再产生新版本、不产生第二份下游结果。
        """
        tz_name = (target_tz or settings.target_timezone).strip()
        get_zone(tz_name)  # 目标时区非法直接抛错，由路由转 400
        start = self._parse_start(start_iso)
        limit = max(1, min(int(limit), 100))

        content_key = self._content_key(tz_name, start, limit)
        dedup_key = (idempotency_key or content_key).strip()

        with self._lock:
            existing_id = self._idempotency.get(dedup_key)
            if existing_id:
                existing = next(r for r in self._versions if r["report_id"] == existing_id)
                return existing, True

            report = self._build_report_locked(tz_name, start, limit)
            # 旧 active 版本降级为 superseded，只保留一个 active。
            for prior in self._versions:
                if prior["status"] == "active":
                    prior["status"] = "superseded"
            self._versions.append(report)
            self._idempotency[dedup_key] = report["report_id"]
            return report, False

    def get_active(self) -> dict[str, Any] | None:
        with self._lock:
            return next((r for r in reversed(self._versions) if r["status"] == "active"), None)

    def list_versions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "report_id": r["report_id"],
                    "version": r["version"],
                    "status": r["status"],
                    "target_timezone": r["target_timezone"],
                    "generated_at": r["generated_at"],
                    "summary": r["summary"],
                }
                for r in self._versions
            ]

    @staticmethod
    def _parse_start(start_iso: str | None) -> datetime:
        if not start_iso:
            return datetime.now().astimezone()
        try:
            parsed = datetime.fromisoformat(start_iso)
        except ValueError as exc:
            raise CronError(f"起始时间格式非法：{start_iso}") from exc
        return parsed if parsed.tzinfo else parsed.astimezone()

    @staticmethod
    def _content_key(tz_name: str, start: datetime, limit: int) -> str:
        raw = f"{tz_name}|{start.astimezone().isoformat()}|{limit}"
        return "auto-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


report_service = CompatibilityReportService()
