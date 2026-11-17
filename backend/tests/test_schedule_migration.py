"""调度时间口径迁移的接口级验收测试。

覆盖：
1. 既有调用契约不破坏（时间线/导出/登记/动作仍按原字段与错误码）；
2. 兼容报告必须同时含原时区、目标时区、Cron，缺时区依据的计划被拦截、不自动执行；
3. 历史批次按原时区固化，迁移只影响未生成窗口；
4. 并发/连续重复提交只保留一个成功版本，爬虫引擎只读该 active 版本；
5. 爬虫待下发清单的下发时间已换算到目标时区；
6. 夏令时：春跳缺口跳过、秋退歧义只保留一个批次；
7. 旧版/新版报告给爬虫引擎带来不同下游结果。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_legacy_contract_unchanged() -> None:
    # 旧时间线接口结构不变
    res = client.get("/api/schedule")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 3
    assert {"id", "name", "start", "end", "status", "logs"} <= set(items[0])

    # 旧爬虫时间线接口结构不变
    craw = client.get("/api/crawler").json()["items"]
    assert len(craw) == 3

    # 旧登记契约仍可用，缺必填返回 ok=False（沿用原错误契约，不抛 5xx）
    res = client.post("/api/schedule", json={"values": {"计划编号": ""}})
    assert res.json()["ok"] is False
    assert "缺少必填字段" in res.json()["message"]


def test_report_requires_timezone_basis_and_blocks_missing() -> None:
    res = client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC",
        "start": "2026-01-05T00:00:00+00:00",
        "limit": 5,
        "idempotency_key": "contract-1",
    })
    assert res.status_code == 200
    report = res.json()["report"]
    # 三要素齐备
    for entry in report["entries"]:
        assert "source_timezone" in entry
        assert "target_timezone" in entry
        assert "cron" in entry
    # 缺时区依据的 SCHE-0004 被拦截
    blocked_codes = {b["plan_code"] for b in report["blocked"]}
    assert "SCHE-0004" in blocked_codes
    blocked = next(b for b in report["blocked"] if b["plan_code"] == "SCHE-0004")
    assert blocked["error_code"] == "MISSING_SOURCE_TIMEZONE"
    # 被拦截计划没有任何待执行窗口
    blocked_entry = next(e for e in report["entries"] if e["plan_code"] == "SCHE-0004")
    assert blocked_entry["eligible"] is False
    assert blocked_entry["upcoming"] == []


def test_blocked_plan_never_enters_dispatch() -> None:
    client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC",
        "start": "2026-01-05T00:00:00+00:00",
        "limit": 5,
        "idempotency_key": "dispatch-1",
    })
    dispatch = client.get("/api/crawler/dispatch/pending").json()
    codes = {i["plan_code"] for i in dispatch["items"]}
    assert "SCHE-0004" not in codes
    assert {"SCHE-0001", "SCHE-0002", "SCHE-0003"} <= codes
    # 每条下发项都带时区依据且下发时间在目标时区
    for item in dispatch["items"]:
        assert item["target_timezone"] == "UTC"
        assert item["dispatch_time"].endswith("+00:00")
        assert item["utc_time"]


def test_history_batches_keep_original_timezone() -> None:
    res = client.get("/api/schedule/compatibility-reports/history-batches")
    batches = res.json()["items"]
    assert len(batches) == 2
    for batch in batches:
        assert batch["source_timezone"]
        # executed_at 带原时区偏移
        assert batch["executed_at"].split("+")[-1] or "-" in batch["executed_at"]
    ny = next(b for b in batches if b["plan_code"] == "SCHE-0002")
    assert ny["source_timezone"] == "America/New_York"
    assert ny["executed_at"].endswith("-04:00")


def test_duplicate_submit_keeps_single_version() -> None:
    payload = {"target_timezone": "UTC", "start": "2026-02-01T00:00:00+00:00",
               "limit": 3, "idempotency_key": "dup-key"}
    first = client.post("/api/schedule/compatibility-reports", json=payload).json()
    second = client.post("/api/schedule/compatibility-reports", json=payload).json()
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["report"]["report_id"] == second["report"]["report_id"]
    # 连续两次（不带显式幂等键、内容相同）也只保留一份
    body = {"target_timezone": "Europe/Berlin", "start": "2026-02-01T00:00:00+00:00", "limit": 3}
    a = client.post("/api/schedule/compatibility-reports", json=body).json()
    b = client.post("/api/schedule/compatibility-reports", json=body).json()
    assert a["report"]["report_id"] == b["report"]["report_id"]
    assert b["deduped"] is True
    # 任意时刻只有一个 active
    versions = client.get("/api/schedule/compatibility-reports/versions").json()["items"]
    active = [v for v in versions if v["status"] == "active"]
    assert len(active) == 1


def test_crawler_reads_only_active_version() -> None:
    # 生成目标时区 A 的报告
    client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC", "start": "2026-01-05T00:00:00+00:00",
        "limit": 4, "idempotency_key": "ver-a"})
    active_a = client.get("/api/schedule/compatibility-reports/active").json()["report"]
    dispatch_a = client.get("/api/crawler/dispatch/pending").json()
    assert dispatch_a["active_report_id"] == active_a["report_id"]

    # 提交新版 B，A 降级 superseded，爬虫改读 B
    client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "Asia/Shanghai", "start": "2026-01-05T00:00:00+00:00",
        "limit": 4, "idempotency_key": "ver-b"})
    active_b = client.get("/api/schedule/compatibility-reports/active").json()["report"]
    dispatch_b = client.get("/api/crawler/dispatch/pending").json()
    assert active_b["report_id"] != active_a["report_id"]
    assert dispatch_b["active_report_id"] == active_b["report_id"]
    assert dispatch_b["target_timezone"] == "Asia/Shanghai"
    # 新旧版本下游结果不同（目标时区换算结果不同）
    assert dispatch_a["items"][0]["dispatch_time"] != dispatch_b["items"][0]["dispatch_time"]


def test_dst_spring_forward_gap_is_skipped() -> None:
    res = client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC",
        "start": "2026-03-06T00:00:00-05:00",
        "limit": 6,
        "idempotency_key": "dst-gap-test",
    })
    gap_plan = next(e for e in res.json()["report"]["entries"] if e["plan_code"] == "SCHE-0005")
    # 2026-03-08 02:30 纽约春跳缺口：被统计且不进入窗口清单
    assert gap_plan["gap_skipped"] == 1
    assert any(g["wall_time"].startswith("2026-03-08T02:30") for g in gap_plan["gaps"])
    for window in gap_plan["upcoming"]:
        assert "2026-03-08T02:30" not in window["source_wall_time"]
        assert window["utc_time"] and window["target_wall_time"]
    # 缺口跳过后仍补齐到 limit 个真实窗口
    assert len(gap_plan["upcoming"]) == 6


def test_dst_fall_back_keeps_single_batch() -> None:
    res = client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC",
        "start": "2026-10-29T00:00:00-04:00",
        "limit": 5,
        "idempotency_key": "dst-fold-test",
    })
    ny = next(e for e in res.json()["report"]["entries"] if e["plan_code"] == "SCHE-0002")
    walls = [w["source_wall_time"] for w in ny["upcoming"]]
    # 秋退日同墙钟不重复
    assert len(walls) == len(set(walls))
    assert ny["fold_merged"] == 1
    assert any(w["note"] == "dst-fold-merged" for w in ny["upcoming"])


def test_invalid_target_timezone_returns_400() -> None:
    res = client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "Mars/Olympus", "idempotency_key": "bad-tz"})
    assert res.status_code == 400
    assert "UNKNOWN_TIMEZONE" in res.json()["detail"]


def test_reconcile_matches_report_and_dispatch() -> None:
    client.post("/api/schedule/compatibility-reports", json={
        "target_timezone": "UTC", "start": "2026-01-05T00:00:00+00:00",
        "limit": 4, "idempotency_key": "reconcile-1"})
    rec = client.get("/api/crawler/dispatch/reconcile").json()
    # 正常情况下不存在「准入却未下发」或「拦截却下发」的差异
    assert rec["differences"] == []
    assert {b["plan_code"] for b in rec["blocked"]} == {"SCHE-0004"}
