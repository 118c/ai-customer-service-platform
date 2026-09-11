<script setup>
import { computed, onMounted, ref } from 'vue'
import { requestOperations } from './lib/backends'

const adminKey = ref(sessionStorage.getItem('employee-service.admin-key') || '')
const keyDraft = ref(adminKey.value)
const authenticated = ref(false)
const loading = ref(false)
const running = ref(false)
const error = ref('')
const runs = ref([])
const selected = ref(null)
const monitor = ref(null)

const latest = computed(() => runs.value[0] || null)
const passingAgents = computed(() => {
  const agents = Object.values(monitor.value?.agent_stats || {})
  return agents.filter((item) => item.health_state !== 'isolated').length
})
const agentCount = computed(() => Object.keys(monitor.value?.agent_stats || {}).length)
const trendPoints = computed(() => {
  const values = [...runs.value].reverse().slice(-12)
  if (!values.length) return ''
  return values.map((item, index) => {
    const x = values.length === 1 ? 50 : 6 + index * (88 / (values.length - 1))
    const y = 90 - Math.max(0, Math.min(1, item.pass_rate)) * 72
    return `${x},${y}`
  }).join(' ')
})

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value))
}

function metricLabel(name) {
  return ({
    'recall@3': '召回率', mrr: '首条命中', 'ndcg@3': '排序质量',
    citation_precision: '引用准确率', citation_recall: '引用覆盖率',
    answer_faithfulness: '事实支撑度', faithfulness: '答案忠实度',
    faithfulness_rule: '规则忠实度', faithfulness_judge: '模型忠实度',
    answer_relevance: '答案相关性', answer_relevance_rule: '规则相关性',
    answer_relevance_judge: '模型相关性', controlled_degradation: '受控降级',
    latency_p50_ms: '中位耗时', latency_p95_ms: '长尾耗时',
    workflow_success_rate: '流程通过率', failure_rate: '失败率',
    business_success_rate: '业务受理率', business_action_success_rate: '业务操作成功率',
    business_action_latency_ms: '业务操作耗时', confirmation_success_rate: '确认成功率',
    workflow_resume_success_rate: '恢复成功率', review_flow_success_rate: '确认流程完整度',
    cancelled_write_count: '取消后写入数', duplicate_write_count: '重复写入数',
    idempotency_hit_count: '幂等命中数'
  })[name] || name
}

function metricValue(name, value) {
  if (name.endsWith('_ms')) return `${Math.round(Number(value || 0))} ms`
  if (name.endsWith('_count')) return String(Math.round(Number(value || 0)))
  return percent(value)
}

function metricWidth(name, value) {
  return name.endsWith('_ms')
    ? `${Math.min(100, Math.round(Number(value || 0) / 200))}%`
    : name.endsWith('_count')
      ? `${Math.min(100, Math.round(Number(value || 0) * 20))}%`
    : percent(value)
}

async function connect() {
  sessionStorage.setItem('employee-service.admin-key', keyDraft.value)
  adminKey.value = keyDraft.value
  await refresh()
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [history, health] = await Promise.all([
      requestOperations('/admin/evaluations?limit=20', adminKey.value),
      requestOperations('/monitor', adminKey.value)
    ])
    runs.value = history.items || []
    monitor.value = health
    authenticated.value = true
    if (!selected.value && runs.value.length) await openRun(runs.value[0].run_id)
  } catch (reason) {
    error.value = reason.message
    authenticated.value = false
  } finally {
    loading.value = false
  }
}

async function openRun(runId) {
  error.value = ''
  try {
    selected.value = await requestOperations(`/admin/evaluations/${encodeURIComponent(runId)}`, adminKey.value)
  } catch (reason) {
    error.value = reason.message
  }
}

async function runEvaluation() {
  running.value = true
  error.value = ''
  try {
    const report = await requestOperations('/admin/evaluations/run', adminKey.value, {
      method: 'POST', body: JSON.stringify({ dataset_version: '2026.09' })
    })
    selected.value = report
    await refresh()
    selected.value = report
  } catch (reason) {
    error.value = reason.message
  } finally {
    running.value = false
  }
}

async function runRagEvaluation() {
  running.value = true
  error.value = ''
  try {
    const report = await requestOperations('/admin/evaluations/rag/run', adminKey.value, {
      method: 'POST', body: JSON.stringify({ dataset_version: '2026.09' })
    })
    selected.value = report
    await refresh()
    selected.value = report
  } catch (reason) {
    error.value = reason.message
  } finally {
    running.value = false
  }
}

onMounted(() => {
  if (adminKey.value || location.hostname === 'localhost' || location.hostname === '127.0.0.1') refresh()
})
</script>

