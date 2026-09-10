<template>
  <main class="app-shell" :class="{ 'with-references': referencesOpen }">
    <aside class="history-panel">
      <div class="product">
        <div class="product-mark" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <div>
          <strong>员工智能客服</strong>
          <small>企业内部服务</small>
        </div>
      </div>

      <button class="new-conversation" @click="newConversation">
        <span>＋</span>发起新对话
      </button>

      <section class="history-list">
        <p class="panel-caption">最近对话</p>
        <button
          v-for="session in sessions"
          :key="session.id"
          :class="['history-item', { active: session.id === identity.conversationId }]"
          @click="selectConversation(session)"
        >
          <span class="history-icon">{{ session.id === identity.conversationId ? '●' : '○' }}</span>
          <span>
            <strong>{{ session.title }}</strong>
            <small>{{ session.updatedAt }}</small>
          </span>
        </button>
        <p v-if="sessions.length === 0" class="history-empty">完成第一次咨询后，会话会保存在这里。</p>
      </section>

      <div class="employee-card">
        <div class="employee-avatar">员</div>
        <div><strong>当前员工</strong><small>{{ identity.userId }}</small></div>
        <span class="presence-dot" title="在线"></span>
      </div>
    </aside>

    <section class="chat-workspace">
      <header class="chat-header">
        <div>
          <h1>智能客服</h1>
          <p><span :class="['service-dot', { online: serviceOnline }]"></span>{{ serviceOnline ? '服务在线' : '正在连接服务' }}</p>
        </div>
        <button class="header-button" @click="newConversation">新对话</button>
      </header>

      <div ref="messageList" class="message-list" aria-live="polite">
        <section v-if="messages.length === 0" class="welcome">
          <div class="assistant-symbol" aria-hidden="true">
            <span></span><span></span><i></i>
          </div>
          <p>{{ greeting }}，{{ identity.userId }}</p>
          <h2>今天有什么可以帮你？</h2>
          <span>直接描述遇到的问题，我会结合企业内部资料为你解答，并协助处理相关事项。</span>
        </section>

        <article v-for="item in messages" :key="item.id" :class="['message', item.role]">
          <div v-if="item.role === 'assistant'" class="assistant-avatar" aria-hidden="true">
            <span></span><span></span><i></i>
          </div>
          <div class="message-content">
            <div class="message-heading">
              <strong>{{ item.role === 'assistant' ? '智能客服' : '我' }}</strong>
              <time>{{ item.time }}</time>
            </div>
            <p>{{ item.content }}</p>

            <button
              v-if="item.result?.sources?.length"
              class="reference-link"
              @click="showReferences(item.result.sources)"
            >
              <span>▤</span>查看参考资料（{{ item.result.sources.length }}）
            </button>

            <section v-if="item.result?.confirmation" class="confirmation-card">
              <div class="confirmation-title">
                <div class="document-icon">✓</div>
                <div>
                  <strong>{{ item.result.confirmation.title }}</strong>
                  <p>{{ item.result.confirmation.description }}</p>
                </div>
              </div>
              <dl>
                <div v-for="field in item.result.confirmation.fields" :key="field.label">
                  <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd>
                </div>
              </dl>
              <div class="confirmation-actions">
                <button :disabled="busy" @click="handleConfirmation(item, 'confirm')">
                  {{ item.result.confirmation.confirm_label }}
                </button>
                <button class="plain" :disabled="busy" @click="handleConfirmation(item, 'cancel')">
                  {{ item.result.confirmation.cancel_label }}
                </button>
              </div>
            </section>

            <section v-if="item.result?.receipt" class="receipt-card">
              <span class="receipt-check">✓</span>
              <div><strong>{{ item.result.receipt.message }}</strong><small>可在相关业务系统中继续查看处理进度</small></div>
            </section>

            <div v-if="item.error" class="error-message">
              <strong>消息发送失败</strong><span>{{ item.error }}，请稍后重试。</span>
            </div>
          </div>
        </article>

        <div v-if="busy" class="message assistant pending-message">
          <div class="assistant-avatar" aria-hidden="true"><span></span><span></span><i></i></div>
          <div class="typing"><span></span><span></span><span></span></div>
        </div>
      </div>

      <form class="composer" @submit.prevent="sendMessage">
        <div class="composer-box">
          <textarea
            v-model="draft"
            rows="2"
            aria-label="输入消息"
            placeholder="请输入你的问题"
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>
          <div class="composer-footer">
            <span>内容可能存在偏差，重要事项请以企业正式制度为准</span>
            <button :disabled="busy || !draft.trim()" aria-label="发送消息">↑</button>
          </div>
        </div>
      </form>
    </section>

    <aside v-if="referencesOpen" class="reference-panel">
      <header>
        <div><strong>参考资料</strong><span>本次回答使用的企业内部资料</span></div>
        <button aria-label="关闭参考资料" @click="referencesOpen = false">×</button>
      </header>
      <div class="reference-list">
        <article v-for="(source, index) in activeReferences" :key="`${source.title}-${index}`">
          <div class="source-number">{{ index + 1 }}</div>
          <div><strong>{{ source.title }}</strong><p>{{ source.excerpt }}</p></div>
        </article>
      </div>
      <footer>资料内容由知识库统一维护，更新时间以原始文档为准。</footer>
    </aside>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { confirmRequest, createIdentity, requestChat, requestHealth } from './lib/backends'

