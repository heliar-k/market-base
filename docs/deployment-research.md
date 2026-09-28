# 静态站托管迁移 + 数据刷新频率提升 调研

> 2026-09-28 · 观澜台（market-base）· 结论优先，所有关键数字附一手来源；标「未核实」的为社区/经验性信息。

## 0. 现状快照（仓库内已核实）

- `site/` 预渲染产物 **94 MB / 268 个文件**，最大单文件 16 MB（`site/api/macro/rates`）。
- 部署链：`daily-fetch`（UTC 21:00）/ `fetch-refresh`（UTC 15:15，~3 min）→ `workflow_run` 触发 `deploy-pages` → `uv sync` + `export_pages` 全量重导出 → `actions/deploy-pages` 发 GitHub Pages。
- 公开仓库 Actions 标准 runner **完全免费**（无分钟数限制）：<https://docs.github.com/en/billing/concepts/product-billing/github-actions>。
- 痛点：盘中会变的数据（资产快照、FOMC 概率/ZQ、期权墙、加密衍生品、新闻）一天只刷一次。

---

## 1. 托管候选对比

### 1.1 GitHub Pages（不迁移行不行？）

| 项 | 值 | 来源 |
|---|---|---|
| 站点大小 | ≤1 GB（源仓库建议 ≤1 GB） | <https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits> |
| 带宽 | soft limit **100 GB/月**，超限可能 429/邮件警告 | 同上 |
| build 频率 | 10 次/小时 soft limit，**但用自定义 Actions workflow 构建发布不受此限** | 同上 |
| 单次部署超时 | 10 分钟 | 同上 |
| 商用限制 | 不得用于商业/SaaS 性质站点（研究平台个人使用没问题） | 同上 |

结论：**容量上完全够**（94 MB ≪ 1 GB）。真正的瓶颈是：① Actions schedule 高峰期延迟（见 §2.1），刷新频率上限约 15-30 分钟级；② 每次部署都要完整跑一遍 `uv sync + export_pages + upload`（数分钟），高频部署浪费且易排队；③ 国内访问时好时坏（未核实，社区共识）。

### 1.2 Cloudflare Pages ★ 推荐

| 项 | 免费层 | 来源 |
|---|---|---|
| 带宽/静态请求 | **无限**（官方产品页：unlimited requests & bandwidth） | <https://www.cloudflare.com/products/pages> |
| Git 集成 build | 500 次/月、1 并发、20 min 超时 | <https://developers.cloudflare.com/pages/platform/limits/> |
| **Direct Upload（wrangler）** | **不计入 build 额度，可无限次部署**（CF 员工社区答复，2023，未核实是否仍无上限） | <https://community.cloudflare.com/t/builds-vs-deployments/494717> · <https://developers.cloudflare.com/pages/get-started/direct-upload/> |
| 文件数/单文件 | 20,000 文件 / 25 MiB（我们 268 文件 / 16 MB，均达标） | limits 页同上 |
| Pages Functions | 计入 Workers 免费额度 10 万请求/天 | limits 页 |
| 预览部署 | 无限（每分支一个 URL） | limits 页 |
| 自定义域名 | 100 个/项目，免费 SSL | limits 页 |

关键机制：wrangler 部署按**内容哈希去重**，未变更文件不重传（CF 员工解释：<https://community.cloudflare.com/t/cloudflare-pages-simple-explanation-of-already-uploaded-when-deploying/582135>）→ 我们「数据没变的 260 个文件」零成本跳过，只传变化的 JSON，秒级完成。

### 1.3 Cloudflare Workers Static Assets

Pages 的继任者（新项目官方推荐 Workers）。静态资产请求**免费且无限、存储不加钱**：<https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/>；免费层 20,000 文件/25 MiB 与 Pages 相同：<https://developers.cloudflare.com/workers/platform/limits/>。区别：自带 Worker（可写 API/定时任务），但无 Pages 的分支预览。**对本项目：Pages 够用；若上 §3 路线 C 的实时层，直接用 Workers 或 Pages Functions 都行。**

### 1.4 Vercel（Hobby）

