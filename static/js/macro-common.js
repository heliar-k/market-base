// macro-common.js — shared constants & utilities for macro/correlation/liquidity views

export const MACRO_COLORS = ['#1a73e8', '#ff9800', '#26a69a', '#ef5350', '#9c27b0', '#00bcd4', '#7c4dff', '#607d8b'];

export const MACRO_LABELS = {
  VIX: '波动率指数（恐慌指数）', HY_OAS: '高收益债信用利差', IG_OAS: '投资级债信用利差',
  CPI: '消费者物价指数', PCE: '个人消费支出价格指数', CORE_CPI: '核心消费者物价指数',
  T5YIE: '5年期通胀预期', T10YIE: '10年期通胀预期', T5YIFR: '5年期远期通胀率',
  MICH: '密歇根通胀预期', EXPINF_1Y: '1年期通胀预期', EXPINF_2Y: '2年期通胀预期',
  EXPINF_5Y: '5年期通胀预期', EXPINF_10Y: '10年期通胀预期',
  UNRATE: '失业率', PAYEMS: '非农就业人数', ICSA: '初请失业金人数',
  GDP: '国内生产总值(GDP)', INDPRO: '工业生产指数',
  FEDFUNDS: '联邦基金利率', DFF: '有效联邦基金利率', DFEDTARL: 'FOMC 目标利率下限', DFEDTARU: 'FOMC 目标利率上限',
  SOFR: '担保隔夜融资利率', SOFR1: 'SOFR 1st 分位数', SOFR25: 'SOFR 25th 分位数',
  SOFR75: 'SOFR 75th 分位数', SOFR99: 'SOFR 99th 分位数', SOFRVOL: 'SOFR 日成交量',
  OBFR: '隔夜银行融资利率', IORB: '准备金余额利率',
  DGS1MO: '1月期国债收益率', DGS3MO: '3月期国债收益率', DGS6MO: '6月期国债收益率',
  DGS1: '1年期国债收益率', DGS2: '2年期国债收益率', DGS3: '3年期国债收益率',
  DGS5: '5年期国债收益率', DGS7: '7年期国债收益率', DGS10: '10年期国债收益率',
  DGS20: '20年期国债收益率', DGS30: '30年期国债收益率',
  SPREAD_2S10S: '2s10s利差', SPREAD_3M10S: '3m10s利差', SPREAD_5S30S: '5s30s利差',
  SOFR_IORB_SPREAD_BP: 'SOFR-IORB利差(bp)',
  DFII5: '5年期TIPS收益率', DFII7: '7年期TIPS收益率', DFII10: '10年期TIPS收益率',
  DFII20: '20年期TIPS收益率', DFII30: '30年期TIPS收益率',
  BEI_5Y: '5年期盈亏平衡通胀率', BEI_7Y: '7年期盈亏平衡通胀率',
  BEI_10Y: '10年期盈亏平衡通胀率', BEI_20Y: '20年期盈亏平衡通胀率',
  BEI_30Y: '30年期盈亏平衡通胀率',
  NFCI: '金融状况指数', RRPONTSYD: '隔夜逆回购规模', WTREGEN: '财政部一般账户余额',
  WRESBAL: '准备金余额', WALCL: '美联储总资产', NET_LIQUIDITY: '净流动性',
  UMCSENT: '密歇根消费者信心指数', STLFSI4: '金融压力指数',
  DXY: '美元指数',
};

export const MACRO_DATE_RANGES = [
  { label: '1M', value: '1m', months: 1 },
  { label: '3M', value: '3m', months: 3 },
  { label: '6M', value: '6m', months: 6 },
  { label: '1Y', value: '1y', months: 12 },
  { label: '2Y', value: '2y', months: 24 },
  { label: '3Y', value: '3y', months: 36 },
  { label: '5Y', value: '5y', months: 60 },
  { label: '10Y', value: '10y', months: 120 },
  { label: '30Y', value: '30y', months: 360 },
  { label: 'All', value: 'all', months: 0 },
];

export function applyDateFilter(data, range) {
  if (range === 'all' || !data || data.length === 0) return data;
  const rangeInfo = MACRO_DATE_RANGES.find(r => r.value === range);
  if (!rangeInfo || !rangeInfo.months) return data;
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - rangeInfo.months);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return data.filter(d => d.date >= cutoffStr);
}
