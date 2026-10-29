// Cloudflare Worker Cron → GitHub Actions workflow_dispatch
// 替代 GitHub schedule 的延迟/丢班（实测 daily-full 延迟 3-12h、intraday-refresh
// 32 个班次只落 2 个）。触发时间在 wrangler.jsonc 的 triggers 里配置，下方的键必须与
// 那份 crons 逐字一致（本文件靠 event.cron 匹配分发）。
// 注意：Cloudflare 的 weekday 字段 1-7 且 1 = 周日，与 GitHub 的 1-5（周一到周五）不是
// 同一套编号，同一件事两边要写不同字符串（这里 mon-fri，yml 里 1-5）。
// 需要带 actions:write 的 fine-grained PAT，用 secret 注入（wrangler secret put），
// 不进代码不进 git。

const CRON_WORKFLOWS = {
  "7,22,37,52 13-21 * * mon-fri": "intraday-refresh.yml",
  "0 21 * * *": "daily-full.yml",
  "15 15 * * *": "evening-late.yml",
  "12 * * * *": "crypto-hourly.yml",
};

export default {
  async scheduled(event, env, ctx) {
    // Cloudflare 原样回传配置里的 cron 字符串，缩写的字母大小写可能不同 → 归一化后查表
    const workflow = CRON_WORKFLOWS[event.cron.trim().toLowerCase()];
    if (!workflow) {
      console.error(`triggers 里有 cron "${event.cron}" 但本文件没有映射，补映射后重新 deploy`);
      return;
    }
    const res = await fetch(
      `https://api.github.com/repos/${env.REPO}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "cf-cron-dispatch", // GitHub API 必需 User-Agent
        },
        body: JSON.stringify({ ref: env.REF }),
      },
    );
    // 成功返回 204；409 = 该 workflow 没有 workflow_dispatch 触发器；401 = PAT 过期/无效
    if (res.ok) {
      console.log(`dispatched ${workflow}`);
    } else {
      console.error(`dispatch ${workflow} failed: HTTP ${res.status} ${await res.text()}`);
    }
  },
};
