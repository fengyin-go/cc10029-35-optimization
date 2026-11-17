<template>
  <section class="page" data-module="crawler">
    <header class="page-head">
      <div><h2>爬虫引擎</h2><p class="page-desc">管理爬虫任务的调度配置、并发度和批次参数，驱动采集引擎执行。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记爬虫任务</button>
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

    <!-- ===== 待下发任务清单：读取调度时间兼容报告 active 版本 ===== -->
    <div class="migration-panel" data-testid="dispatch-panel">
      <div class="migration-head">
        <h3>待下发任务清单</h3>
        <p class="page-desc">读取定时调度的调度时间兼容数据报告（active 版本），下发时间已换算到目标时区；缺时区依据的计划不下发。</p>
      </div>
      <div class="filter-bar">
        <button class="btn primary" type="button" data-testid="load-dispatch" @click="loadDispatch">刷新待下发清单</button>
        <button class="btn" type="button" data-testid="load-reconcile" @click="loadReconcile">与兼容报告核对差异</button>
        <span v-if="dispatch.active_report_id" class="kpi-chip">生效报告 {{ dispatch.active_report_id }} · 目标时区 {{ dispatch.target_timezone }} · 共 {{ dispatch.total }} 条</span>
      </div>
      <p v-if="dispatch.message" class="migration-msg">{{ dispatch.message }}</p>
      <div v-if="dispatch.items && dispatch.items.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr><th>计划编号</th><th>关联任务</th><th>Cron</th><th>原时区</th><th>目标时区</th><th>下发时间（目标时区）</th><th>UTC</th><th>夏令时</th></tr>
          </thead>
          <tbody>
            <tr v-for="(it, idx) in dispatch.items" :key="idx" :data-testid="`dispatch-row-${it.plan_code}-${idx}`">
              <td>{{ it.plan_code }}</td><td>{{ it.task }}</td><td><code>{{ it.cron }}</code></td>
              <td>{{ it.source_timezone }}</td><td>{{ it.target_timezone }}</td>
              <td><span class="wall">{{ it.dispatch_time }}</span></td>
              <td class="muted">{{ it.utc_time }}</td>
              <td><span v-if="it.note === 'dst-fold-merged'" class="tag warn">秋退合并</span><span v-else>{{ it.dst ? '夏令时' : '标准时' }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="reconcile" class="reconcile-box" data-testid="reconcile-box">
        <h4 class="table-title">数据核对：兼容报告 → 待下发清单</h4>
        <p class="muted">报告计划：{{ reconcile.report_plan_codes?.join('、') || '—' }}</p>
        <p class="muted">已下发：{{ reconcile.dispatched_plan_codes?.join('、') || '—' }}</p>
        <p v-if="reconcile.differences && reconcile.differences.length" class="tag bad">存在差异：{{ reconcile.differences.length }} 处</p>
        <p v-else class="tag ok" data-testid="reconcile-clean">无差异，报告与清单一致</p>
        <p v-for="b in reconcile.blocked" :key="b.plan_code" class="muted">拦截：{{ b.plan_code }}（{{ b.error_code }}）</p>
      </div>
    </div>
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记爬虫任务</h3>
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
const ENDPOINT = '/api/crawler'
const columns = ["任务编号", "任务名称", "关联站点", "关联规则", "并发线程", "调度周期", "超时秒数", "下发批次"]
const actions = ["提交校验", "启用配置", "停用配置"]
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])
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

// ===== 待下发清单与差异核对 =====
const dispatch = ref<any>({ items: [] })
const reconcile = ref<any>(null)
async function loadDispatch() {
  const res = await request(`${ENDPOINT}/dispatch/pending`)
  dispatch.value = await res.json()
}
async function loadReconcile() {
  const res = await request(`${ENDPOINT}/dispatch/reconcile`)
  reconcile.value = await res.json()
}
onMounted(async () => { await reload(); await loadDispatch(); await loadReconcile() })
</script>
