# ADR 0003 — 前端专题页增量迁移到 Astro

**状态**：已接受
**日期**：2026-09-30

32 个专题页(rates/credit/liquidity/assets/…)样板重复,靠 `sync_pages_head.py` + pytest 纪律测试当补丁维持一致性。决定:专题页增量迁移到 **Astro**(static → frontend/,先整体进 public/ 保证 URL 不变,再逐页改 .astro);主站 SPA(index.html 四视图,交互最重且无样板痛点)保留 vanilla,只预留并入口子。数据流不动:export_pages.py 继续预渲染 JSON,页面运行时 fetch 同路径——明确排除「Astro build 时注入数据」(数据日更、代码不日更,注入会把部署和数据耦合死)和「运行时动态算」(违背纯静态)。

选 Astro 是因为它是唯一不强制 UI 框架的候选:现有 echarts-theme.js / RE_LEGEND / rates-common.js 原样进 island,图表代码零重写;`.astro` 模板≈HTML,单人 Python 维护者成本最低;产物纯静态,Cloudflare Pages 部署方式不变。

## Considered Options

- **Nuxt + vue-echarts / Next.js 整站重写**:wrapper 质量高/生态大,但对「zoom/tooltip/tab」的交互深度是过度设计,图表层全重写,单人维护负担最大。Next 静态导出属二等公民且 CF 部署最折腾。
- **保持 vanilla + esbuild 最小构建**:零 Node 依赖,但解决不了样板重复,属续命。

## Consequences

- Python 仓引入 Node 工具链:frontend/ 下 package.json + npm lock,.nvmrc 钉 Node 版本;deploy-pages.yml 加 setup-node + astro build(构建链变为 export_pages 产 JSON → astro build → wrangler deploy dist,+1~2 分钟)。
- `PAGES_BASE` / `_PATH_PREFIXES` 路径改写机制删除(CF 根路径后已空转,Astro base 接管);SITE_NAV 改造为 ESM 单文件,Astro Layout 构建期 import 渲染导航,SPA 运行时 import 同一份——跨语言 pytest 交叉校验(test_site_nav_paths_in_export_prefixes)随之退役。
- 迁移顺序按同构族:liquidity(8,最同构)→ rates(4)→ 单页族 → assets(11,最杂)殿后。页面从 public/ 删除后自动退出 pytest/sync_pages_head 管辖(搬走即退役),最后一批迁完删除 sync_pages_head.py 及相关用例;test_legend_sync 与 R.asOf 约定永久存活。
- 共享 JS(echarts-theme/rates-common/macro-common)留在 public/js/ 不进打包管线,与未迁移页及 SPA 共用同一份,直到全站迁完再议收编。
- 先 spike credit 页(288 行内联脚本,中等复杂度)验收后才做全量搬运;硬闸:迁移不得改动共享 JS。