const identity = reactive(createIdentity())
const sessions = ref(readSessions())
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const serviceOnline = ref(false)
const referencesOpen = ref(false)
const activeReferences = ref([])
const messageList = ref(null)

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 11) return '上午好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

onMounted(async () => {
  try {
    serviceOnline.value = (await requestHealth()).status === 'ok'
  } catch {
    serviceOnline.value = false
  }
})

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || busy.value) return
  messages.value.push({ id: crypto.randomUUID(), role: 'user', content, time: currentTime() })
  draft.value = ''
  busy.value = true
  saveCurrentSession()
  await scrollToEnd()
  try {
    const result = await requestChat(identity, content)
    identity.conversationId = result.conversation_id || identity.conversationId
    messages.value.push({
      id: crypto.randomUUID(), role: 'assistant', content: result.answer || '你的消息已经收到。',
      result, time: currentTime()
    })
    serviceOnline.value = true
  } catch (error) {
    messages.value.push({
      id: crypto.randomUUID(), role: 'assistant', content: '这条消息暂时没有处理完成。',
      error: error.message, time: currentTime()
    })
  } finally {
    busy.value = false
    saveCurrentSession()
    await scrollToEnd()
  }
}

async function handleConfirmation(item, action) {
  if (busy.value) return
  busy.value = true
  try {
    const result = await confirmRequest(item.result.request_id, action)
    item.result = result
    item.content = result.answer || (action === 'confirm' ? '申请已经提交。' : '已取消提交。')
    item.time = currentTime()
  } catch (error) {
    item.error = error.message
  } finally {
    busy.value = false
    saveCurrentSession()
    await scrollToEnd()
  }
}

function showReferences(sources) {
  activeReferences.value = sources
  referencesOpen.value = true
}

function newConversation() {
  saveCurrentSession()
  identity.conversationId = crypto.randomUUID()
  messages.value = []
  referencesOpen.value = false
}

function selectConversation(session) {
  saveCurrentSession()
  identity.conversationId = session.id
  messages.value = JSON.parse(JSON.stringify(session.messages || []))
  referencesOpen.value = false
  scrollToEnd()
}

function saveCurrentSession() {
  if (!messages.value.length) return
  const firstQuestion = messages.value.find(item => item.role === 'user')?.content || '新对话'
  const record = {
    id: identity.conversationId,
    title: firstQuestion.length > 18 ? `${firstQuestion.slice(0, 18)}…` : firstQuestion,
    updatedAt: currentTime(),
    messages: JSON.parse(JSON.stringify(messages.value))
  }
  const index = sessions.value.findIndex(item => item.id === record.id)
  if (index >= 0) sessions.value.splice(index, 1)
  sessions.value.unshift(record)
  sessions.value = sessions.value.slice(0, 20)
  localStorage.setItem('employee-service.sessions', JSON.stringify(sessions.value))
}

function readSessions() {
  try {
    return JSON.parse(localStorage.getItem('employee-service.sessions') || '[]')
  } catch {
    return []
  }
}

function currentTime() {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date())
}

async function scrollToEnd() {
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
}
</script>
