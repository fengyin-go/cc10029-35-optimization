"""定时调度 时间线接口：返回作业时间序列结构。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.report import report_service
from app.services.schedule import ScheduleService
from app.services.timekit import CronError, TimezoneError

router = APIRouter(prefix="/api/schedule", tags=["定时调度"])
service = ScheduleService()
LIST_FIELDS = ["计划编号", "关联任务", "Cron表达式", "执行窗口", "重试策略", "失败通知", "最大并发", "是否启用"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


class ReportSubmitPayload(BaseModel):
    """兼容报告提交载荷；字段均可缺省，沿用既有的 values 包裹风格。"""

    target_timezone: str | None = None
    start: str | None = None
    limit: int = 10
    idempotency_key: str | None = None


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=schedule.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"调度计划 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="调度计划已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)


# ===================== 调度时间兼容数据报告（迁移新增，不改旧契约） =====================

@router.post("/compatibility-reports")
def submit_report(payload: ReportSubmitPayload) -> dict[str, Any]:
    """生成并提交兼容报告；并发/连续重复提交只保留一个成功版本。"""
    try:
        report, deduped = report_service.submit(
            target_tz=payload.target_timezone,
            start_iso=payload.start,
            limit=payload.limit,
            idempotency_key=payload.idempotency_key,
        )
    except TimezoneError as exc:
        # 沿用错误码风格：HTTP 400 + 中文 detail。
        raise HTTPException(status_code=400, detail=f"UNKNOWN_TIMEZONE: {exc}") from exc
    except CronError as exc:
        raise HTTPException(status_code=400, detail=f"INVALID_CRON: {exc}") from exc
    return {
        "ok": True,
        "deduped": deduped,
        "message": "重复提交已去重，仍返回当前生效版本" if deduped else "兼容报告已生成并生效",
        "report": report,
    }


@router.get("/compatibility-reports/active")
def get_active_report() -> dict[str, Any]:
    report = report_service.get_active()
    if report is None:
        return {"ok": True, "report": None, "message": "尚未生成兼容报告"}
    return {"ok": True, "report": report}


@router.get("/compatibility-reports/versions")
def list_report_versions() -> dict[str, Any]:
    return {"items": report_service.list_versions()}


@router.get("/compatibility-reports/history-batches")
def get_history_batches() -> dict[str, Any]:
    """历史已执行批次：按原时区固化，迁移不回写，供页面核对。"""
    from app.store import store
    return {"items": store.rows("schedule_history")}
