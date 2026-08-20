<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookOpen, Database, FileText, Network, Play, Plus, Search, Trash2, UploadCloud, Zap, X, RefreshCw } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useResource } from '../composables/useResource'
import { useToast } from '../composables/useToast'
import type { AIServiceStatus, KnowledgeSearchResponse, RagEvalResponse, SyncConsistencyResponse } from '../types'

const { data, loading, error, load } = useResource(api.knowledge)
const { show: showToast } = useToast()
const query = ref('孩子不吃辣，周三要快手，推荐什么晚餐？')
const category = ref('营养')
const searching = ref(false)
const uploading = ref(false)
const bootstrapping = ref(false)
const actionError = ref('')
const result = ref<KnowledgeSearchResponse | null>(null)
const serviceStatus = ref<AIServiceStatus | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const smokeTesting = ref(false)
const smokeMessage = ref('')
const statusRefreshing = ref(false)

const evalLoading = ref(false)
const evalResult = ref<RagEvalResponse | null>(null)
const evalError = ref('')
const syncLoading = ref(false)
const syncReport = ref<SyncConsistencyResponse | null>(null)
const syncError = ref('')

const totalChunks = computed(() => data.value?.reduce((sum, doc) => sum + doc.chunks, 0) ?? 0)
const totalDocs = computed(() => data.value?.length ?? 0)

// 目标取向英文词表（bulk/cut/maintain）→ 中文展示标签
const GOAL_LABELS: Record<string, string> = { bulk: '增肌', cut: '减脂', maintain: '健康维护' }
function goalLabel(value?: string) { return value ? (GOAL_LABELS[value] ?? value) : '' }
function metaTags(hit: { goal_type?: string; meal_time?: string; nutrition_focus?: string }) {
  const tags: string[] = []
  const goal = goalLabel(hit.goal_type)
  if (goal) tags.push(goal)
  if (hit.meal_time && hit.meal_time !== '通用') tags.push(hit.meal_time)
  if (hit.nutrition_focus && hit.nutrition_focus !== '均衡') tags.push(hit.nutrition_focus)
  return tags
}

async function refreshStatus() {
  statusRefreshing.value = true
  try { serviceStatus.value = await api.aiStatus() } catch { serviceStatus.value = null }
  finally { statusRefreshing.value = false }
}

function clearSearch() {
  result.value = null
  actionError.value = ''
}

async function search() {
  if (query.value.trim().length < 2 || searching.value) return
  searching.value = true; actionError.value = ''
  try { result.value = await api.searchKnowledge(query.value.trim()) }
  catch (reason) { actionError.value = apiErrorMessage(reason, '知识检索失败，请稍后重试或联系管理员') }
  finally { searching.value = false }
}

async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true; actionError.value = ''
  try { await api.uploadKnowledge(file, category.value); await Promise.all([load(), refreshStatus()]); showToast('文档上传成功', 'success') }
  catch (reason) { actionError.value = apiErrorMessage(reason, '文档入库失败，请稍后重试') }
  finally { uploading.value = false; if (fileInput.value) fileInput.value.value = '' }
}

async function bootstrap() {
  bootstrapping.value = true; actionError.value = ''
  try { await api.bootstrapKnowledge(); await Promise.all([load(), refreshStatus()]); showToast('知识库初始化完成', 'success') }
  catch (reason) { actionError.value = apiErrorMessage(reason, '知识库初始化失败，请稍后重试') }
  finally { bootstrapping.value = false }
}

async function removeDocument(id: string | number) {
  actionError.value = ''
  try {
    await api.deleteKnowledge(id)
    showToast('文档已删除', 'success')
    await Promise.all([load(), refreshStatus()])
  } catch (reason) {
    showToast(apiErrorMessage(reason, '删除文档失败'), 'error')
  }
}

async function smokeTest() {
  smokeTesting.value = true; smokeMessage.value = ''
  try {
    const response = await api.llmSmoke()
    smokeMessage.value = `模型响应正常 · ${response.latency_ms}ms · ${response.message}`
    showToast(`AI 模型连通正常 (${response.latency_ms}ms)`, 'success')
    await refreshStatus()
  } catch (reason) { smokeMessage.value = apiErrorMessage(reason, 'AI 模型连通测试失败'); showToast(smokeMessage.value, 'error') }
  finally { smokeTesting.value = false }
}

onMounted(refreshStatus)

async function runEval() {
  if (evalLoading.value) return
  evalLoading.value = true; evalError.value = ''
  try { evalResult.value = await api.ragEval(4) }
  catch (reason) { evalError.value = apiErrorMessage(reason, '检索质量评测失败，请稍后重试') }
  finally { evalLoading.value = false }
}