<template>
  <main v-if="!authenticated" class="access-shell">
    <section class="access-card" aria-labelledby="access-title">
      <div class="console-mark" aria-hidden="true"><i></i><i></i><i></i></div>
      <p class="eyebrow">运维访问</p>
      <h1 id="access-title">服务质量控制台</h1>
      <p>输入运维密钥查看评测记录与运行健康。本机未配置密钥时可直接继续。</p>
      <label for="admin-key">运维密钥</label>
      <input id="admin-key" v-model="keyDraft" type="password" autocomplete="current-password" @keyup.enter="connect" />
      <button :disabled="loading" @click="connect">{{ loading ? '正在连接' : '进入控制台' }}</button>
      <div v-if="error" class="access-error" role="alert">{{ error }}</div>
    </section>
  </main>

  <div v-else class="ops-shell">
    <header class="ops-header">
      <div class="ops-brand">
        <div class="console-mark" aria-hidden="true"><i></i><i></i><i></i></div>
        <div><strong>服务质量控制台</strong><span>员工智能服务运行保障</span></div>
      </div>
      <div class="ops-actions">
        <span class="live-state"><b></b>数据已连接</span>
        <button class="quiet-button" :disabled="loading" @click="refresh">刷新</button>
        <button class="quiet-button" :disabled="running" @click="runRagEvaluation">运行知识评测</button>
        <button class="run-button" :disabled="running" @click="runEvaluation">
          {{ running ? '正在执行完整链路' : '运行完整评测' }}
        </button>
      </div>
    </header>

    <main class="ops-content">
      <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
      <section class="readiness-strip" aria-label="当前质量状态">
        <div class="readiness-lead">
          <span>最近一次通过率</span>
          <strong>{{ latest ? percent(latest.pass_rate) : '待运行' }}</strong>
          <small>{{ latest ? `数据集 ${latest.dataset_version} · ${formatTime(latest.completed_at)}` : '运行完整评测后生成基线' }}</small>
        </div>
        <dl>
          <div><dt>可服务 Agent</dt><dd>{{ passingAgents }}/{{ agentCount }}</dd></div>
          <div><dt>活动告警</dt><dd :class="{ warning: monitor?.active_alerts?.length }">{{ monitor?.active_alerts?.length || 0 }}</dd></div>
          <div><dt>评测运行</dt><dd>{{ runs.length }}</dd></div>
        </dl>
        <div class="trend-panel">
          <span>通过率轨迹</span>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="最近评测通过率趋势">
            <path d="M5 90H95M5 54H95M5 18H95" />
            <polyline v-if="trendPoints" :points="trendPoints" />
          </svg>
        </div>
      </section>

      <div class="ops-grid">
        <section class="run-rail" aria-labelledby="history-title">
          <div class="section-heading"><div><p>质量轨道</p><h2 id="history-title">评测运行记录</h2></div><span>{{ runs.length }} 次</span></div>
          <div v-if="!runs.length" class="empty-state">尚无评测记录。运行一次完整评测建立质量基线。</div>
          <button
            v-for="run in runs" :key="run.run_id" class="rail-item"
            :class="{ active: selected?.run_id === run.run_id }" @click="openRun(run.run_id)"
          >
            <i :class="{ pass: run.pass_rate === 1 }"></i>
            <span><strong>{{ formatTime(run.completed_at) }}</strong><small>{{ run.suite === 'rag' ? '知识评测' : '完整链路' }} · {{ run.dataset_version }}</small></span>
            <b>{{ percent(run.pass_rate) }}</b>
          </button>
        </section>

        <section class="run-detail" aria-labelledby="detail-title">
          <div class="section-heading">
            <div><p>运行明细</p><h2 id="detail-title">{{ selected ? formatTime(selected.completed_at) : '选择一次运行' }}</h2></div>
            <span v-if="selected">{{ selected.metadata?.passed || 0 }}/{{ selected.metadata?.case_count || 0 }} 通过</span>
          </div>
          <template v-if="selected">
            <div class="metric-grid">
              <article v-for="(value, name) in selected.metrics" :key="name">
                <span>{{ metricLabel(name) }}</span><strong>{{ metricValue(name, value) }}</strong>
                <div><i :style="{ width: metricWidth(name, value) }"></i></div>
              </article>
            </div>
            <div class="case-table" role="table" aria-label="评测用例明细">
              <div class="case-row case-head" role="row"><span>用例</span><span>耗时</span><span>结果</span></div>
              <div v-for="item in selected.results" :key="item.case_id" class="case-row" role="row">
                <span><strong>{{ item.case_id }}</strong><small v-if="item.detail">{{ item.detail }}</small></span>
                <span>{{ Math.round(item.latency_ms) }} ms</span>
                <span :class="item.passed ? 'passed' : 'failed'">{{ item.passed ? '通过' : '未通过' }}</span>
              </div>
            </div>
          </template>
          <div v-else class="empty-state">从左侧选择一条评测记录查看指标与用例结果。</div>
        </section>
      </div>
    </main>
  </div>
</template>
