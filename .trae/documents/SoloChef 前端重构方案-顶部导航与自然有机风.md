# SoloChef 前端重构方案 — 顶部导航栏 + 自然有机风(Organic Biophilic)

> 范围:外壳 + 全局组件 + 关键页美化(通过全局 class 增强,不改动视图数据绑定)
> 视觉方向:Organic Biophilic(温暖鼠尾草绿 / 米白燕麦底 / 陶土橙点缀,大圆角,柔和阴影)
> 约束:沿用现有代码逻辑与风格;保持 CSS 变量体系;所有 API 集成零影响;响应式 1100/820/560 断点

---

## 一、当前状态分析

### 1.1 架构现状(已读源码确认)
- **布局**:`AppShell.vue` 采用 `.app-shell{display:grid;grid-template-columns:236px minmax(0,1fr)}`,左侧固定侧边栏 + 右侧 workspace
- **顶栏**:`.topbar` 高 84px,sticky,仅含 `page-heading`(标题+描述+移动端汉堡按钮)与 `top-actions`(图标按钮+头像)
- **导航**:9 个导航项在 `.side-nav`(侧边栏垂直),移动端另有 `.mobile-tabs`(底部 5 项)+ 抽屉式 sidebar
- **样式**:`main.scss` 单文件集中所有全局样式 + `rag.scss`(知识库覆盖),使用 CSS 变量体系(`--space-*`/`--font-*`/`--ease-*`/`--primary` 等)
- **API**:`api.ts` 完全独立(axios + 拦截器 + token 刷新),视图通过 `useResource` + `AsyncState` 解耦,与布局无耦合
- **路由**:`router.ts` 11 路由,lazy 加载 + chunk 错误重试,保持不动
- **状态**:`stores/app.ts` 的 `sidebarOpen` 直接复用为移动端抽屉开关
- **构建**:`vite.config.ts` 仅含 plugin + proxy,无 chunk 分割(性能优化点)

### 1.2 关键硬约束(源码确认)
1. **顶栏高度必须 84px**:`.planner-layout` 与 `.chat-workspace` 均使用 `height:calc(100dvh - 152px)`,其中 152px = topbar(84) + page-content 上下 padding(24+44)。改变 topbar 高度会破坏 PlannerView/ChatView 布局
2. **CSS 变量体系不可破坏**:spacing(`--space-xs`~`--space-xl`)、typography(`--font-xs` 12 ~ `--font-2xl` 24)、easing(`--ease-out-expo` 等)必须沿用;字号必须引用 `--font-*`,禁止 8-11px 硬编码
3. **`prefers-reduced-motion` 必须尊重**(已有,保留)
4. **API 集成零影响**:`api.ts`/`composables/*`/`router.ts`/视图数据绑定均不改动

### 1.3 改动文件清单(共 4 个,最小化)
| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `frontend/src/components/AppShell.vue` | 模板重构 | sidebar→顶部双行导航,保留移动抽屉+底部 tab |
| `frontend/src/styles/main.scss` | 样式重写 | 调色板 + 顶栏布局 + 全局组件美化 + 响应式 |
| `frontend/vite.config.ts` | 增量配置 | build chunk 分割(性能优化) |
| `frontend/src/styles/rag.scss` | 微调 | 同步调色板变量引用(仅 2-3 处硬编码色值) |

> 视图文件(11 个)**不改动模板与数据绑定**;关键页美化通过全局 class(`.panel`/`.metric-card`/`.welcome-band`/`.chart-panel` 等)增强 + CSS 伪元素实现,自然级联到 Dashboard/Planner/Feedback。

---

## 二、Organic Biophilic 设计系统

