// scripts_dev/check_empty_option.mjs — R.mkChart 空态判定的最小可跑校验（node 直接执行，无框架）
// 为什么需要：空态守卫写在共享函数 R.mkChart 里，一次覆盖 51 个建图点；
// 判错方向很不对称——把有数据的图判成空 = 静默吃掉图表。所以留一个失败即报警的检查。
// 跑法：node scripts_dev/check_empty_option.mjs
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// rates-common.js 是经典脚本（顶层 const R = {...}），用 vm 求值后取 R，不改生产代码
const src = readFileSync(new URL('../frontend/public/js/rates-common.js', import.meta.url), 'utf8');
const ctx = vm.createContext({
  window: { addEventListener() {} },
  document: { body: { classList: { contains: () => false } }, addEventListener() {}, getElementById: () => null },
  console,
});
vm.runInContext(`${src}\n;globalThis.__R = R;`, ctx);
const R = ctx.__R;

const cases = [
  // [说明, option, 期望 isEmptyOption]
  ['有数据', { series: [{ type: 'line', data: [1, 2, 3] }] }, false],
  ['0 是有效值，不是空', { series: [{ data: [0, 0] }] }, false],
  ['空数组', { series: [{ data: [] }] }, true],
  ['全 null', { series: [{ data: [null, undefined, ''] }] }, true],
  ['一条空 + 一条有数据 → 不算空', { series: [{ data: [] }, { data: [5] }] }, false],
  ['对象点 value 为 null', { series: [{ data: [{ value: null }] }] }, true],
  ['对象点有 value', { series: [{ data: [{ value: 5 }] }] }, false],
  ['candlestick 数组点', { series: [{ type: 'candlestick', data: [[1, 2, 3, 4]] }] }, false],
  ['series 是对象而非数组', { series: { data: [1] } }, false],
  ['无 series 字段', {}, true],
  ['series 缺失但有 xAxis', { xAxis: { type: 'time' } }, true],
];

let bad = 0;
for (const [name, opt, want] of cases) {
  const got = R.isEmptyOption(opt);
  if (got !== want) { bad++; console.log(`FAIL ${name}: 期望 empty=${want}，实得 ${got}`); }
}
console.log(bad ? `${bad}/${cases.length} 例失败` : `✓ ${cases.length} 例全过`);
process.exit(bad ? 1 : 0);
