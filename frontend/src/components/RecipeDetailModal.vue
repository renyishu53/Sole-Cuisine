<script setup lang="ts">
import { ref, watch } from 'vue'
import { Clock3, Drumstick, Droplet, Flame, RotateCcw, Utensils, Wheat, X } from 'lucide-vue-next'
import { api, apiErrorMessage } from '../api'
import type { RecipeDetail } from '../types'

const props = defineProps<{ recipeId: string | null }>()
const emit = defineEmits<{ close: []; 'add-to-plan': [] }>()

const detail = ref<RecipeDetail | null>(null)
const loading = ref(false)
const error = ref('')

const difficultyLabel: Record<string, string> = { easy: '简单', medium: '中等', hard: '困难' }

async function load(id: string) {
  loading.value = true; error.value = ''; detail.value = null
  try {
    detail.value = await api.getRecipe(id)
  }
  catch (reason) { error.value = apiErrorMessage(reason, '菜谱详情加载失败') }
  finally { loading.value = false }
}

function close() { emit('close') }
function retry() { if (props.recipeId) load(props.recipeId) }

watch(() => props.recipeId, (id, _old, onCleanup) => {
  if (id) {
    load(id)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    onCleanup(() => window.removeEventListener('keydown', onKey))
  } else {
    detail.value = null; error.value = ''; loading.value = false
  }
})
</script>

<template>
  <Transition name="recipe-modal">
    <div v-if="recipeId" class="recipe-backdrop" @click.self="close">
      <section class="recipe-dialog" role="dialog" aria-modal="true" aria-label="菜谱详情">
        <!-- 加载态 -->
        <div v-if="loading" class="dialog-state">
          <span class="recipe-spinner" />
          <strong>正在加载菜谱…</strong>
        </div>

        <!-- 错误态 -->
        <div v-else-if="error" class="dialog-state">
          <p>{{ error }}</p>
          <div class="dialog-actions">
            <button class="button secondary" @click="retry"><RotateCcw :size="16" />重试</button>
            <button class="button primary" @click="close">关闭</button>
          </div>
        </div>

        <!-- 详情内容 -->
        <template v-else-if="detail">
          <header class="dialog-hero">
            <div class="dialog-banner">
              <img :src="detail.image_url" :alt="detail.name" />
              <button class="icon-button dialog-close" aria-label="关闭" @click="close"><X :size="18" /></button>
            </div>
            <div class="dialog-head">
              <h2>{{ detail.name }}</h2>
              <div v-if="detail.tags.length" class="tag-row"><i v-for="tag in detail.tags" :key="tag">{{ tag }}</i></div>
              <div class="hero-quick">
                <span><Flame :size="14" />{{ detail.calories }} kcal</span>
                <span><Clock3 :size="14" />{{ detail.prep_time }} 分钟</span>
                <span><Utensils :size="14" />{{ detail.servings }} 人份</span>
                <span>{{ difficultyLabel[detail.difficulty] || detail.difficulty }}</span>
              </div>
            </div>
          </header>

          <div class="dialog-body">
            <div class="dialog-cols">
              <!-- 食材清单 -->
              <section class="dialog-block">
                <h3><Utensils :size="16" />食材清单</h3>
                <ul v-if="detail.ingredients.length" class="ingredient-list">
                  <li v-for="(item, i) in detail.ingredients" :key="i">
                    <span>{{ item.name }}</span><b>{{ item.amount }}</b>
                  </li>
                </ul>
                <p v-else class="empty-line">暂无食材明细</p>
              </section>

              <!-- 营养信息 -->
              <section class="dialog-block">
                <h3><Flame :size="16" />营养信息</h3>
                <div class="nutrition-grid">
                  <div class="nutrition-item"><span class="nutrition-icon orange"><Flame :size="16" /></span><div><span>热量</span><strong>{{ detail.nutrition.calories }} kcal</strong></div></div>
                  <div class="nutrition-item"><span class="nutrition-icon sage"><Drumstick :size="16" /></span><div><span>蛋白质</span><strong>{{ detail.nutrition.protein }} g</strong></div></div>
                  <div class="nutrition-item"><span class="nutrition-icon blue"><Wheat :size="16" /></span><div><span>碳水</span><strong>{{ detail.nutrition.carbs }} g</strong></div></div>
                  <div class="nutrition-item"><span class="nutrition-icon"><Droplet :size="16" /></span><div><span>脂肪</span><strong>{{ detail.nutrition.fat }} g</strong></div></div>
                </div>
              </section>
            </div>

            <!-- 做法步骤 -->
            <section class="dialog-block steps-block">
              <h3>做法步骤</h3>
              <ol v-if="detail.steps.length" class="step-list">
                <li v-for="(step, i) in detail.steps" :key="i"><i>{{ i + 1 }}</i><p>{{ step }}</p></li>
              </ol>
              <p v-else class="empty-line">暂无做法步骤</p>
            </section>
          </div>

          <footer class="dialog-footer">
            <span class="dialog-spacer" />
            <button class="button secondary" @click="close">关闭</button>
            <button class="button primary" @click="emit('add-to-plan')">加入本周计划</button>
          </footer>
        </template>
      </section>
    </div>
  </Transition>
</template>

<style scoped>
/* ── 遮罩（沿用 .dialog-backdrop 模式） ── */
.recipe-backdrop {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(27, 38, 33, .52);
  display: grid; place-items: center;
  padding: 20px;
}

.recipe-dialog {
  width: min(720px, 100%);
  max-height: calc(100dvh - 40px);
  overflow: auto;
  background: var(--surface);
  border-radius: var(--radius-lg);
  box-shadow: 0 24px 70px rgba(20, 35, 29, .24);
  display: flex; flex-direction: column;
}

