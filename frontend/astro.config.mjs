// @ts-check
import { defineConfig } from 'astro/config';

// ADR-0003 扩张阶段第一步：无 .astro 页，build 仅原样拷贝 public/ 到 dist/。
// publicDir 默认 ./public，outDir 默认 ./dist，均用 Astro 默认值。
export default defineConfig({
  output: 'static',
});
