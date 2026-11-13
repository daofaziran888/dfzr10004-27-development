<template>
  <section class="page" data-module="dedup">
    <header class="page-head">
      <div>
        <h2>采集去重比对管理</h2>
        <p class="page-desc">维护采集去重的业务键定义、相似度算法与保留策略，并跟进标注批次回传的人工裁决与保留结果。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出采集去重比对清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>策略编号</span>
        <input v-model="filters.keyword" placeholder="按策略编号检索" />
      </label>
      <label class="filter-item">
        <span>策略状态</span>
        <select v-model="filters.status" class="select">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>待复核清单</h3>
          <p class="panel-desc">标注结果同步到这里：按批次展示本批人工裁决与保留结果，下游入口可直接读取已完成、待处理与冲突数量。</p>
        </div>
        <div class="stat-chips">
          <span class="chip done">已完成 {{ queueStats.已完成 ?? 0 }}</span>
          <span class="chip todo">待处理 {{ queueStats.待处理 ?? 0 }}</span>
          <span class="chip conflict">冲突 {{ queueStats.冲突 ?? 0 }}</span>
          <span class="chip rejected">已驳回 {{ queueStats.已驳回 ?? 0 }}</span>
          <button class="btn" type="button" @click="loadQueue">刷新</button>
        </div>
      </div>

      <table class="data-table queue-table">
        <thead>
          <tr>
            <th>标注批次</th>
            <th>引用策略</th>
            <th>标注记录</th>
            <th>已完成</th>
            <th>待处理</th>
            <th>冲突</th>
            <th>已驳回</th>
            <th>保留结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in queueBatches" :key="batch.标注批次">
            <td>{{ batch.标注批次 }}</td>
            <td>
              {{ batch.策略.策略编号 || '—' }}
              <small v-if="batch.策略.保留策略" class="muted block">{{ batch.策略.保留策略 }}</small>
            </td>
            <td>{{ batch.stats.total ?? batch.items.length }}</td>
            <td>{{ batch.stats.已完成 ?? 0 }}</td>
            <td>{{ batch.stats.待处理 ?? 0 }}</td>
            <td>{{ batch.stats.冲突 ?? 0 }}</td>
            <td>{{ batch.stats.已驳回 ?? 0 }}</td>
            <td>{{ keepSummary(batch.items) }}</td>
          </tr>
          <tr v-if="!queueBatches.length">
            <td colspan="8" class="empty-state">暂无待复核标注批次</td>
          </tr>
        </tbody>
      </table>

      <div v-if="queueBatches.length" class="queue-detail">
        <div class="filter-bar">
          <label class="filter-item">
            <span>查看批次明细</span>
            <select v-model="activeBatch" class="select">
              <option v-for="batch in queueBatches" :key="batch.标注批次" :value="batch.标注批次">{{ batch.标注批次 }}</option>
            </select>
          </label>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>标注编号</th>
              <th>数据记录</th>
              <th>业务键</th>
              <th>拟保留</th>
              <th>人工裁决</th>
              <th>保留结果</th>
              <th>复核结论</th>
              <th>复核状态</th>
              <th>冲突</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in activeItems" :key="item.id">
              <td>{{ item.标注编号 }}</td>
              <td>{{ item.数据记录 }}</td>
              <td>{{ item.业务键 || '—' }}</td>
              <td>{{ item.策略拟保留 }}<small class="muted block">（{{ item.策略拟保留记录 }}）</small></td>
              <td>{{ item.人工裁决 || '—' }}</td>
              <td>{{ item.保留结果 || '—' }}</td>
              <td>{{ item.复核结论 || '—' }}</td>
              <td>{{ item.复核状态 }}</td>
              <td>{{ item.冲突状态 }}</td>
            </tr>
            <tr v-if="!activeItems.length">
              <td colspan="9" class="empty-state">该批次没有标注记录</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="queueError" class="error-text">{{ queueError }}</p>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>去重策略清单</h3>
          <p class="panel-desc">维护业务键、排序字段与保留策略；策略被标注批次引用后，拟保留结果会进入待复核清单。</p>
        </div>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无采集去重比对数据</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条采集去重比对记录</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type QueueItem = {
  id: number
  标注编号: string
  数据记录: string
  业务键: string
  策略拟保留: string
  策略拟保留记录: string
  人工裁决: string
  保留结果: string
  复核结论: string
  复核状态: string
  冲突状态: string
}
type QueueBatch = {
  标注批次: string
  stats: Record<string, number>
  策略: { 策略编号?: string; 保留策略?: string }
  items: QueueItem[]
}
type QueueStats = { 已完成?: number; 待处理?: number; 冲突?: number; 已驳回?: number }

const ENDPOINT = '/api/dedup'
const QUEUE_ENDPOINT = '/api/dedup/review/queue'
const columns = ['策略编号', '业务键列', '排序字段', '保留策略', '时间窗口', '相似阈值', '比对字段', '是否启用']
const actions = ['提交校验', '启用策略', '停用策略']
const statuses = ['草稿', '待校验', '已启用', '已停用']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive({ keyword: '', status: '' })

const queueBatches = ref<QueueBatch[]>([])
const queueStats = ref<QueueStats>({})
const queueError = ref('')
const activeBatch = ref('')

const stats = computed(() => {
  const pending = rows.value.filter((row) => row.status === '待校验').length
  const enabled = rows.value.filter((row) => row.status === '已启用').length
  return [
    { label: '去重策略总数', value: total.value },
    { label: '待校验策略', value: pending },
    { label: '已启用策略', value: enabled },
    { label: '待复核批次', value: queueBatches.value.length },
    { label: '待处理标注', value: queueStats.value.待处理 ?? 0 },
  ]
})

const activeItems = computed(
  () => queueBatches.value.find((batch) => batch.标注批次 === activeBatch.value)?.items ?? [],
)

function keepSummary(items: QueueItem[]) {
  const kept = items.filter((item) => item.保留结果 === '保留').length
  const removed = items.filter((item) => item.保留结果 === '删除').length
  const undecided = items.length - kept - removed
  return `保留 ${kept} / 删除 ${removed} / 未决 ${undecided}`
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集去重比对动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadQueue()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集去重比对操作失败'
  }
}

async function loadQueue() {
  queueError.value = ''
  try {
    const payload = await fetchJson<{ stats: QueueStats; batches: QueueBatch[] }>(QUEUE_ENDPOINT)
    queueBatches.value = payload.batches ?? []
    queueStats.value = payload.stats ?? {}
    if (!queueBatches.value.some((batch) => batch.标注批次 === activeBatch.value)) {
      activeBatch.value = queueBatches.value[0]?.标注批次 ?? ''
    }
  } catch (error) {
    queueError.value = error instanceof Error ? error.message : '待复核清单读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({ keyword: filters.keyword, status: filters.status }).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('去重策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集去重比对列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadQueue()])
})
</script>
