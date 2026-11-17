"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = "智能爬虫采集平台"
    env: str = os.getenv("APP_ENV", "local")
    port: int = 8000
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 调度时间口径迁移的目标时区（IANA 名）。存量计划的原时区逐条记录在兼容报告里。
    target_timezone: str = os.getenv("SCHEDULE_TARGET_TZ", "UTC")


settings = Settings()