/* ── 进出动画 ── */
.recipe-modal-enter-active { transition: opacity .18s var(--ease-out-expo); }
.recipe-modal-leave-active { transition: opacity .15s ease; }
.recipe-modal-enter-active .recipe-dialog { transition: transform .26s var(--ease-spring), opacity .2s var(--ease-out-expo); }
.recipe-modal-leave-active .recipe-dialog { transition: transform .16s ease, opacity .14s ease; }
.recipe-modal-enter-from, .recipe-modal-leave-to { opacity: 0; }
.recipe-modal-enter-from .recipe-dialog { transform: translateY(12px) scale(.985); opacity: 0; }
.recipe-modal-leave-to .recipe-dialog { transform: translateY(6px) scale(.99); opacity: 0; }

/* ── 加载 / 错误态 ── */
.dialog-state {
  padding: 64px 24px; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 14px;
  color: var(--muted); font-size: var(--font-sm);
}
.dialog-state strong { color: var(--text); font-size: var(--font-md); }
.recipe-spinner {
  width: 28px; height: 28px;
  border: 3px solid #e0e8e4; border-top-color: var(--primary);
  border-radius: 50%; animation: spin .7s linear infinite; display: inline-block;
}
.dialog-actions { display: flex; gap: 9px; }

/* ── 顶部：大图 ── */
.dialog-banner {
  position: relative;
  height: 180px;
  background: #edf1ef;
  overflow: hidden;
}
.dialog-banner img { width: 100%; height: 100%; object-fit: cover; display: block; }
.recipe-category {
  position: absolute; left: 14px; top: 14px;
  font-size: var(--font-xs); font-weight: 700;
  color: #fff; background: rgba(47, 125, 104, .9);
  padding: 3px 9px; border-radius: var(--radius-sm);
}
.dialog-close {
  position: absolute; right: 12px; top: 12px;
  background: rgba(255, 255, 255, .92);
}
.dialog-close:hover { background: #fff; }
.dialog-head { padding: 16px 22px 18px; border-bottom: 1px solid var(--line); }
.dialog-head h2 { font-size: var(--font-xl); margin: 0 0 10px; color: var(--text); }
.hero-quick {
  display: flex; flex-wrap: wrap; gap: 14px;
  margin-top: 10px; color: var(--muted); font-size: var(--font-sm);
}
.hero-quick span { display: inline-flex; align-items: center; gap: 4px; }

/* ── 主体 ── */
.dialog-body { padding: 18px 22px; display: flex; flex-direction: column; gap: 18px; }
.dialog-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 22px; }
.dialog-block h3 {
  font-size: var(--font-md); margin: 0 0 12px;
  display: inline-flex; align-items: center; gap: 6px; color: var(--text);
}
.empty-line { font-size: var(--font-sm); color: var(--muted); margin: 0; }

/* 食材清单 */
.ingredient-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.ingredient-list li {
  display: flex; justify-content: space-between; gap: 12px;
  padding: 9px 11px; background: #f7f9f8; border-radius: var(--radius-sm);
  font-size: var(--font-sm);
}
.ingredient-list li span { color: var(--text); }
.ingredient-list li b { font-weight: 700; color: var(--primary); }

/* 营养信息 */
.nutrition-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.nutrition-item {
  display: flex; align-items: center; gap: 10px;
  padding: 12px; border: 1px solid var(--line); border-radius: var(--radius-md);
}
.nutrition-icon {
  width: 34px; height: 34px; border-radius: var(--radius-sm);
  display: grid; place-items: center; flex: none;
  background: #eef1ef; color: var(--muted);
}
.nutrition-icon.orange { background: #faece6; color: var(--orange); }
.nutrition-icon.sage { background: #edf2e5; color: var(--sage); }
.nutrition-icon.blue { background: #e8f0f6; color: var(--blue); }
.nutrition-item div span { display: block; font-size: var(--font-xs); color: var(--muted); }
.nutrition-item div strong { display: block; font-size: var(--font-md); color: var(--text); margin-top: 2px; }

/* 做法步骤 */
.steps-block { border-top: 1px solid var(--line); padding-top: 18px; }
.step-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
.step-list li { display: grid; grid-template-columns: 28px 1fr; gap: 12px; align-items: start; }
.step-list li i {
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--primary); color: #fff;
  display: grid; place-items: center;
  font-style: normal; font-size: var(--font-xs); font-weight: 700;
}
.step-list li p { margin: 0; font-size: var(--font-base); color: var(--text); line-height: 1.6; padding-top: 3px; }

/* ── 底部按钮 ── */
.dialog-footer {
  display: flex; align-items: center; gap: 9px;
  padding: 16px 22px; border-top: 1px solid var(--line);
  position: sticky; bottom: 0; background: var(--surface);
}
.dialog-spacer { flex: 1; }

/* ── 移动端：全屏从底部滑入 ── */
@media (max-width: 560px) {
  .recipe-backdrop { padding: 0; align-items: flex-end; }
  .recipe-dialog { width: 100%; max-height: 92dvh; border-radius: 16px 16px 0 0; }
  .recipe-modal-enter-from .recipe-dialog { transform: translateY(100%); }
  .recipe-modal-leave-to .recipe-dialog { transform: translateY(100%); }
  .dialog-cols { grid-template-columns: 1fr; }
  .dialog-banner { height: 150px; }
  .dialog-head, .dialog-body, .dialog-footer { padding-left: 16px; padding-right: 16px; }
}
</style>
