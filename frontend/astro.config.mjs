// @ts-check
import { defineConfig } from 'astro/config';

// ADR-0003：public/ 原样拷贝到 dist/ + 编译 .astro 页。
// format 维持默认 'directory'：入口页 /xxx/ 原生可用（spike 已验证 /credit/）；
// 子页原 URL 带 .html（SITE_NAV items，共享 JS 硬闸不可改），
// 由 public/_redirects 200 重写保路径（.html → 目录页，地址栏不变）。
export default defineConfig({
  output: 'static',
});