### 2.1 调色板演进(保留绿色品牌主色,暖化背景与边框)
```scss
:root {
  /* 主色 — 保留品牌鼠尾草绿 */
  --primary:#3a7d6b; --primary-dark:#2d6253; --primary-light:#eaf3ec;
  --primary-soft:#f2f7f3;          /* 新增:超浅鼠尾草洗色,用于悬停/芯片底 */
  /* 文本 — 暖化炭灰(带绿底调) */
  --text:#2b322a; --muted:#6f786c;
  /* 边框/底色 — 暖米色系 */
  --line:#e6e3d8; --line-soft:#efece2; --surface:#fff;
  --canvas:#f6f4ec;                /* 新增:燕麦暖白画布(替代 #f7f8f5) */
  /* 强调色 — 保留 */
  --orange:#d97757; --blue:#5b8db8; --sage:#8baa63; --red:#c2413d;
  /* 圆角 — 更有机,放大 */
  --radius-sm:8px; --radius-md:12px; --radius-lg:18px;
  --radius-pill:999px;             /* 新增:导航胶囊 */
  /* 阴影 — 暖色调,多层柔和 */
  --shadow-sm:0 1px 2px rgba(45,52,40,.05),0 1px 3px rgba(45,52,40,.06);
  --shadow-md:0 4px 12px rgba(45,52,40,.07),0 2px 4px rgba(45,52,40,.04);
  --shadow-lg:0 12px 32px rgba(45,52,40,.10),0 4px 12px rgba(45,52,40,.06);
  --shadow-glow:0 0 0 4px rgba(58,125,107,.12);  /* 新增:聚焦/激活光晕 */
  /* spacing / typography / easing — 完全保留不变 */
}
```
- `body{background:var(--canvas)}` 替代硬编码 `#f7f8f5`
- 现有 `--font-*`/`--space-*`/`--ease-*` 全部保留

### 2.2 视觉风格关键词
- **Soft Glassmorphism 顶栏**:`backdrop-filter:blur(16px) saturate(1.2)` + 暖色半透明底
- **Pill Nav 胶囊导航**:激活项 `--primary` 实心 + 柔和投影;非激活透明底悬停浮 `--primary-soft`
- **有机圆形装饰**:`.welcome-band::before` 径向渐变光斑、`.plate` 双层径向渐变
- **大圆角卡片**:`--radius-md`(12)/`--radius-lg`(18)提升亲和力
- **暖色多层阴影**:替代冷灰阴影,营造温润质感

---

## 三、详细改动方案

### 3.1 `AppShell.vue` — 模板重构

**结构**:桌面双行顶栏(50px 导航行 + 34px 标题行 = 84px);移动端单行 72px + 左侧抽屉 + 底部 tab。

```html
<template>
  <div class="app-shell">
    <div v-if="store.sidebarOpen" class="sidebar-mask" @click="store.closeSidebar" />

    <!-- 移动端抽屉(桌面 display:none) -->
    <aside class="sidebar" :class="{ open: store.sidebarOpen }">
      <!-- 保留原 brand + side-nav + sidebar-foot 结构(AI 状态 + 退出) -->
    </aside>

    <section class="workspace">
      <header class="topbar">
        <!-- 第 1 行:导航 -->
        <div class="topnav-row">
          <button class="icon-button mobile-menu" @click="store.toggleSidebar"><Menu :size="21" /></button>
          <div class="brand brand-top">
            <div class="brand-mark"><Bot :size="20" /></div>
            <div><strong>SoloChef</strong><span>AI 营养备餐</span></div>
          </div>
          <nav class="topnav" aria-label="主导航">
            <button v-for="item in nav" :key="item.to" :class="{ active: route.path === item.to }" :title="item.label" @click="navigate(item.to)">
              <component :is="item.icon" :size="16" /><span>{{ item.label }}</span><i v-if="item.to === '/planner'">AI</i>
            </button>
          </nav>
          <div class="ai-status-chip" :title="aiStatusText">
            <span class="live-dot" :class="{ muted: !aiStatus }" />
            <span>{{ aiStatus ? 'AI 已连接' : 'AI 待确认' }}</span>
          </div>
          <div class="top-actions">
            <button class="icon-button" title="今日营养" @click="navigate('/')"><Activity :size="18" /></button>
            <button class="icon-button" title="个人设置" @click="navigate('/nutrition')"><Settings :size="18" /></button>
            <button class="icon-button" title="退出登录" @click="logout"><LogOut :size="18" /></button>
            <span class="avatar">{{ store.userName.slice(0,1) }}</span>
          </div>
          <h1 class="mobile-title">{{ title }}</h1>
        </div>
        <!-- 第 2 行:页面标题 -->
        <div class="page-heading-row">
          <div class="page-heading"><h1>{{ title }}</h1><p>{{ description }}</p></div>
        </div>
      </header>

      <main class="page-content">
        <RouterView v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="route.fullPath" />
          </Transition>
        </RouterView>
      </main>
    </section>

    <nav class="mobile-tabs" aria-label="移动端导航"><!-- 保留原 5 项底部 tab --></nav>
    <ToastContainer />
  </div>
</template>
```

