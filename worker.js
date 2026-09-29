// workers.dev 代理层：market-base.<account>.workers.dev → market-base.pages.dev
//
// 为什么需要一层代理：主站（含 /api/* 预渲染数据）由 GH Actions 用 export_pages
// 部署到 Cloudflare Pages，每次数据 push 自动更新；workers.dev 只是「国内可达入口」，
// 自身不再存静态副本 —— 原先 assets 指到 static/（前端源码，没有 api/ 那 218 个
// 预渲染 JSON），导致 /api/* 全 404、各面板空白。
export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.hostname = "market-base.pages.dev";
    return fetch(new Request(url, request));
  },
};