- 免费层：100 GB 流量/月、1M edge requests；**Hobby 仅限非商用个人项目**：<https://vercel.com/docs/plans/hobby>。
- 部署上限：100 次/天、100 次/小时、60 次/5 分钟：<https://vercel.com/docs/limits>。
- **CLI 上传源码包上限 100 MB（Hobby）**——我们 94 MB 已贴边，数据每天在涨，超限即失败（limits 页同）。
- Cron：Hobby 只允许**每天一次**，小时级/分钟级 cron 部署直接报错：<https://vercel.com/docs/cron-jobs/usage-and-pricing>。
- 官方 KB 承认 `.vercel.app` 在中国大陆「可能被封锁或降速」，建议换自定义域名，且无中国大陆节点：<https://vercel.com/kb/guide/accessing-vercel-hosted-sites-from-mainland-china>。

结论：**不合适**（体积贴边 + Hobby cron 日频 + 商用限制语义模糊）。

### 1.5 Netlify

2025-09 起改为 credit 制：Free = **300 credits/月**（带宽、请求、生产部署、compute 全部从 credits 扣），超限整号暂停到下月：<https://www.netlify.com/pricing/>。免费额度换算不透明、构建类操作消耗 credits，高频部署会烧额度。**不如 Cloudflare 省心，不推荐。**

### 1.6 其他

| 平台 | 免费层 | 判定 | 来源 |
|---|---|---|---|
| Deno Deploy | 1M 请求/月 + **20 GB egress/月** | 94 MB 站高频访问，egress 撑不住；无 Pages 式文件托管 | <https://deno.com/deploy/pricing> |
| AWS Amplify Hosting | 15 GB 流量/月、5 GB 存储、1000 build 分钟（新账号 2025-07 起改为 $100-200 credits 制，12 个月免费层对部分账号已不存在） | 流量额度太小，超额 $0.15/GB | <https://aws.amazon.com/amplify/pricing> · <https://spot.rackspace.com/blog/aws-free-tier> |
| Fly.io | 2024-10 起**取消免费额度**（新用户仅 2 VM 小时试用），纯 PAYG | 不适合静态站 | <https://fly.io/docs/about/discontinued-plans/> |
| Hugging Face Spaces | Static Space 免费，但**自定义域名要 PRO($9/月)**、48h 无访客休眠、非 CDN 定位 | 不适合 | <https://huggingface.co/pricing>（经 klymentiev.com 二手核实，未直接核对官方页） |

### 1.7 中国大陆可达性小结

- GitHub Pages：不用翻墙工具时好时坏（未核实，多年社区共识）。
- `*.vercel.app` / `*.pages.dev`：免费子域被 GFW 针对性干扰的概率高（Vercel 官方 KB 已承认，见 1.4；pages.dev 同理，未核实）。
- **自定义域名 + Cloudflare 代理**：无中国大陆边缘节点（需 Enterprise），但一般可达、延迟 30-80 ms 级（腾讯云社区 2026-06 对比文推荐「国内用户 → Cloudflare Pages」：<https://cloud.tencent.com/developer/article/2689511>，未核实）。
- 实操建议：花 ~¥60/年买个域名托管到 Cloudflare，比任何免费子域都稳。

---

## 2. 刷新频率提升

### 2.1 GitHub Actions schedule 的官方警告

- 最小间隔 **5 分钟**；只在默认分支运行；公开仓库 60 天无活动自动停用。
- **「高峰期（尤其整点）会延迟，负载足够高时排队任务可能被直接丢弃」**——官方原文：<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule>。
- 排队中的 run 若 45 分钟没被 runner 接走会被丢弃：<https://docs.github.com/en/actions/using-github-hosted-runners/using-github-hosted-runners/about-github-hosted-runners>。

→ Actions cron 适合 **≥15 分钟**的节奏（错开整点，如 `*/15` 改成 `7,22,37,52 * * * *`）；5-10 分钟级会周期性漂移。

### 2.2 部署侧瓶颈

