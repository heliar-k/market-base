# React 迁移计划(占位决策记录)

> 状态:**未立项**——本文是「什么时候动、动什么、怎么动」的预先决策,不是排期。
> 由来:2026-09 Astro 迁移(ADR-0003)+ 两期偷脸换肤收官时,维护者明确「后续要改 React
> 的时候再弄」,为避免到时重新踩一遍决策,把当时的思考落盘。
> 前置阅读:`docs/adr/0003-frontend-astro-incremental-migration.md`(当时否掉整站重写的理由)、
> `CONTEXT.md`「前端」节(主站 SPA / 专题页 / island / 预渲染数据术语)。

## 一、为什么当时没上 React(ADR-0003 的决策语境)

- 站点本质是「静态壳 + ECharts island」:数据日更、交互以 zoom/tooltip/tab 为主,
  没有需要组件框架支撑的状态复杂度——Astro 默认零 JS 恰好匹配
- 单人 Python 维护者,引入 React 生态(bundle/状态管理/依赖矩阵)的持续成本 > 收益
- Next.js/Nuxt 整站重写被明确否决(ADR-0003 Considered Options),**该否决至今有效**

## 二、什么信号出现才立项(触发条件)

满足任一,React 化从「过度设计」变「合理」:

1. **交互深度质变**:实时行情流式推送、跨视图复杂状态联动(选中的 symbol/range
   全站同步)、可保存的自定义工作台——vanilla 状态管理开始出现 homemade 框架的味道
2. **SPA 四视图(dashboard/tech/macro/correlation)要大改**:这是全站交互最重的部分,
  也是当时唯一保留 vanilla 的部分(Q1=C 决策)。它若要重做,是 React 的天然落点
3. **组件复用需求跨越页面边界**:现在 TopicLayout + CSS 类复用够用;当出现
  「同一交互组件在多个页面以不同数据源复用且行为有状态」时
4. **要接入 npm 生态的重量级组件**:如 AG Grid 级数据网格、可嵌入的回测/画图工具

仅出现「想让页面更好看」→ 不触发,先调 token(两期偷脸已证明纯 CSS 能走多远)。

## 三、迁移什么、不迁移什么

沿用 ADR-0003 的增量哲学,方向反转:

| 对象 | 决策 |
|---|---|
| 专题页(32 页 .astro) | **不迁 React**。静态壳 + island 的格局不变;.astro 里的 is:inline 脚本继续零重写。React 与 Astro 的结合方式(Astro 4+ 官方 React 集成,岛内挂 React 组件)是局部选项,按页决定 |
| 主站 SPA(四视图) | **唯一候选**。若立项,SPA 壳 + 四视图整体迁 React,Astro 包裹(host 页 .astro 里挂 React island)或独立 Vite app 均可评估 |
| ECharts 层 | 继续原生。不引 echarts-for-react(wrapper 只是 init/dispose 的薄壳,现有 mkChart 体系已做同样的事);React 组件里 useEffect 挂原生 ECharts 即可 |
| 预渲染数据流 | **永不动**(export_pages JSON → 静态 fetch,与框架无关) |
| SITE_NAV 单源 | ESM 文件继续两头吃(Astro 构建期 import + React 运行时 import),已是框架中立的形态 |
| tokens.css / 主题体系 | **直接平移**。CSS 变量在 React 里同样是一等公民,两期偷脸产出的 token(--hover-tint/--card-shadow/--border-strong/亮暗自适应全套)零成本继承;暗色机制(body.dark + theme-changed 事件)原样保留 |
| 共享 JS(echarts-theme/rates-common 等) | ADR-0003 留的「收编进打包管线」口子,与 React 化一起做:进管线后 React/Vanilla 同源 import |

## 四、立项时怎么动(路径草案,到时再细化)

1. **先 spike 一个视图**(建议 correlation 或 dashboard,数据流简单):Astro host 页 +
   React island,验证构建管线(Vite)与现有 CSS 变量体系的无缝度
2. **硬闸照抄 ADR-0003**:预渲染数据流不改、ECharts 原生不换 wrapper、tokens 零重偷
3. **逐视图迁移**,每视图一个验收单元(playwright + preview URL 眼验,模式已成熟)
4. Vue 也是候选:当时调研 Nuxt+vue-echarts 是 wrapper 质量最高的组合,若对 React
   生态无执念,Vue 单文件组件对 Python 维护者可能更友好——立项时重新对比一次

## 五、成本与风险提示(当时视角)

- 引入 React = 引入它的整条工具链(bundler 深度配置/状态方案选型/hooks 心智模型),
  这是 ADR-0003 否决的主因,立项时要能回答「哪个触发条件出现了」
- Astro 官方对 React/Vue/Svelte 集成是成熟路径,风险不在「能不能」而在「值不值」
- 若只是想要**组件化**而非 React 本身: Astro 组件(.astro/.ts)已覆盖无状态组件,
  评估先写 Astro 组件是否够用,再决定要不要 JS 框架

## 六、决策关联

- 本文件 = ADR-0003 的「未来何时重开」附录
- 重开时:新写 ADR(引用本文件),废止本文「未立项」状态,入 `/grill-with-docs` 重新烤
  (触发条件是否真满足、Vue vs React、SPA 迁移范围)