async function loadSync() {
  if (syncLoading.value) return
  syncLoading.value = true; syncError.value = ''
  try { syncReport.value = await api.ragSync() }
  catch (reason) { syncError.value = apiErrorMessage(reason, '同步一致性检查失败，请稍后重试') }
  finally { syncLoading.value = false }
}
</script>

<template>
  <AsyncState :loading="loading" :error="error" @retry="load">
    <div v-if="data" class="knowledge-layout">
      <section class="panel">
        <div class="section-toolbar inner knowledge-toolbar">
          <div>
            <h2>知识文档</h2>
            <p>{{ totalDocs }} 份文档 · {{ totalChunks }} 个知识片段<span v-if="serviceStatus" class="status-badge" :class="serviceStatus.rag_enabled ? 'success' : 'warning'">{{ serviceStatus.rag_enabled ? '智能检索已启用' : '智能检索未就绪' }}</span></p>
          </div>
          <div class="toolbar-group">
            <select v-model="category" aria-label="文档分类">
              <option>营养</option><option>菜谱</option><option>食材</option><option>采购</option><option>口味偏好</option>
            </select>
            <button class="button" :disabled="bootstrapping" @click="bootstrap"><Database :size="16" />{{ bootstrapping ? '初始化中' : '初始化知识' }}</button>
            <button class="button primary" :disabled="uploading" @click="fileInput?.click()"><UploadCloud :size="17" />{{ uploading ? '解析入库中' : '上传文档' }}</button>
            <input ref="fileInput" class="sr-only" type="file" accept=".md,.txt,.pdf" @change="upload" />
          </div>
        </div>


        <div v-if="data.length" class="document-table">
          <div class="table-head"><span>文档</span><span>分类</span><span>索引状态</span><span>片段</span></div>
          <article v-for="doc in data" :key="doc.id">
            <span class="file-icon"><FileText :size="19" /></span>
            <span><strong>{{ doc.name }}</strong><small>更新于 {{ doc.updated_at }}</small></span>
            <i>{{ doc.category }}</i>
            <span class="status success">可检索</span>
            <b>{{ doc.chunks }}</b>
            <button class="icon-button danger" title="删除文档" aria-label="删除文档" @click="removeDocument(doc.id)"><Trash2 :size="15" /></button>
          </article>
        </div>
        <button v-else class="drop-zone" @click="fileInput?.click()"><Plus :size="21" /><span><strong>添加第一份营养知识</strong><small>支持 Markdown、PDF 和 TXT，上传后自动解析并建立索引</small></span></button>
        <p v-if="actionError" class="knowledge-error">{{ actionError }}</p>
      </section>

      <aside class="panel retrieval-test">
        <div class="retrieval-title"><span class="metric-icon blue"><Search /></span><div><h3>知识检索测试</h3><p>同时检索饮食图谱与营养知识文档</p></div></div>
        <label>测试问题<textarea v-model="query" rows="5" /></label>
        <button class="button primary full" :disabled="searching || query.trim().length < 2" @click="search"><Play :size="16" />{{ searching ? '检索中' : '运行检索' }}</button>
        <div v-if="result" class="retrieval-results">
          <span class="eyebrow">关系 {{ result.graph_hits.length }} 条 · 文档 {{ result.vector_hits.length }} 条 · {{ result.elapsed_ms }}ms</span>
          <article v-for="(hit, index) in result.graph_hits" :key="`graph-${index}`"><strong><Network :size="15" />{{ hit.subject }} · {{ hit.relation }}</strong><p>{{ hit.target }} <span v-if="hit.detail">· {{ hit.detail }}</span></p></article>
          <article v-for="hit in result.vector_hits" :key="`${hit.document_id}-${hit.chunk_index}`"><strong><BookOpen :size="15" />{{ hit.document_name }}</strong><p>{{ hit.content }}</p><div v-if="metaTags(hit).length" class="hit-tags"><span v-for="tag in metaTags(hit)" :key="tag" class="hit-tag">{{ tag }}</span><span v-if="hit.allergens" class="hit-tag warn">忌口：{{ hit.allergens }}</span></div><small>相似度 {{ hit.score.toFixed(3) }} · 片段 {{ hit.chunk_index }}</small></article>
          <p v-if="!result.graph_hits.length && !result.vector_hits.length" class="empty-result">没有召回结果，请先初始化或上传知识文档。</p>
        </div>
      </aside>

      <section class="panel retrieval-quality">
        <div class="section-toolbar inner knowledge-toolbar">
          <div>
            <h2>检索质量与同步监控</h2>
            <p>离线评测检索召回质量，并检查知识库与关系图谱的一致性。</p>
          </div>
          <div class="toolbar-group">
            <button class="button" :disabled="evalLoading" @click="runEval"><Zap :size="16" />{{ evalLoading ? '评测中' : '运行检索评测' }}</button>
            <button class="button" :disabled="syncLoading" @click="loadSync"><Database :size="16" />{{ syncLoading ? '检查中' : '同步一致性检查' }}</button>
          </div>
        </div>

        <div v-if="evalResult" class="eval-summary">
          <div class="metric-card">
            <span class="metric-value">{{ (evalResult.mean_recall_at_k * 100).toFixed(1) }}%</span>
            <span class="metric-label">平均 Recall@{{ evalResult.top_k }}</span>
          </div>
          <div class="metric-card">
            <span class="metric-value">{{ (evalResult.mean_ndcg_at_k * 100).toFixed(1) }}%</span>
            <span class="metric-label">平均 nDCG@{{ evalResult.top_k }}</span>
          </div>
          <div class="metric-card">
            <span class="metric-value">{{ evalResult.case_count }}</span>
            <span class="metric-label">评测用例数</span>
          </div>
        </div>
        <p v-if="evalError" class="knowledge-error">{{ evalError }}</p>
        <div v-if="evalResult" class="eval-cases">
          <article v-for="(item, index) in evalResult.results" :key="`eval-${index}`">
            <strong>{{ item.query }}</strong>
            <p>Recall@{{ evalResult.top_k }} {{ (item.recall_at_k * 100).toFixed(0) }}% · nDCG@{{ evalResult.top_k }} {{ (item.ndcg_at_k * 100).toFixed(0) }}%</p>
            <small v-if="item.hit_document_names.length">命中文档：{{ item.hit_document_names.join('、') }}</small>
            <small v-if="item.hit_entity_kinds.length">命中实体类型：{{ item.hit_entity_kinds.join('、') }}</small>
          </article>
        </div>

        <div v-if="syncReport" class="sync-report" :class="syncReport.consistent ? 'ok' : 'warn'">
          <strong>{{ syncReport.consistent ? '索引一致' : '存在偏差' }}</strong>
          <span>知识库 {{ syncReport.vector_documents }} 文档 / {{ syncReport.vector_chunks }} 片段 · 图谱 {{ syncReport.neo4j_documents }} 文档 / {{ syncReport.neo4j_entities }} 实体</span>
          <ul>
            <li v-for="(note, index) in syncReport.notes" :key="`note-${index}`">{{ note }}</li>
          </ul>
        </div>
        <p v-if="syncError" class="knowledge-error">{{ syncError }}</p>
      </section>
    </div>
  </AsyncState>
