// site-nav.js — 全站导航唯一数据源（ESM 单文件：export const + 兼容挂 window）。
//
// 消费方（三处视图，一份数据，统一 import）：
//   1. js/nav.js        → 专题页顶栏 Tab（只渲染到「组」这一层）
//   2. js/macro-view.js → SPA 左侧专题树（组 + items 子页）
//   3. js/dashboard.js  → SPA 跨资产表跳转（指标键 → 导航键 → page）
// 页面以 <script type="module"> 加载本文件（经典 <script src> 遇 export 语法直接报错）；
// 挂 window.SITE_NAV 供过渡期未改 import 的模块消费方兜底，Astro 构建期无 window 则跳过。
//
// 新增专题：改这里即可（页面放 frontend/public/ 对应目录，无需别处登记）。
// 字段：home=顶栏第一个入口（也是 SPA 默认落地页）；spaViews=SPA 自有视图（非页面）；
// groups=专题分组，page=入口页，items=子页，sub=侧栏二级缩进，view=SPA 视图键。
export const SITE_NAV = {
  home: { key: 'daily', label: '今日研判', page: '/daily/' },
  spaViews: [
    { view: 'dashboard', label: '市场仪表盘' },
    { view: 'tech', label: '技术分析' },
    { view: 'correlation', label: '关联分析' },
  ],
  groups: [
    { key: 'assets', label: '大类资产', page: '/assets/', items: [
      { key: 'assets/equities', label: '美股', page: '/assets/equities.html' },
      { key: 'assets/etfs', label: 'ETF 看板', page: '/assets/etfs.html', sub: true },
      { key: 'equities/options', label: '期权 / GEX', page: '/assets/equities/options.html', sub: true },
      { key: 'equities/positioning', label: '持仓追踪 · CFTC', page: '/assets/equities/positioning.html', sub: true },
      { key: 'assets/bonds', label: '债券', page: '/assets/bonds.html' },
      { key: 'assets/commodities', label: '商品', page: '/assets/commodities.html' },
      { key: 'assets/fx', label: '外汇', page: '/assets/fx.html' },
      { key: 'assets/crypto', label: '加密货币', page: '/assets/crypto.html' },
      { key: 'assets/crypto-derivatives', label: '衍生品 · 资金与杠杆', page: '/assets/crypto-derivatives.html', sub: true },
      { key: 'assets/crypto-options', label: '期权 · 预测市场', page: '/assets/crypto-options.html', sub: true },
    ] },
    { key: 'rates', label: '利率', page: '/rates/', items: [
      { key: 'rates/fed-funds', label: '联邦基金利率', page: '/rates/fed-funds.html' },
      { key: 'rates/yield-curve', label: '收益率曲线', page: '/rates/yield-curve.html' },
      { key: 'rates/pricing', label: '利率定价', page: '/rates/pricing.html' },
    ] },
    { key: 'inflation', label: '通胀', page: '/inflation/' },
    { key: 'labor', label: '就业', page: '/labor/' },
    { key: 'treasury', label: '美债', page: '/treasury/' },
    { key: 'liquidity', label: '流动性', page: '/liquidity/', items: [
      { key: 'liquidity/transmission-chain', label: '压力指数', page: '/liquidity/transmission-chain.html' },
      { key: 'liquidity/fed-balance-sheet', label: '资产负债表', page: '/liquidity/fed-balance-sheet.html' },
      { key: 'liquidity/operations', label: '公开市场操作', page: '/liquidity/operations.html' },
      { key: 'liquidity/rrp-tga', label: 'RRP & TGA', page: '/liquidity/rrp-tga.html' },
      { key: 'liquidity/reserves', label: '准备金', page: '/liquidity/reserves.html' },
      { key: 'liquidity/global-dollar', label: '全球美元', page: '/liquidity/global-dollar.html' },
      { key: 'liquidity/subsurface', label: '次表层资金流', page: '/liquidity/subsurface.html' },
    ] },
    { key: 'credit', label: '信用', page: '/credit/' },
    { key: 'fed', label: '美联储', page: '/fed/' },
    { key: 'vol', label: '波动率', page: '/volatility/', items: [
      { key: 'volatility/vix', label: 'VIX', page: '/volatility/vix.html' },
    ] },
    { key: 'geo', label: '地缘风险', page: '/geo/' },
  ],
};

// 过渡期兼容：仍读 window.SITE_NAV 的模块消费方兜底（经典 <script src> 加载本文件会报错，勿用）
if (typeof window !== 'undefined') window.SITE_NAV = SITE_NAV;
