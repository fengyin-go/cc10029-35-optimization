# 智能爬虫采集平台

面向网页爬取、API接入、增量抓取、反爬对抗与采集质量校验的数据采集平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集目标 | `targetsite` | 目标站点 | 站点编号、站点名称、入口URL |
| 抽取规则 | `selector` | 选择器规则 | 规则编号、归属站点、列表选择器 |
| 爬虫引擎 | `crawler` | 爬虫任务 | 任务编号、任务名称、关联站点 |
| 代理池 | `proxy` | 代理节点 | 节点编号、IP地址、端口号 |
| 反爬对抗 | `antiblock` | 反爬策略 | 策略编号、策略名称、触发条件 |
| 定时调度 | `schedule` | 调度计划 | 计划编号、关联任务、Cron表达式 |
| 数据提取 | `extractor` | 提取模板 | 模板编号、模板名称、输入URL |
| 采集校验 | `validate` | 校验规则 | 规则编号、校验字段、期望格式 |
| 失败重试 | `retry` | 重试策略 | 策略编号、适用任务、最大重试 |
| 存储落地 | `storage` | 存储配置 | 配置编号、存储类型、连接信息 |
| 重复去重 | `dedup` | 去重策略 | 策略编号、业务键列、去重算法 |
| 质量报告 | `quality` | 质量指标 | 指标编号、指标名称、统计口径 |
| 运行监控 | `monitor` | 监控大盘 | 大盘编号、关联任务、采集速率 |
| 告警中心 | `alert` | 告警规则 | 规则编号、监控对象、告警条件 |
| 采集API | `apigate` | API配置 | 配置编号、端点路径、请求方法 |
| 历史归档 | `archive` | 归档策略 | 策略编号、数据来源、归档周期 |
| 模板市场 | `template` | 采集模板 | 模板编号、模板名称、适用站点 |
| 采集流程 | `flowline` | 流程编排 | 流程编号、流程名称、步骤序列 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 调度时间口径迁移（时区兼容）

Cron 不再按服务器本地时间解释，改为逐条记录 IANA 时区并显式换算，解决跨时区执行窗口偏移。

- 目标时区由环境变量 `SCHEDULE_TARGET_TZ`（默认 `UTC`）配置，提交报告时也可在请求体
  用 `target_timezone` 单独指定。依赖 `croniter`（Cron 序列）与 `tzdata`（IANA 时区库）。
- **调度时间兼容数据报告**（定时调度）：每条计划同时记录「原时区 `source_timezone` /
  目标时区 `target_timezone` / Cron 表达式 `cron`」三要素；缺少时区依据的计划
  `eligible=false`，错误码 `MISSING_SOURCE_TIMEZONE`，不进入自动执行。
  - `POST /api/schedule/compatibility-reports` 生成并提交（线程锁 + 幂等键去重，
    并发或连续两次相同提交只保留一个成功版本，旧版本置 `superseded`）。
  - `GET /api/schedule/compatibility-reports/active` 读取唯一生效版本。
  - `GET /api/schedule/compatibility-reports/versions` 查看版本链。
  - `GET /api/schedule/compatibility-reports/history-batches` 历史批次（仍按原时区固化，
    迁移只影响尚未生成的执行窗口）。
- **夏令时**：春跳缺口（墙钟时间不存在）的窗口跳过并计入 `gap_skipped`；秋退歧义
  （同一墙钟出现两次）只保留 `fold=0` 的第一个批次（`note=dst-fold-merged`），
  读取端始终只有一个带时区依据的完整批次清单。
- **爬虫引擎待下发清单**：只读取 active 报告，下发时间先换算到目标时区。
  - `GET /api/crawler/dispatch/pending` 待下发任务清单。
  - `GET /api/crawler/dispatch/reconcile` 顺着数据流核对报告与清单的差异。
- 旧版/新版报告目标时区不同，爬虫引擎得到的下游下发时间随之不同；既有列表、登记、
  动作、导出接口的字段与错误码保持不变。

领域：数据工程
子方向：数据采集、爬虫与RPA
