<template>
  <section class="page" data-module="tag">
    <header class="page-head">
      <div>
        <h2>采集数据标注管理</h2>
        <p class="page-desc">按标注批次批量复核：依据去重策略的业务键与拟保留结果生成待确认项，支持整批确认、只驳回争议项，并逐条留存复核回执。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出采集数据标注清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>批量标注复核</h3>
          <p class="panel-desc">一次最多复核 {{ batchLimit }} 条；同一条标注记录只保留一个复核裁定，被驳回项回到待处理。</p>
        </div>
        <label class="filter-item">
          <span>选择标注批次</span>
          <select v-model="activeBatch" class="select" @change="loadBatchDetail">
            <option value="">请选择标注批次</option>
            <option v-for="item in batches" :key="item.标注批次" :value="item.标注批次">
              {{ item.标注批次 }}（待复核 {{ item.stats.待复核 ?? 0 }} / 冲突 {{ item.stats.冲突 ?? 0 }}）
            </option>
          </select>
        </label>
      </div>

      <div v-if="activeBatch && batchDetail" class="batch-meta">
        <div class="policy-card">
          <strong>去重策略引用</strong>
          <dl>
            <dt>策略编号</dt><dd>{{ batchDetail.策略.策略编号 || '—' }}</dd>
            <dt>业务键列</dt><dd>{{ batchDetail.策略.业务键列 || '—' }}</dd>
            <dt>保留策略</dt><dd>{{ batchDetail.策略.保留策略 || '—' }}</dd>
            <dt>策略状态</dt><dd>{{ batchDetail.策略.status || '—' }}</dd>
            <dt>引用方式</dt><dd>{{ batchDetail.策略.引用来源 || '—' }}</dd>
          </dl>
          <p v-if="batchDetail.策略.提示" class="policy-hint">{{ batchDetail.策略.提示 }}</p>
        </div>
        <div class="stat-chips">
          <span class="chip">本批 {{ batchDetail.stats.total }}</span>
          <span class="chip done">已完成 {{ batchDetail.stats.已完成 }}</span>
          <span class="chip todo">待处理 {{ batchDetail.stats.待处理 }}</span>
          <span class="chip conflict">冲突 {{ batchDetail.stats.冲突 }}</span>
          <span class="chip rejected">已驳回 {{ batchDetail.stats.已驳回 }}</span>
        </div>
      </div>

      <form class="review-form" @submit.prevent="submitSelected">
        <label class="filter-item">
          <span>复核人</span>
          <input v-model="reviewForm.operator" placeholder="复核人姓名" />
        </label>
        <label class="filter-item">
          <span>标注类型</span>
          <input v-model="reviewForm.annotationType" placeholder="可覆盖本批标注类型" />
        </label>
        <label class="filter-item grow">
          <span>复核结论</span>
          <input v-model="reviewForm.conclusion" placeholder="整批确认时留空则按策略拟保留结果记录" />
        </label>
        <div class="tag-picker">
          <span class="tag-picker-label">多选标签</span>
          <button
            v-for="tag in tagOptions"
            :key="tag"
            type="button"
            class="tag-option"
            :class="{ active: reviewForm.tags.includes(tag) }"
            @click="toggleTag(tag)"
          >
            {{ tag }}
          </button>
        </div>
        <div class="review-actions">
          <button class="btn primary" type="submit" :disabled="!selectedIds.length || busy">
            提交所选（{{ selectedIds.length }}）
          </button>
          <button class="btn" type="button" :disabled="!activeBatch || busy" @click="confirmGroup">
            整组确认{{ selectedIds.length ? `（所选 ${selectedIds.length} 条）` : '（本批全部待复核）' }}
          </button>
          <button class="btn danger" type="button" :disabled="!activeBatch || busy" @click="rejectDisputed">
            只驳回争议项{{ selectedIds.length ? `（所选 ${selectedIds.length} 条）` : '' }}
          </button>
          <button class="btn ghost" type="button" @click="clearSelection">清空选择</button>
        </div>
      </form>

      <table class="data-table review-table">
        <thead>
          <tr>
            <th class="check-col">
              <input type="checkbox" :checked="allSelected" @change="toggleAll" />
            </th>
            <th>标注编号</th>
            <th>数据记录</th>
            <th>标注类型</th>
            <th>标注标签</th>
            <th>业务键</th>
            <th>策略拟保留</th>
            <th>复核状态</th>
            <th>冲突提示</th>
            <th>本批人工裁决</th>
            <th>逐条裁定</th>
            <th>明细</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in reviewRows" :key="row.id">
            <tr :class="{ 'row-muted': !row.是否待复核 }">
              <td class="check-col">
                <input
                  v-model="selectedIds"
                  type="checkbox"
                  :value="row.id"
                  :disabled="!row.是否待复核"
                />
              </td>
              <td>{{ row.标注编号 }}</td>
              <td>{{ row.数据记录 }}</td>
              <td>{{ row.标注类型 }}</td>
              <td>
                <span v-for="tag in row.标签列表" :key="tag" class="tag-pill">{{ tag }}</span>
              </td>
              <td>{{ row.业务键 }}</td>
              <td>
                {{ row.策略拟保留 }}
                <small class="muted">（{{ row.策略拟保留记录 }}）</small>
              </td>
              <td>{{ row.复核状态 }}</td>
              <td>
                <span :class="['status-dot', conflictClass(row)]">{{ row.冲突状态 }}</span>
                <small v-if="row.冲突原因" class="muted block">{{ row.冲突原因 }}</small>
              </td>
              <td>{{ row.人工裁决 || '—' }}<small v-if="row.保留结果" class="muted block">保留：{{ row.保留结果 }}</small></td>
              <td>
                <select
                  v-if="row.是否待复核"
                  v-model="rowDecision[row.id]"
                  class="select select-sm"
                >
                  <option value="确认保留">确认保留</option>
                  <option value="确认删除">确认删除</option>
                  <option value="驳回待处理">驳回待处理</option>
                </select>
                <span v-else class="muted">已裁定</span>
              </td>
              <td>
                <button class="link" type="button" @click="toggleDetail(row.id)">
                  {{ expandedIds.includes(row.id) ? '收起' : '查看' }}
                </button>
              </td>
            </tr>
            <tr v-if="expandedIds.includes(row.id)" :key="`detail-${row.id}`" class="detail-row">
              <td colspan="12">
                <dl class="detail-grid">
                  <dt>标注编号</dt><dd>{{ row.标注编号 }}</dd>
                  <dt>数据记录</dt><dd>{{ row.数据记录 }}</dd>
                  <dt>标注人 / 时间</dt><dd>{{ row.标注人 }} · {{ row.标注时间 }}</dd>
                  <dt>标注版本</dt><dd>{{ row.标注版本 }}</dd>
                  <dt>业务键 / 策略</dt><dd>{{ row.业务键 }} · {{ row.策略编号 }}</dd>
                  <dt>拟保留记录</dt><dd>{{ row.策略拟保留记录 }}</dd>
                  <dt>复核人 / 时间</dt><dd>{{ row.复核人 || '—' }} · {{ row.复核时间 || '—' }}</dd>
                  <dt>复核回执</dt><dd>{{ row.复核回执 || '—' }}</dd>
                  <dt>复核结论</dt><dd>{{ row.复核结论 || '—' }}</dd>
                  <dt>冲突原因</dt><dd>{{ row.冲突原因 || '无' }}</dd>
                </dl>
                <div class="detail-actions">
                  <button class="btn" type="button" :disabled="!row.是否待复核 || busy" @click="decideOne(row, '确认保留')">单条确认保留</button>
                  <button class="btn" type="button" :disabled="!row.是否待复核 || busy" @click="decideOne(row, '确认删除')">单条确认删除</button>
                  <button class="btn danger" type="button" :disabled="!row.是否待复核 || busy" @click="decideOne(row, '驳回待处理')">单条驳回</button>
                  <button class="btn ghost" type="button" @click="viewEntry(row)">单独查看记录</button>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!reviewRows.length">
            <td colspan="12" class="empty-state">
              {{ activeBatch ? '当前批次没有待确认的标注记录' : '请选择标注批次后开始批量复核' }}
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="groupResult" class="receipt-panel">
        <div class="receipt-head">
          <strong>{{ groupResult.message }}</strong>
          <span class="muted">整批回执 {{ groupResult.group_receipt }}</span>
        </div>
        <ul class="receipt-list">
          <li v-for="item in groupResult.results" :key="`${groupResult.group_receipt}-${item.id}`">
            <span :class="item.ok ? 'ok-text' : 'error-text'">{{ item.ok ? '已生效' : '未生效' }}</span>
            <span>{{ item.标注编号 || `#${item.id}` }}</span>
            <span class="muted">{{ item.message }}</span>
            <span v-if="item.receipt" class="muted">回执 {{ item.receipt }}</span>
          </li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>全部标注记录</h3>
          <p class="panel-desc">批量复核后仍可按标注编号、状态与批次单独查看每一条记录。</p>
        </div>
      </div>
      <form class="filter-bar" @submit.prevent="reloadEntries">
        <label class="filter-item">
          <span>标注编号</span>
          <input v-model="filters.keyword" placeholder="按标注编号检索" />
        </label>
        <label class="filter-item">
          <span>复核状态</span>
          <select v-model="filters.status" class="select">
            <option value="">全部状态</option>
            <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>标注批次</span>
          <select v-model="filters.batch" class="select">
            <option value="">全部批次</option>
            <option v-for="item in batches" :key="item.标注批次" :value="item.标注批次">{{ item.标注批次 }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>明细</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>
              <button class="link" type="button" @click="viewEntry(row)">查看</button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 1" class="empty-state">暂无采集数据标注数据</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条采集数据标注记录</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </section>

    <aside v-if="detailEntry" class="detail-drawer">
      <div class="panel-head">
        <h3>标注记录 {{ detailEntry.标注编号 }}</h3>
        <button class="btn ghost" type="button" @click="detailEntry = null">关闭</button>
      </div>
      <dl class="detail-grid">
        <dt v-for="field in entryDetailFields" :key="field">{{ field }}</dt>
        <dd v-for="field in entryDetailFields" :key="`v-${field}`">{{ formatDetail(detailEntry[field]) }}</dd>
      </dl>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { fetchJson, request } from '@/api/client'

type BatchStats = { total?: number; 待复核?: number; 已完成?: number; 待处理?: number; 冲突?: number; 已驳回?: number }
type Policy = { 策略编号?: string; 业务键列?: string; 保留策略?: string; 时间窗口?: string; status?: string; 引用来源?: string; 提示?: string }
type ReviewItem = {
  id: number
  标注编号: string
  数据记录: string
  标注类型: string
  标注标签: string
  标签列表: string[]
  可选标签?: string[]
  业务键: string
  策略编号: string
  策略拟保留: string
  策略拟保留记录: string
  人工裁决: string
  保留结果: string
  复核结论: string
  复核状态: string
  复核回执: string
  复核人?: string
  复核时间?: string
  冲突状态: string
  冲突原因: string
  标注版本: number
  标注人: string
  标注时间: string
  是否待复核: boolean
  是否已复核: boolean
  是否被驳回: boolean
  策略拟保留方向: string
}
type BatchSummary = { 标注批次: string; total: number; stats: BatchStats; 策略: Policy }
type BatchDetail = { 标注批次: string; batch_limit: number; stats: BatchStats; 策略: Policy; items: ReviewItem[] }
type ReviewResult = { id: number; 标注编号?: string; ok: boolean; message: string; receipt?: string | null; duplicate?: boolean }
type GroupResult = { ok: boolean; message: string; batch: string; group_receipt: string; results: ReviewResult[]; stats: BatchStats }
type PageRow = Record<string, string | number | null>
type EntryRow = Record<string, unknown>

const ENDPOINT = '/api/tag'
const QUEUE_ENDPOINT = '/api/tag/review/batches'
const columns = ['标注编号', '数据记录', '标注类型', '标注标签', '标注批次', '复核状态', '人工裁决', '保留结果', '复核人', '复核时间']
const statuses = ['草稿', '待复核', '待处理', '已复核', '已归档']
const entryDetailFields = [
    '标注编号', '数据记录', '标注类型', '标注标签', '标注批次', '复核状态', '人工裁决', '保留结果',
    '复核结论', '复核回执', '复核人', '复核时间', '业务键', '策略编号', '策略拟保留记录', '冲突状态', '冲突原因', '标注版本',
]

const batches = ref<BatchSummary[]>([])
const batchLimit = ref(50)
const candidateTags = ref<string[]>([])
const activeBatch = ref('')
const batchDetail = ref<BatchDetail | null>(null)
const reviewRows = ref<ReviewItem[]>([])
const selectedIds = ref<number[]>([])
const expandedIds = ref<number[]>([])
const rowDecision = reactive<Record<number, string>>({})
const groupResult = ref<GroupResult | null>(null)
const reviewForm = reactive({
    operator: '值班复核',
    annotationType: '',
    conclusion: '',
    tags: [] as string[],
})
const busy = ref(false)

const rows = ref<PageRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive({ keyword: '', status: '', batch: '' })
const detailEntry = ref<EntryRow | null>(null)

const stats = computed(() => {
    const totalRows = batches.value.reduce((sum, item) => sum + item.total, 0)
    const pending = batches.value.reduce((sum, item) => sum + Number(item.stats.待复核 ?? 0), 0)
    const conflicts = batches.value.reduce((sum, item) => sum + Number(item.stats.冲突 ?? 0), 0)
    const done = batches.value.reduce((sum, item) => sum + Number(item.stats.已完成 ?? 0), 0)
    return [
        { label: '标注批次', value: batches.value.length },
        { label: '标注记录总数', value: totalRows },
        { label: '待复核标注', value: pending },
        { label: '冲突提示', value: conflicts },
        { label: '已完成复核', value: done },
    ]
})

const tagOptions = computed(() => {
    const fromRows = reviewRows.value.flatMap((row) => row.可选标签 ?? [])
    return Array.from(new Set([...candidateTags.value, ...fromRows]))
})

const allSelected = computed(() => {
    const selectable = reviewRows.value.filter((row) => row.是否待复核)
    return selectable.length > 0 && selectable.every((row) => selectedIds.value.includes(row.id))
})

watch(selectedIds, (value) => {
    if (!value.length) return
    const selected = reviewRows.value.find((row) => value.includes(row.id))
    if (selected && !reviewForm.annotationType) {
        reviewForm.annotationType = selected.标注类型
    }
})

function toggleTag(tag: string) {
    if (reviewForm.tags.includes(tag)) {
        reviewForm.tags = reviewForm.tags.filter((item) => item !== tag)
    } else {
        reviewForm.tags = [...reviewForm.tags, tag]
    }
}

function toggleAll() {
    const selectable = reviewRows.value.filter((row) => row.是否待复核).map((row) => row.id)
    selectedIds.value = allSelected.value ? [] : selectable
}

function clearSelection() {
    selectedIds.value = []
}

function toggleDetail(id: number) {
    if (expandedIds.value.includes(id)) {
        expandedIds.value = expandedIds.value.filter((item) => item !== id)
    } else {
        expandedIds.value = [...expandedIds.value, id]
    }
}

function conflictClass(row: ReviewItem) {
    if (row.冲突状态 === '冲突待裁决') return 'conflict'
    if (row.冲突状态 === '已驳回') return 'rejected'
    if (row.冲突状态 === '已裁决') return 'done'
    return 'ok'
}

function formatDetail(value: unknown) {
    if (Array.isArray(value)) return value.length ? value.join('、') : '—'
    if (value === null || value === undefined || value === '') return '—'
    return String(value)
}

function proposalDecision(row: ReviewItem) {
    return row.策略拟保留方向 === '删除' ? '确认删除' : '确认保留'
}

function seedDecisions(items: ReviewItem[]) {
    for (const key of Object.keys(rowDecision)) {
        delete rowDecision[Number(key)]
    }
    for (const item of items) {
        rowDecision[item.id] = proposalDecision(item)
    }
}

function hydrateDetail(payload: BatchDetail) {
    batchDetail.value = payload
    reviewRows.value = payload.items ?? []
    seedDecisions(reviewRows.value)
    const validIds = new Set(reviewRows.value.filter((row) => row.是否待复核).map((row) => row.id))
    selectedIds.value = selectedIds.value.filter((id) => validIds.has(id))
}

async function loadBatches() {
    try {
        const payload = await fetchJson<{ batch_limit: number; candidate_tags: string[]; batches: BatchSummary[] }>(QUEUE_ENDPOINT)
        batches.value = payload.batches ?? []
        batchLimit.value = payload.batch_limit ?? 50
        candidateTags.value = payload.candidate_tags ?? []
        if (!activeBatch.value && batches.value.length) {
            activeBatch.value = batches.value[0].标注批次
        }
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '待复核批次读取失败'
    }
}

async function loadBatchDetail() {
    errorMessage.value = ''
    groupResult.value = null
    if (!activeBatch.value) {
        batchDetail.value = null
        reviewRows.value = []
        return
    }
    try {
        const payload = await fetchJson<BatchDetail>(`${ENDPOINT}/review/batches/${encodeURIComponent(activeBatch.value)}`)
        hydrateDetail(payload)
        reviewForm.annotationType = ''
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '待确认项读取失败'
    }
}

async function refreshAll() {
    await Promise.all([loadBatches(), loadBatchDetail(), reloadEntries()])
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
    if (!response.ok) {
        throw new Error(`接口返回 ${response.status}，复核未提交`)
    }
    return (await response.json()) as T
}

async function submitSelected() {
    if (!activeBatch.value || !selectedIds.value.length || busy.value) return
    errorMessage.value = ''
    busy.value = true
    try {
        const payload = await postJson<GroupResult>(`${ENDPOINT}/review/submit`, {
            batch: activeBatch.value,
            operator: reviewForm.operator,
            tags: reviewForm.tags.length ? reviewForm.tags : null,
            annotation_type: reviewForm.annotationType || null,
            conclusion: reviewForm.conclusion || null,
            items: selectedIds.value.map((id) => {
                const row = reviewRows.value.find((item) => item.id === id)
                return {
                    id,
                    decision: rowDecision[id] ?? proposalDecision(row as ReviewItem),
                    expected_version: row?.标注版本,
                }
            }),
        })
        groupResult.value = payload
        selectedIds.value = []
        await refreshAll()
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '批量复核提交失败'
    } finally {
        busy.value = false
    }
}

async function confirmGroup() {
    if (!activeBatch.value || busy.value) return
    errorMessage.value = ''
    busy.value = true
    try {
        const payload = await postJson<GroupResult>(`${ENDPOINT}/review/confirm`, {
            batch: activeBatch.value,
            items: selectedIds.value,
            operator: reviewForm.operator,
            conclusion: reviewForm.conclusion || null,
            tags: reviewForm.tags.length ? reviewForm.tags : null,
            annotation_type: reviewForm.annotationType || null,
        })
        groupResult.value = payload
        selectedIds.value = []
        await refreshAll()
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '整组确认失败'
    } finally {
        busy.value = false
    }
}

async function rejectDisputed() {
    if (!activeBatch.value || busy.value) return
    errorMessage.value = ''
    busy.value = true
    try {
        const payload = await postJson<GroupResult>(`${ENDPOINT}/review/reject`, {
            batch: activeBatch.value,
            items: selectedIds.value,
            operator: reviewForm.operator,
            conclusion: reviewForm.conclusion || '人工复核驳回，回到待处理',
            tags: reviewForm.tags.length ? reviewForm.tags : null,
            annotation_type: reviewForm.annotationType || null,
        })
        groupResult.value = payload
        selectedIds.value = []
        await refreshAll()
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '驳回争议项失败'
    } finally {
        busy.value = false
    }
}

async function decideOne(row: ReviewItem, decision: string) {
    if (busy.value) return
    errorMessage.value = ''
    busy.value = true
    try {
        const payload = await postJson<{ ok: boolean; message: string; receipt: string | null; entry: ReviewItem }>(
            `${ENDPOINT}/${row.id}/decisions`,
            {
                decision,
                operator: reviewForm.operator,
                tags: reviewForm.tags.length ? reviewForm.tags : null,
                annotation_type: reviewForm.annotationType || null,
                conclusion: reviewForm.conclusion || null,
                expected_version: row.标注版本,
            },
        )
        groupResult.value = {
            ok: payload.ok,
            message: payload.message,
            batch: activeBatch.value,
            group_receipt: payload.receipt ?? '',
            results: [{ id: row.id, 标注编号: row.标注编号, ok: payload.ok, message: payload.message, receipt: payload.receipt }],
            stats: batchDetail.value?.stats ?? {},
        }
        await refreshAll()
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '单条裁定失败'
    } finally {
        busy.value = false
    }
}

async function viewEntry(row: PageRow | ReviewItem) {
    try {
        detailEntry.value = await fetchJson<EntryRow>(`${ENDPOINT}/${row.id}`)
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '标注记录读取失败'
    }
}

function resetFilters() {
    filters.keyword = ''
    filters.status = ''
    filters.batch = ''
    void reloadEntries()
}

function exportRows() {
    window.open(`${ENDPOINT}/export`, '_blank')
}

async function reloadEntries() {
    const query = new URLSearchParams({
        keyword: filters.keyword,
        status: filters.status,
        batch: filters.batch,
    }).toString()
    try {
        const payload = await fetchJson<{ items: PageRow[]; total: number }>(`${ENDPOINT}?${query}`)
        rows.value = payload.items ?? []
        total.value = payload.total ?? rows.value.length
    } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : '标注记录列表读取失败'
    }
}

onMounted(async () => {
    await loadBatches()
    await loadBatchDetail()
    await reloadEntries()
})
</script>
