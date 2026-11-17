<template>
  <section class="page" data-module="schedule">
    <header class="page-head">
      <div><h2>定时调度</h2><p class="page-desc">配置采集调度的 Cron 表达式、执行窗口和失败处理策略。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记调度计划</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
      </div>
    </header>
    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <!-- ===== 调度时间口径迁移：兼容数据报告 ===== -->
    <div class="migration-panel" data-testid="migration-panel">
      <div class="migration-head">
        <h3>调度时间兼容数据报告</h3>
        <p class="page-desc">迁移 Cron 口径到目标时区；报告同时记录原时区、目标时区与 Cron 表达式，缺时区依据的计划不进入自动执行。</p>
      </div>
      <div class="filter-bar">
        <label><span>目标时区</span>
          <select v-model="targetTz" data-testid="target-tz">
            <option v-for="tz in timezoneOptions" :key="tz" :value="tz">{{ tz }}</option>
          </select>
        </label>
        <label><span>窗口起始</span><input type="datetime-local" v-model="startAt" data-testid="window-start" /></label>
        <label><span>窗口条数</span><input type="number" min="1" max="20" v-model.number="limit" style="width:90px" /></label>
        <button class="btn primary" type="button" data-testid="generate-report" @click="generateReport">生成兼容报告</button>
        <button class="btn" type="button" data-testid="refresh-report" @click="loadActive">读取生效版本</button>
      </div>
      <p v-if="reportMessage" class="migration-msg" data-testid="report-message">{{ reportMessage }}</p>

      <div v-if="activeReport" data-testid="report-summary">
        <div class="kpi-row">
          <span class="kpi-chip">版本 {{ activeReport.report_id }}（{{ activeReport.status }}）</span>
          <span class="kpi-chip">目标时区 {{ activeReport.target_timezone }}</span>
          <span class="kpi-chip">计划 {{ activeReport.summary.total }}</span>
          <span class="kpi-chip ok">可执行 {{ activeReport.summary.eligible }}</span>
          <span class="kpi-chip bad">拦截 {{ activeReport.summary.blocked }}</span>
          <span class="kpi-chip warn">秋退合并 {{ activeReport.summary.fold_merged }}</span>
          <span class="kpi-chip warn">春跳跳过 {{ activeReport.summary.gap_skipped }}</span>
          <span class="kpi-chip">历史批次 {{ activeReport.summary.history_batches }}</span>
        </div>
        <p class="migration-note">{{ activeReport.migration_scope }}</p>

        <h4 class="table-title">存量计划 → 迁移后执行窗口对照</h4>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>计划编号</th><th>Cron表达式</th><th>原时区</th><th>目标时区</th>
                <th>准入</th><th>迁移后执行窗口（下发时间，已换算目标时区）</th><th>夏令时</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in activeReport.entries" :key="e.plan_code" :data-testid="`report-row-${e.plan_code}`" :data-eligible="String(e.eligible)">
                <td>{{ e.plan_code }}</td>
                <td><code>{{ e.cron }}</code></td>
                <td>{{ e.source_timezone || '— 缺失 —' }}</td>
                <td>{{ e.target_timezone }}</td>
                <td>
                  <span v-if="e.eligible" class="tag ok">可执行</span>
                  <span v-else class="tag bad" :data-testid="`blocked-${e.plan_code}`" :title="e.block_reason">拦截（{{ e.error_code }}）</span>
                </td>
                <td>
                  <ul v-if="e.upcoming.length" class="window-list">
                    <li v-for="(w, i) in e.upcoming" :key="i">
                      <span class="wall">{{ w.target_wall_time }}</span>
                      <span class="src">原时区 {{ w.source_wall_time }}</span>
                      <span v-if="w.note === 'dst-fold-merged'" class="tag warn">秋退合并</span>
                    </li>
                  </ul>
                  <span v-else class="muted">{{ e.block_reason || '暂无窗口' }}</span>
                </td>
                <td>
                  <span v-if="e.gap_skipped" class="tag warn">春跳跳过 {{ e.gap_skipped }}</span>
                  <span v-if="e.fold_merged" class="tag warn">秋退合并 {{ e.fold_merged }}</span>
                  <span v-if="!e.gap_skipped && !e.fold_merged" class="muted">无冲突</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <h4 class="table-title">历史已执行批次（仍按原时区解释，迁移不回写）</h4>
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>批次号</th><th>计划编号</th><th>Cron</th><th>原时区</th><th>执行时间（原时区）</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="h in activeReport.history_batches" :key="h.batch_no" :data-testid="`history-${h.batch_no}`">
                <td>{{ h.batch_no }}</td><td>{{ h.plan_code }}</td><td><code>{{ h.cron }}</code></td>
                <td>{{ h.source_timezone }}</td><td>{{ h.executed_at }}</td><td class="muted">{{ h.note }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h4 class="table-title">报告版本（并发/连续提交只保留一个 active）</h4>
        <div class="table-scroll">
          <table class="data-table">
            <thead><tr><th>报告号</th><th>版本</th><th>状态</th><th>目标时区</th><th>生成时间</th></tr></thead>
            <tbody>
              <tr v-for="v in versions" :key="v.report_id" :data-testid="`version-${v.report_id}`" :data-status="v.status">
                <td>{{ v.report_id }}</td><td>{{ v.version }}</td><td>{{ v.status }}</td>
                <td>{{ v.target_timezone }}</td><td>{{ v.generated_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记调度计划</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/schedule'
const columns = ["计划编号", "关联任务", "Cron表达式", "执行窗口", "重试策略", "失败通知", "最大并发", "是否启用"]
const actions = ["提交校验", "启用配置", "停用配置"]
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])

// ===== 兼容报告状态 =====
const timezoneOptions = ['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles']
const targetTz = ref('UTC')
const startAt = ref('')
const limit = ref(5)
const activeReport = ref<any>(null)
const versions = ref<any[]>([])
const reportMessage = ref('')

function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); allItems.value = data.items || []; applyFilter() } catch(e) { errorMessage.value = '加载失败' }
}
function applyFilter() {
  let items = allItems.value
  if(dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if(dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }

async function loadVersions() {
  const res = await request(`${ENDPOINT}/compatibility-reports/versions`)
  versions.value = (await res.json()).items || []
}
async function loadActive() {
  const res = await request(`${ENDPOINT}/compatibility-reports/active`)
  const data = await res.json()
  activeReport.value = data.report
  await loadVersions()
}
async function generateReport() {
  reportMessage.value = ''
  const payload: Record<string, unknown> = { target_timezone: targetTz.value, limit: limit.value }
  if (startAt.value) payload.start = new Date(startAt.value).toISOString()
  const res = await request(`${ENDPOINT}/compatibility-reports`, { method: 'POST', body: JSON.stringify(payload) })
  if (!res.ok) { const err = await res.json().catch(() => ({ detail: '生成失败' })); reportMessage.value = err.detail || '生成失败'; return }
  const data = await res.json()
  activeReport.value = data.report
  reportMessage.value = data.deduped ? '检测到重复提交，已去重并返回当前生效版本（未产生第二份）' : '兼容报告已生成并生效'
  await loadVersions()
}
onMounted(async () => { await reload(); await loadActive() })
</script>