</template>

<style scoped lang="scss">
.retrieval-quality {
  margin-top: 18px;
}

.eval-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 14px 0;

  .metric-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 14px 16px;
    border-radius: 14px;
    background: rgba(70, 112, 93, 0.08);
    border: 1px solid rgba(70, 112, 93, 0.18);
  }

  .metric-value {
    font-size: var(--font-2xl, 24px);
    font-weight: 700;
    color: #2F7D68;
  }

  .metric-label {
    font-size: var(--font-xs, 12px);
    color: #5b6b62;
  }
}

.eval-cases {
  display: grid;
  gap: 10px;

  article {
    padding: 12px 14px;
    border-radius: 12px;
    background: rgba(0, 0, 0, 0.02);
    border: 1px solid rgba(0, 0, 0, 0.06);

    strong {
      font-size: var(--font-base, 14px);
    }

    p {
      margin: 4px 0;
      font-size: var(--font-sm, 13px);
      color: #5b6b62;
    }

    small {
      display: block;
      font-size: var(--font-xs, 12px);
      color: #7a8a80;
    }
  }
}

.hit-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 4px 0;

  .hit-tag {
    padding: 2px 8px;
    border-radius: 999px;
    font-size: var(--font-xs, 12px);
    color: #2F7D68;
    background: rgba(47, 125, 104, 0.1);

    &.warn {
      color: #b0721b;
      background: rgba(190, 130, 40, 0.12);
    }
  }
}

.sync-report {
  margin-top: 14px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.08);

  &.ok {
    background: rgba(70, 112, 93, 0.08);
    border-color: rgba(70, 112, 93, 0.25);
  }

  &.warn {
    background: rgba(190, 130, 40, 0.08);
    border-color: rgba(190, 130, 40, 0.3);
  }

  strong {
    display: block;
    font-size: var(--font-md, 15px);
    margin-bottom: 4px;
  }

  span {
    font-size: var(--font-sm, 13px);
    color: #5b6b62;
  }

  ul {
    margin: 8px 0 0;
    padding-left: 18px;

    li {
      font-size: var(--font-xs, 12px);
      color: #7a8a80;
    }
  }
}
</style>
