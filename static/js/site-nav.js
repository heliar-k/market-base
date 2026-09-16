// site-nav.js — 全站导航唯一数据源（经典脚本，全局 SITE_NAV，无依赖、可在任何脚本前加载）。
//
// 消费方（三处视图，一份数据）：
//   1. js/nav.js        → 专题页顶栏 Tab（只渲染到「组」这一层）
//   2. js/macro-view.js → SPA 左侧专题树（组 + items 子页）
//   3. src/export_pages.py._PATH_PREFIXES → Pages 子路径注入白名单
//      （跨语言不做运行时共享，由 tests/test_static_single_source.py 交叉校验，漏加即测试红）
//
// 新增专题：改这里 + export_pages.py 白名单（若引入新顶层目录）。
// 字段：home=顶栏第一个入口（也是 SPA 默认落地页）；spaViews=SPA 自有视图（非页面）；
// groups=专题分组，page=入口页，items=子页，sub=侧栏二级缩进，view=SPA 视图键。
var SITE_NAV = {
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
      { key: 'assets/crypto-derivatives', label: '衍生品 · OKX+Deribit', page: '/assets/crypto-derivatives.html', sub: true },
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