- GitHub Pages：自定义 workflow 部署不受 10 builds/h 限制（§1.1），但每次部署 = 全量导出 + 上传 94 MB artifact + 10 min 超时预算，实测链路 ~5-8 min，高频跑浪费。
- Cloudflare Pages Direct Upload：内容哈希增量 + 原子切换，**部署本身秒级、无频率额度**（§1.2）→ 瓶颈只剩 Actions 调度本身。
- 外部触发通道：`repository_dispatch` / `workflow_dispatch` REST API 可从任意 cron 服务触发 Actions（PAT 认证 5000 API 请求/小时，内容生成类 secondary limit ~500/小时，足够）：<https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>。

### 2.3 替代调度器

| 调度器 | 免费额度 | 最小间隔 | 来源 |
|---|---|---|---|
| Cloudflare Workers Cron Triggers | **5 个/账号**（免费），10 ms CPU/次、15 min wall-clock | 1 分钟（`* * * * *`） | <https://developers.cloudflare.com/workers/platform/limits/> · <https://developers.cloudflare.com/workers/configuration/cron-triggers/> |
| Vercel Cron | Hobby 含在免费层 | **仅日频**（Pro 才到分钟级） | <https://vercel.com/docs/cron-jobs/usage-and-pricing> |
| cron-job.org | 免费、任务数无硬上限（fair use） | 1 分钟；超时 30s 判失败（打 GitHub API 够用，返回快） | <https://cron-job.org/en/> · <https://merginit.com/blog/26062026-free-cron-job-scheduler-comparison> |

Worker cron 的典型用法：`scheduled()` 里 `fetch()` GitHub `workflow_dispatch` API → 比 Actions 原生 schedule 准时得多（不受整点排队影响）。

### 2.4 「重预渲染」vs「数据放 KV/R2 + 运行时 fetch」

| | 全站重导出+重部署 | 只更新个别 JSON（增量部署） | 运行时 API（Worker+KV/R2） |
|---|---|---|---|
| 前提 | 现状即是 | **wrangler 哈希去重天然支持**，导出代码零改动 | 前端 fetch 路径改造 + Worker 层 |
| 延迟下限 | ~10-15 min（Actions 链路） | ~10-15 min（同上，但部署秒级） | **分钟级**（Worker 直接写 KV/R2 或前端轮询） |
| 免费额度压力 | 无 | 无 | Functions/KV 计入 Workers 10 万请求/天、KV 10 万读/天（<https://developers.cloudflare.com/kv/platform/limits/>）；**R2 更宽**：10M 读/月 + 10GB 存储 + egress 免费（<https://developers.cloudflare.com/r2/pricing/>） |
| 工作量 | 0 | ~10 行 workflow 改动 | 中：新 Worker + 前端 + Actions 写 R2 步骤 |

94 MB 全站重传在 GitHub Pages 上是真成本，在 Cloudflare 上不是（哈希增量）。所以**高频化第一步不需要上 KV/R2**，只需要换部署通道。

---

## 3. 推荐路线

### 路线 A：留在 GitHub Pages，加密 cron（零迁移）

- 做法：加一个 `fast-refresh` workflow（错开整点的 `*/15` 或 `*/30`，只跑 fred/cboe/yfinance/rate_expectations 等盘中源），沿用现有 `workflow_run → deploy-pages`。
- 成本 ¥0；工作量：1 个新 workflow 文件。
- 频率上限：~15-30 min（受 §2.1 调度延迟 + 每次全量导出/上传 94 MB 拖累）。
- 国内：维持现状（时好时坏）。
- 风险：Actions 高峰期延迟/丢弃；Pages soft 带宽 100 GB/月；部署频率上去后 workflow_run 链变长（fetch→deploy 串行 ~10 min）。

### 路线 B：迁 Cloudflare Pages（wrangler Direct Upload）+ 15 min cron ★ 推荐