**`<script setup>` 增量**:
- 新增 `aiStatusText` computed(汇总 LLM/Redis/Celery 状态文本,供 chip 的 title 提示)
- `nav` 数组、`navigate()`、`loadContext()`、`logout()` 全部保留不变
- 已导入的 `LogOut`/`Menu`/`Activity`/`Settings`/`Bot` 等 icon 全部复用,无需新增依赖

**导航溢出策略**(9 项):
- 桌面:`.topnav{flex:1;overflow-x:auto;scrollbar-width:none}` + 隐藏 webkit 滚动条 → 横向滚动,所有标签可见
- ≤820px:`.topnav{display:none}`,改用抽屉 + 底部 tab

### 3.2 `main.scss` — 样式重写要点

#### (a) 布局层
```scss
.app-shell { display:block; min-height:100dvh }       /* 移除 grid 双列 */
.sidebar { display:none }                              /* 桌面隐藏 */
.workspace { min-width:0 }

.topbar {
  height:84px;                                         /* 保持!不改 152px 计算 */
  display:grid; grid-template-rows:50px 34px;
  background:rgba(246,244,236,.82);
  backdrop-filter:blur(16px) saturate(1.2);
  -webkit-backdrop-filter:blur(16px) saturate(1.2);
  border-bottom:1px solid var(--line);
  box-shadow:0 1px 0 rgba(45,52,40,.03);
  position:sticky; top:0; z-index:30;
  padding:0 var(--space-lg);
  contain:layout style;                                /* 性能:隔离重排 */
}
.topnav-row { display:flex; align-items:center; gap:var(--space-md); min-width:0 }
.topnav { display:flex; align-items:center; gap:2px; flex:1; min-width:0;
          overflow-x:auto; scrollbar-width:none; margin:0 }
.topnav::-webkit-scrollbar { display:none }
.topnav button { flex:none; height:36px; padding:0 12px; border:0; background:transparent;
                 border-radius:var(--radius-pill); display:inline-flex; align-items:center; gap:7px;
                 color:var(--muted); font-size:var(--font-base); font-weight:500; white-space:nowrap;
                 transition:background-color .18s,color .18s,box-shadow .18s }
.topnav button:hover { background:var(--primary-soft); color:var(--primary) }
.topnav button.active { background:var(--primary); color:#fff;
                        box-shadow:0 2px 8px rgba(58,125,107,.28) }
.topnav button.active:focus-visible { outline:2px solid #fff; outline-offset:2px }
.ai-status-chip { flex:none; display:inline-flex; align-items:center; gap:6px; height:30px;
                  padding:0 11px; border-radius:var(--radius-pill); background:var(--primary-soft);
                  border:1px solid var(--primary-light); font-size:var(--font-xs); color:var(--primary); font-weight:600 }
.page-heading-row { display:flex; align-items:center; min-width:0 }
.page-heading { display:flex; align-items:baseline; gap:12px }
.mobile-title { display:none; margin:0; font-size:var(--font-lg); font-weight:700 }
```

