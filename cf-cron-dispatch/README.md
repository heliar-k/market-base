# gh-cron-dispatch — Cloudflare Worker 定时触发 GitHub Actions

GitHub Actions 的 schedule 是 best-effort 共享队列，实测本仓 `daily-full` 延迟 3~12 小时、
`intraday-refresh` 一天 32 班只落 2 班（高峰期触发被静默丢弃，run list 里连记录都没有）。
本 Worker 用 Cloudflare Cron Triggers（免费版，分钟级精度）按点调 GitHub API
`workflow_dispatch`，YAML 全部逻辑不动，GitHub schedule 保留作兜底（双触发幂等，
`data-fetch` 并发组会合并撞车的班次）。

## 一次性部署

```bash
# 1. 建 fine-grained PAT（github.com → Settings → Developer settings → Fine-grained tokens）
#    Only select repositories: heliar-k/market-base
#    Repository permissions: Actions → Read and write   ← workflow_dispatch 只需要这一个权限

# 2. 部署（首次会引导 wrangler login）
cd cf-cron-dispatch
npx wrangler secret put GH_DISPATCH_TOKEN   # 粘贴 PAT
npx wrangler deploy
```

触发时间在 `wrangler.jsonc` 的 `triggers.crons` 里，**必须与各 workflow yml 的 cron 保持一致**
（`src/worker.js` 靠 `event.cron` 精确匹配分发到对应 workflow）。
⚠️ deploy 必须在本目录内执行（`npx wrangler deploy` 需要读到本目录的 `wrangler.jsonc`）。
原先仓库根目录还有个 `wrangler.jsonc`（`market-base` 国内入口代理 Worker），从别处 deploy
会误覆盖它（2026-10-14 实际发生过）；该代理 Worker 已于 2026-10-25 删除（Workers Builds
build-minutes 超免费额度），根目录不再有第二份配置。

## 验证

```bash
# 本地模拟任一条 cron（--test-scheduled 暴露 /__scheduled 端点，会把 dispatch 真发出去）
npx wrangler dev --test-scheduled &
curl "http://localhost:8787/__scheduled?cron=12+*+*+*+*"   # 模拟 crypto-hourly
gh run list --workflow=crypto-hourly.yml --limit 1          # 应出现一条 event=workflow_dispatch 的 run

# 线上：等下一个整刻钟，看 intraday 是否按点起跑
gh run list --workflow=intraday-refresh.yml --limit 5
```

## 运维

- **PAT 到期**：Worker 日志报 `HTTP 401`，当天班次失效，GitHub schedule 兜底仍在 →
  续 PAT 后 `npx wrangler secret put GH_DISPATCH_TOKEN` 重贴即可（与 `DATA_PUSH_TOKEN` 同一个续期习惯）。
- **改触发时间**：改 `wrangler.jsonc` triggers.crons + 对应 workflow yml cron，两处一起，然后 `npx wrangler deploy`。
- **加新 workflow**：`triggers.crons` 加一条 + `src/worker.js` 的 `CRON_WORKFLOWS` 加映射（免费版每 Worker 上限 5 条）。
- 日志排查：Cloudflare Dashboard → Workers → gh-cron-dispatch → Logs（或 `npx wrangler tail`）。