- 做法：`deploy-pages.yml` 末尾三步（configure/upload/deploy-pages）换成 `cloudflare/wrangler-action` + `CLOUDFLARE_API_TOKEN`/`ACCOUNT_ID` secrets（GitHub Pages 可保留做双写冗余，零成本）；再按路线 A 加 fast-refresh。
- 成本 ¥0（免费层：无限带宽/请求、268 文件 ≪ 20k、单文件 16 MB < 25 MiB）；域名另计 ~¥60/年（可选但强烈建议，见 §1.7）。
- 频率上限：**~10-15 min 稳定可行**（部署秒级，瓶颈只剩 Actions 调度；要再快用 §2.3 的 Worker cron 触发 workflow_dispatch，可压到 5 min）。
- 国内：自定义域名 + CF 明显好于 github.io（未核实，社区共识）。
- 风险：wrangler 无限部署是 2023 年 CF 员工答复，未来可能加 Direct Upload 限额（未核实）；多一个账号体系。

### 路线 C：Cloudflare Pages + Worker/R2 实时层（B 的增量升级）

- 做法：盘中源（资产快照/FOMC 概率/期权墙/加密/新闻）由 Actions（或本地 TWS 机器）拉完直接 `wrangler r2 object put` 写 JSON；站点加一个 Pages Function `/api-live/*` 读 R2；前端对应面板改 fetch 路径。HTML 壳与低频数据仍走预渲染部署。
- 成本 ¥0（R2 免费层 10 GB + 10M 读/月远超所需；Functions 请求计入 Workers 10 万/天，个人站够用）；工作量：中（一个 ~50 行 Worker + 前端局部改造 + Actions 加一步上传）。
- 频率上限：盘中面板 **5 min 级**（数据写入即生效，不依赖整站部署）；配合本地机器跑 IBKR 源还能解决「期权墙无法上云」的实时化。
- 风险：实时层与预渲染层两套取数路径，注意 `as_of` 标注一致。

### 最终建议

**先 B 后 C**：B 是一次 ~30 分钟的 workflow 改动，立刻拿到无限带宽 + 秒级增量部署 + 15 min 刷新；C 在 B 的地基上按需给盘中面板加实时层（R2 免费额度最宽，不用 KV）。A 可作为 B 的过渡期先行（两者不冲突，fast-refresh workflow 是 B 的组成部分）。不建议 Vercel/Netlify/Deno Deploy/Amplify/Fly/HF Spaces（理由见 §1.4-1.6）。

---

## 4. 参考来源汇总

- GitHub Pages limits：<https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits>
- GitHub Actions schedule 可靠性/5 min 下限：<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule>
- GitHub Actions billing（公开仓库免费）：<https://docs.github.com/en/billing/concepts/product-billing/github-actions>
- GitHub runner workflow continuity（排队丢弃）：<https://docs.github.com/en/actions/using-github-hosted-runners/using-github-hosted-runners/about-github-hosted-runners>
- Cloudflare Pages limits / Direct Upload：<https://developers.cloudflare.com/pages/platform/limits/> · <https://developers.cloudflare.com/pages/get-started/direct-upload/>
- wrangler 部署不计 build 额度 / 哈希去重（社区）：<https://community.cloudflare.com/t/builds-vs-deployments/494717> · <https://community.cloudflare.com/t/cloudflare-pages-simple-explanation-of-already-uploaded-when-deploying/582135>
- Cloudflare Workers limits（cron 5 个/账号、100k req/天）：<https://developers.cloudflare.com/workers/platform/limits/>
- Workers Static Assets 计费（静态请求免费无限）：<https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/>
- KV / R2 免费额度：<https://developers.cloudflare.com/kv/platform/limits/> · <https://developers.cloudflare.com/r2/pricing/>
- Vercel limits / Hobby / Cron / 中国访问 KB：<https://vercel.com/docs/limits> · <https://vercel.com/docs/plans/hobby> · <https://vercel.com/docs/cron-jobs/usage-and-pricing> · <https://vercel.com/kb/guide/accessing-vercel-hosted-sites-from-mainland-china>
- Netlify 定价（credit 制）：<https://www.netlify.com/pricing/>
- Deno Deploy / Amplify / Fly.io / HF：<https://deno.com/deploy/pricing> · <https://aws.amazon.com/amplify/pricing> · <https://fly.io/docs/about/discontinued-plans/> · <https://huggingface.co/pricing>
- cron-job.org：<https://cron-job.org/en/>
- 国内可达性对比（社区，未核实）：<https://cloud.tencent.com/developer/article/2689511>