#### (b) 移动端响应式(≤820px)
```scss
@media(max-width:820px) {
  .sidebar { display:flex; position:fixed; left:0; top:0; transform:translateX(-100%);
             width:260px; height:100dvh; transition:transform .25s var(--ease-out-expo); z-index:40 }
  .sidebar.open { transform:translateX(0) }
  .sidebar-mask { display:block; position:fixed; inset:0; background:rgba(45,52,40,.32); z-index:35 }
  .sidebar-close,.mobile-menu { display:grid }
  .topnav,.page-heading-row,.ai-status-chip,.brand-top span { display:none }
  .topbar { grid-template-rows:1fr; height:72px; padding:0 var(--space-md) }
  .mobile-title { display:block }
  .page-content { padding:16px 16px 90px }
  .mobile-tabs { display:grid; grid-template-columns:repeat(5,1fr); position:fixed; bottom:0; left:0; right:0;
                 height:66px; background:var(--surface); border-top:1px solid var(--line);
                 padding-bottom:env(safe-area-inset-bottom); z-index:32 }
  /* 保留原 mobile-tabs button 样式 */
}
```
> 其余 1100px/560px 断点规则保留原有结构,仅同步新变量。

#### (c) 全局组件美化(级联到所有视图,含关键页)
```scss
.panel,.metric-card {
  background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-md);
  box-shadow:var(--shadow-sm);
  transition:border-color .2s,box-shadow .2s,transform .15s var(--ease-out-expo);
}
.panel:hover,.metric-card:hover { border-color:#d4dbd0; box-shadow:var(--shadow-md); transform:translateY(-2px) }

.welcome-band {
  min-height:140px; border-radius:var(--radius-lg);
  background:linear-gradient(135deg,#eaf3ec 0%,#f2f7f3 55%,#f6f4ec 100%);
  border:1px solid var(--primary-light); box-shadow:var(--shadow-sm);
  position:relative; overflow:hidden; padding:26px 28px;
}
.welcome-band::before { content:""; position:absolute; right:-60px; top:-60px; width:220px; height:220px;
  border-radius:50%; background:radial-gradient(circle,rgba(58,125,107,.10),transparent 70%); pointer-events:none }

.button.primary,.send-button {
  background:linear-gradient(135deg,var(--primary),var(--primary-dark));
  border-color:var(--primary); box-shadow:0 2px 8px rgba(58,125,107,.22);
}
.button.primary:hover { box-shadow:0 6px 16px rgba(58,125,107,.32); transform:translateY(-1px) }

.metric-icon { border-radius:var(--radius-sm) }
.metric-icon.green { background:linear-gradient(135deg,#eaf3ec,#dde9dd) }
.metric-icon.orange { background:linear-gradient(135deg,#faece6,#f5dccf) }
.plate { background:radial-gradient(circle at 30% 30%,#f0f5ed,#e3eddf) }

.chart-panel { box-shadow:var(--shadow-sm) }              /* FeedbackView 图表容器 */
.graph-mark { background:linear-gradient(135deg,#edf4f1,#dfebe4) }  /* PlannerView 空态 */
```

#### (d) sidebar 抽屉样式保留
- `.brand`/`.side-nav`(垂直 grid)/`.sidebar-foot`/`.ai-status`/`.profile` 样式保留,仅同步新色值变量
- `.side-nav button.active` 改为 `background:var(--primary);color:#fff`(与 topnav 一致)

#### (e) 排版覆盖层
- 现有 `--font-*` 覆盖层(line 142-263)全部保留,不重复引入硬编码小字

### 3.3 `vite.config.ts` — 性能优化
```ts
export default defineConfig({
  plugins: [vue()],
  envDir: '..',
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-echarts': ['echarts'],
          'vendor-utils': ['axios', 'lucide-vue-next', '@vueuse/core'],
        },
      },
    },
  },
})
```
- 将 echarts(~1MB)独立成 chunk,仅 FeedbackView 按需加载,首屏体积显著下降
- vue 全家桶与工具库分离,长期缓存命中率提升

### 3.4 `rag.scss` — 微调
- `i.online{background:#3a7d6b}` → `var(--primary)`(2 处)
- `background:#f7f9f8` 等硬编码底色 → `var(--canvas)` 或 `var(--primary-soft)`(3 处)
- 不改选择器结构,仅变量化

### 3.5 关键页美化说明(不改视图模板)
- **DashboardView**:受益于 `.welcome-band`(有机渐变+光斑)、`.metric-card`(暖阴影+悬停浮起)、`.plate`(径向渐变)、`.panel` 增强 — 全局 class 级联,模板零改动
- **PlannerView**:受益于 `.graph-mark`(渐变)、`.panel`、`.button.primary`(渐变+投影)增强
- **FeedbackView**:受益于 `.chart-panel`、`.panel` 阴影增强 + ECharts 独立 chunk 加速
- 如需额外装饰,通过 CSS `::before/::after` 伪元素实现,**不触碰 `{{ data.* }}` 数据绑定**

---

## 四、API 集成与功能完整性保障

| 检查项 | 保障措施 |
|--------|---------|
| `api.ts` 不受影响 | 完全不改动该文件 |
| 视图数据绑定不受影响 | 11 个视图模板零改动,仅 class 样式增强 |
| `useResource`/`AsyncState` 不受影响 | 不改动 composables/components |
| `router.ts` 路由不变 | 不改动,11 路由 + lazy + chunk 重试保留 |
| 导航 9 项全部可用 | topnav(桌面) + side-nav(移动抽屉) + mobile-tabs(底部) 三套均渲染同一 `nav` 数组 |
| 登出可用 | top-actions 新增 `LogOut` 图标按钮 + 移动抽屉 profile 保留 |
| AI 状态可见 | 桌面 chip(浓缩) + 移动抽屉 sidebar-foot(完整) |
| 顶栏高度兼容 | 84px 不变,`calc(100dvh - 152px)` 计算保持有效 |

---

## 五、性能优化清单
1. **chunk 分割**:echarts/vue/utils 三分离,首屏仅加载必要 vendor
2. **`contain:layout style`** 于 `.topbar`,隔离重排
3. **`backdrop-filter`** 加 `-webkit-` 前缀兼容 Safari
4. **GPU 友好动画**:仅 `transform`/`opacity`/`box-shadow` 参与过渡
5. **`prefers-reduced-motion`** 保留并扩展覆盖新动画
6. **`scrollbar-width:none`** + `::-webkit-scrollbar{display:none}` 隐藏导航滚动条
7. **sticky topbar** 替代固定定位,避免内容遮挡

---

## 六、验证步骤
1. `cd frontend && npm run typecheck` — TypeScript 类型检查通过
2. `npm run build` — 构建成功,观察 chunk 分割日志(echarts 独立)
3. `npm run dev` — 本地启动,逐项验证:
   - 桌面:顶栏双行显示,9 项导航可点切换,激活项胶囊高亮,页面标题正确
   - 缩至 1100px:导航横向滚动,标签仍可见
   - 缩至 820px:顶栏变单行,汉堡按钮唤出抽屉,底部 tab 显示
   - 缩至 560px:metric 网格单列,welcome-band 堆叠
4. 功能回归:依次访问 11 路由,确认数据正常加载(API 不受影响)
5. PlannerView / ChatView:确认 `calc(100dvh - 152px)` 布局未被破坏(全屏双栏高度正常)
6. `npm run test` — 既有 vitest 用例通过(useResource/useToast/api 不受影响)
7. 键盘可访问性:Tab 遍历导航,焦点可见;`prefers-reduced-motion` 下动画停用

---

## 七、假设与决策
- **保留绿色主色**:品牌延续性 + 健康主题契合,仅暖化背景与边框
- **不改视图模板**:关键页美化通过全局 class + 伪元素实现,确保 API 集成零风险
- **顶栏高度 84px 不变**:保护 PlannerView/ChatView 的 `calc(100dvh-152px)` 计算
- **导航三套并存**:topnav(桌面)/side-nav(移动抽屉)/mobile-tabs(底部),与现有 mobile-tabs 模式一致,通过 CSS 控制显隐
- **`sidebarOpen` 状态复用**:无需新增 store 字段
- **rag.scss 仅变量化**:不改选择器,避免知识库页样式回归
