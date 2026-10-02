# Handoff — 观澜台（market-base）前端 UI/UX 审计 · 收工版

生成时间：2026-10-02 10:58
仓库：`/Users/guankai/code/python/market-base`（uv 项目，Python 一律 `uv run`）
线上：https://market-base.pages.dev （main 分支；部署是否跟上本次 commit，用第 6 节最后两条 curl 现场判定）

> 组织原则：**凡已被 artifact 承载的只给指针**。每笔改动的完整取舍在各自 commit message 里，
> 待办的证据与验收在 Issues 正文里，前端约定在 AGENTS.md 第 8 节里 —— 本文只写没有任何
> 地方承载的东西。（前身是 OS 临时目录里的交接稿，现入库随代码一起演进。）
>
> **OS 临时目录里 2026-10-02 的两份 tmp 交接稿（`market-base-ui-audit-handoff.md` 上午版、
> `market-base-handoff-2026-10-02.md` 收尾版）内容已全部并进本文，作废别再读** ——
> tmp 会被清，且旧版不带本轮的 playwright 回路与 #26 可动性排序。

## 1. 一句话现状

一场 34 页前端审计（第 1–5 步 + 收尾三笔）**全部落地并已线上验证**；后续又收掉
#20/#23/#24/#26/#27 五条（断点复核、移动端 wontfix、去 iframe、重写守卫、375 溢出），
待办全部在 GitHub Issues（open 数以 `gh issue list --state open` 为准，不抄本文）；
无悬而未决的部署风险，wrangler v4 已跑通多次（首次红过，原因与修法见 `59deac33`）。

## 2. 本轮改动（全部已 push；部署是否跟上用第 6 节两条 curl 现场判定）

| commit | 一行 |
|---|---|
| `07f1e5fb` | CI：node24 化 + 钉 `ubuntu-24.04` |
| `78a9474d` | AGENTS.md 第 8 节补 6 条新约定 |
| `9d9232a1` | 第 4 步：`zone_color` 改下发语义 key + 封「后端字面 hex 进文字槽」3 条守卫 |
| `54a1225e` | 修一条硬写日期、过一周必红的 CME 测试 |
| `b9abd490` | 源侧口径：`_pct_chg` 守近零基数、`cds()` 自带 `unit` |
| `5a906663` | brand 文字档压深到 `#155ecb`，第七组语义色入守卫名单 |
| `59deac33` | CI：wrangler-action v4 的 `accountId` 必须走 input（首次 v4 部署红过） |
| `a8ed248f` | 第 5 步：内联色不再固化，切主题即时跟随（`color-mix`） |
| `493178e2` | 文档卫生：过期 plan 加失效标注 + BTC/ETH 价比计划归档 |
| `0b0c785b` | 锁住第 5 步：新守卫 + AGENTS 补「内联色只给引用」口径 |
| `8e70bc07` | 本文入库（交接稿从 OS 临时目录搬进 `docs/audit-reports/`） |
| `bc85cd73` | DATA_CATALOG 页面索引改 directory 路由 + 修 3 条真 404（#25） |
| `6e4b19e4` | 断点收敛为 1024 / 768 两档 + 图表高度改 `clamp()`（#20） |
| `bb7602a5` | 外汇页精修：分组表并卡、verdict 卡 1024 取 2 列、ticker 收进 title |
| `e687f766` | 首屏「今日一句话」结论层 + 补上时效守卫的共享 JS 洞（#21） |
| `3f496142` | 本文同步：待办清单改成「以 `gh issue list` 为准」，不抄编号与计数 |
| `00961406` | 本文：并入 tmp 版交接稿独有的三样东西（playwright 回路 / 可动性排序 / 派 agent 教训） |
| `ce1ea43b` | **#26 落地**：`_redirects` ↔ `.html` 链接的同步守卫（反向验过：删一行必红） |
| `52754aeb` | **#27 落地**：375px 两处真横向溢出（`#re-cards` 缺单列档 + `.re-as-of` nowrap） |
| `e434b468` | `FED_TARGET_RANGE_FALLBACK` 跟上现实 3.75–4.00（旧值差一次 25bp） |
| `36aa17f7` | `.fed-chart-tall` 的 clamp 真正生效（删 JS 内联 500px，上限按实际渲染值定） |
| `119346d8` | BTC/ETH 相关性「高位」改近 1y 滚动分位（绝对 0.8 已恒真） |
| `ea6ecc11` | 960 档 KPI 卡 2→3 列，用 `auto-fit` 不写死列数（#20 人工复核结论） |
| `6c40080d` | **#24 落地**：宏观视图去 iframe，SPA 只留三视图、默认仪表盘（净删 48 行） |
| `c547f23a` | **专题页补回左侧专题树**（#24 的已知代价收口）：TopicLayout 构建期渲染同一棵 SITE_NAV |
| `201d3caa` | 桌面收起顶栏胶囊 Tab（与左树重复；≤1024 反过来：无左树、胶囊是唯一导航） |
| `003c8124` | 删掉外汇页面包屑 `.fx-crumb`（全站唯一孤例）→ 开出 #29 |

## 3. 待办 = GitHub Issues

用 `gh issue list --state open` 取当前清单，别抄本文（会过期）。已知的：#20 断点收敛已落地但
**待浏览器复核**（comment 里有按优先级排的复核表）；#26 是 `_redirects` 与 SITE_NAV 的同步守卫；
#22/#23 要你先判值不值得做，#24 是架构级。正文自带现状证据、动作与验收，别在这里复述。

**按可动性排序**（谁能动，别再等错的人）：本轮 #20 / #23 / #24 / #26 / #27 全部收口，
**open 清单以 `gh issue list --state open` 为准**（下面只记「谁能动」的判断，不抄计数）：

- **#28**（`ready-for-agent`）—— 18 处逐页图表内联高度统一走 clamp。**正文已带完整决策表**
  （18 处逐条 `file:line = 值` + 值→删内联后变成什么），本轮复核过。不是「一行 CSS」（内联
  `style` 优先级高于类），但**比正文原先估计的小**：14/18 已挂类 → 动作是**删内联**不是换
  markup；零成本精确命中 **9 处**（340×5 / 300×2 / 280×2），补一行 `.chart.chart-sm` 后 11 处，
  真正要人定的只有一处（`assets/index` 的 520 主图）。
- **#22**（`needs-triage`）—— 密度模式。我的建议是先不做：真实成本不在那 30 行 CSS，
  在「两套密度长期都要维护」；嫌挤可先把 `.re-cards` 的 `minmax(150px,1fr)` 调到 130px
  （一行，桌面自动多一列，拿 80% 收益）。
- **#29**（`ready-for-agent`）—— 8 个 `assets/*` 子页缺页内锚点条（`.page-toc` 24/33 页有）。
  正文自带逐页小节数与缺哪个 id 的表；与 #28 同属「逐页扫一遍」，可同批做。
  背景：本轮把面包屑与顶栏胶囊收掉后（`003c8124` / `201d3caa`），导航件只剩这一处不一致。

## 4. 下一轮必读的仓库事实（不在任何 artifact 里）

- **本地 `python -m http.server` 不实现 `_redirects`** —— 打 dist 时 `/assets/fx.html`
  这类带后缀的 URL **必 404**，而目录形态 `/assets/fx/` 是 200。看到 404 先分清是本地服务
  限制还是真缺重写行（真缺重写由 `test_html_links_have_redirects` 抓，线上由 Cloudflare 服务）。
- **SPA 现在是三视图（仪表盘 / 技术 / 关联），默认仪表盘**（#24 去 iframe，`6c40080d`）。
  专题页不再是内嵌视图：侧栏专题项是真实链接整页跳转，旧深链（`#daily`、`#rates/fed-funds`、
  `#macro/fed-funds` 末段容错）由 `macro-view.js` 的 `routeHash` 整页 replace。
  ⇒ 跳过去的专题页**也有同一棵左侧专题树**（TopicLayout 构建期渲染，复用 `.macro-nav` 类，
  当前页高亮 + 当前组自动展开；窄屏与内嵌不出现），所以整页跳转不会「导航消失」。
  导航件现状（本轮收口后）：≥1025px = 左树 + 顶栏胶囊收起；≤1024 = 无左树、胶囊是唯一导航；
  面包屑全站已无（`.fx-crumb` 在 `003c8124` 删掉，全站唯一孤例）。
  已知代价：SPA 手动切主题后跳专题页会回到系统偏好（没改是因为那会推翻「刷新恢复自动」
  既有设计，要持久化就 `app.js` 的 `applyTheme` 初始值 + `TopicLayout` 的 `resolveDark` 各一行）。
- **`zone()` 现在只返回语义 label**（`src/analysis_utils.py`）。区间表第四列留给色表本身：
  `volatility_analysis.py` 的 `zones: [{label, color}]` 仍下发 hex，因为前端只拿它画
  `background`。新增分析模块要下发区间/状态，照这个口径：**给语义 key，不给色**。
- **图表高度有两套档位家族，只有一套能用**（`frontend/public/css/special.css`，1440×900 实测量过）：
  `.chart` = `clamp(220px,40vh,300px)`；**能用的是双类 `.chart.chart-lg`(280) / `.chart.chart-md`(340)**
  （特异性 0-2-0 压过 `.chart`，已被 `equities.astro:92/154` 用过两次）；
  **`.re-chart-sm/md/lg/xl`（:93）是死代码** —— 它在 `.chart`（:192）**之前**且同特异性，
  所以 `class="chart re-chart-sm"` 实测 **300px 而非 260px**（静默失效，这就是四档零使用的真因，
  不是忘了用）；要生效得写 `class="re-chart re-chart-sm"`（260 ✓），但 `.re-chart` 自带
  `border + background` = 换卡片外壳。**新页面想要非 300 高度，加到 `.chart.chart-*` 那一族，
  别碰 `re-chart-*`**；改完类必用 playwright 量实高（光看 markup 会以为生效了）。
- **四条守卫各自的边界**（`tests/test_semantic_text_contrast.py`，别把它们当万能网）：
  分析模块不得有 `"*_color"` 键；前端 `color:` 槽不得插值 `*_color`；CSS `color:` 不得引用
  名字以 `-color` 结尾的自定义属性；DOM 内联 `style="…"` 不得出现 `reCssVar()` 的解析结果。
  它们**不**管 `zones[].color`（合法填充）、JS 对象键里的 `color:`（canvas）、
  `.cssText = '…'`（全站仅 1 处，见测试 docstring 的 ponytail 注释）。
- **brand 的文字档是 `--accent-ink: #155ecb`**（亮色），四种真实底色最低 4.87:1；暗色仍
  `= --color-brand`。别再为 brand 文字造新 token。
- **线上是 directory 路由**：`/api/credit/stress` 200、`/api/credit/stress.json` 404、
  `/credit/` 而非 `/credit.html`。写文档、写跳转、curl 验证都按这个来（#25 就是这条）。
- **`ready-for-human` 标签是本轮新建的**，`docs/agents/triage-labels.md` 五档现在齐了。
- **commit message 里互引的 hash 有几处是 rebase 前的旧号**（推之前本地 rebase 过 5 笔数据
  commit）：`4a940d1f`→`07f1e5fb`、`fa429f90`→`9d9232a1`、`39f9ed1d`→`78a9474d`、
  `207a0d78`→`54a1225e`。已入库的 message 不改，读到按此对照。

## 5. 已验证过的死路 / 陷阱（省得重做）

- **两项审计结论已撤销**：`touch-action`（全站零自实现手势，加 `pan-x` 反而禁掉表格纵向
  滑动）；触控目标 <24px（门槛是 **24 CSS px**，WCAG 2.2；44 是 iOS pt / Android dp；
  `.range-btn` 实算 ≈24.4px 已过线）。两条都写进了 #23 正文。
- **别信字段名，回查后端真实来源**：R5「32 页未转义」、R8「helper 归一」都因字段名虚高。
- **`special.css` 的 860 / 640 / 575 不是断点**（表格 `min-width` 与网格约束），断点收敛时
  别一起改 —— 已写进 #20 正文。
- **wrangler-action v4 会 `process.env.CLOUDFLARE_ACCOUNT_ID = getInput('accountId')`**，
  不传 input 就把 workflow 级 env 的真值**静默覆成空串**（v3.15 无此行）。凡升 action 大版本，
  除了 CLI 参数名，还要核它对 env 的赋值行为。
- **硬写日期的测试过一周必红**（`54a1225e`）。断言一律相对 `datetime.now(UTC)`。
- `~/.config/opencode/scripts/run-precommit.sh` 全仓跑 hook 会顺手重排 99 个 `data/*.json`；
  给暂存区做检查直接 `git commit` 或 `uv run pre-commit run`。
- **shell 引号会静默吃掉正文**：commit message 含双引号时别用 `git commit -m "$(cat <<'EOF' …)"`
  （bash 提前闭合外层引号），写临时文件走 `git commit -F`。**同一族且更阴**：
  `gh issue close --comment "…`code`…"` 里的反引号会被当命令替换**执行**，内容静默丢失
  （本轮 #27 的关闭说明整个 `.fed-chart-tall` 不见了，事后才发现）——
  凡带反引号 / 双引号的正文（Issue 正文、评论、commit message）一律走文件：
  `--body-file` / `-F`。**同族第三击是验证脚本自己崩**：本轮两次假故障（一次 grep 错对象、
  一次脚本里的 JS 选择器带双引号，经 heredoc / 写文件多层转义后变成非法续行 → Python
  SyntaxError），**而被测部署两次都是 success**。带嵌套引号的 JS 片段先拼字符串再传，
  别靠反斜杠；`wait_until="networkidle"` 在带轮询/长连接的页上会超时，验证脚本一律
  `domcontentloaded` + 固定 sleep（本轮就因此误报过一次「图表 0」）。
- 并行派 agent 改前端：`frontend/public/css|js`、`layouts/`、`tests/` 这类共享文件**主 agent
  先改完**，agent 只按目录领页面；且别让它们同时跑 `npm run build`（抢 `dist/`）。
- **agent 会照字面执行你错误的指令**：本轮 agent 交付里我改掉两处，另补一处自己写错的断言；
  前三者的根因都不是它们写错，而是**我给的限制把形状逼歪**——① 禁止碰 `server.py` → 它把新端点逻辑放进
  `export_pages.py`，但本仓模式是「路由在 `server.py`、export 复用路由函数」（`get_daily_brief`
  即先例），放反会造成 dev 与静态导出两套口径，已搬回；② 让它自己设计卡头 → 它加了
  `R.asOf(...)`，与页头全局时效**同屏两个不同日期**，已改成非时效文案、日期退回行 `title`；
  ③ 反向的坑（不是 agent 的错，是我的）：我 brief 里写「文字档那份不要动」，自己又把它当成未修项写进交接稿——
  实测 `bucketTextColors` / `stateTextColor` 早在 `a8ed248f` 就已是 `var(--color-*-text)`，
  全站 DOM 内联 style 零字面 hex（守卫 21 绿）——**「我记得没修」不等于「没修」，
  写进交接稿前得 grep 一遍**，否则下一轮去追一个不存在的问题。
  **派活前想清楚指令本身对不对；限制条件常常比目标更容易把结果带偏。**
- **改完要问「锁住了吗」**：第 5 步改完时 AGENTS.md 零提及、守卫零覆盖，隔了一笔才补
  （`0b0c785b`）。约定不落测试就等于没立。

## 6. 验证命令（收尾必跑）

```bash
uv run python -m pytest                                   # 当前 715 全绿
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
cd frontend && npm run build                              # 33 页
uv run python -m pytest tests/test_semantic_text_contrast.py tests/test_static_single_source.py -q
gh run list --workflow=deploy-pages.yml --limit 1 --json databaseId,status,conclusion
curl -sS https://market-base.pages.dev/credit/ | grep -c color-mix             # 期望 >0
curl -sS https://market-base.pages.dev/api/credit/stress | grep -c zone_color  # 期望 0
```

### 视觉验证回路（playwright，#20 复核就用这个）

「数学上安全但难看」的退化只有量尺寸才抓得出来，靠猜和只看截图都不行。仓里已装
playwright（chromium 在 `~/Library/Caches/ms-playwright/`），`dist` 含未 push 的改动，比线上快一步：

```bash
(nohup python3 -m http.server 8899 --directory frontend/dist >/dev/null 2>&1 &); sleep 1
uv run python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h in [(1440, 900), (960, 900), (820, 900), (375, 812)]:
        pg = b.new_page(viewport={'width': w, 'height': h}, color_scheme='dark')
        pg.goto('http://127.0.0.1:8899/assets/fx/', wait_until='networkidle')
        pg.wait_for_timeout(1400)
        print(pg.evaluate("() => [...document.querySelectorAll('.fx-verdict-card')]"
                         ".map(c => Math.round(c.getBoundingClientRect().width))"))
        pg.screenshot(path=f'/tmp/shot-{w}.png', full_page=True)
        pg.close()
    b.close()
PY
pkill -f "http.server 8899"
```

三个坑（每个都让本轮白跑过一次）：

1. **主题不靠 `localStorage`** —— 非内嵌页走 `prefers-color-scheme`，要用
   `new_page(color_scheme='dark')`；键 `ticker-toolkit-dark` 只在 iframe 内生效。
2. **SPA 默认视图已改**（#24 去 iframe）—— 现在默认就是仪表盘，不用再点切换；
   侧栏专题项是**真实 `<a href>`**（点组头用 `#macro-nav a[href="/assets/"]`，
   子页在未展开的 `.macro-nav-sub` 里、直接点会因不可见而超时），点了就是整页跳走。
3. **判横向溢出别看元素 `right`** —— 隐藏抽屉会假阳性；量
   `documentElement.scrollWidth === window.innerWidth`。

已实测：960 宽下 `/assets/fx/` 的 4 张 verdict 卡各 389px（2 列，符合 `bb7602a5` 意图），
`scrollWidth == innerWidth == 960`（无溢出），暗色 body 底色 `rgb(17,18,23)`。

**验线上把 BASE 换成 `https://market-base.pages.dev` 即可**（同一套脚本，本轮用它确认了
#27：375 下 `/rates/pricing/` 与 `/fed/` 均 `[375,375]`、`#re-cards` 1 列、时效块 247×36 两行）。
但**别拿 `curl 页面 HTML | grep 'max-width'` 当验收**：媒体查询在 `/css/special.css`
这个独立资产里（Astro 原样拷 `public/`，不内联），页面 HTML 里 grep 不到 ——
本轮就这么假阴性一次（monitor exit 1 是断言错，部署其实 success）。
要 grep 就 grep 资产 URL，要判布局就用 playwright。

## 7. 建议调用的 skills

- **`tdd`** —— 新约定一律配一条会红的测试，**先例就是 #26**（`test_html_links_have_redirects`：
  先写「每条 `.html` 链接都要有 `_redirects` 行」，再删一行验它必红）。做 #28 时同样先加
  「内联高度白名单」守卫再改页面。
- **`code-review`** —— 跨 Python + Astro 的一致性改动（Standards / Spec 双轴）。
- **`git-commit`** —— 本仓 message 风格很重（中文、`type(scope): 标题——要点`、「刻意未做」段、
  构建验证行），照它写；含双引号时用 `git commit -F 文件`。
- **`ponytail-review`** —— #20/#22 这类「收敛/归一」议题，先判断哪些是真重复。
- **`ui-ux-pro-max`** —— 只有 #22 密度模式需要（#23 已 wontfix）；它给过一处纠正：web 触控目标
  最小 **24 CSS px**（WCAG 2.2），44 是 iOS pt / Android dp。
- **`research`** —— 需要一手数据才能决定做不做时走它（先例：#23 的前置是「有无移动端
  用户」，本地无 CF 凭据查不到流量 → 按正文规则 wontfix）。
- **`domain-modeling`** —— 两档制 + 内联色口径目前只在 AGENTS.md 第 8 节；若要正式记 ADR
  （`docs/adr/` 已有 0001-0003 先例）走它。
- **`diagnosing-bugs`** —— 若某专题页数据不更新 / 导出静默缺端点（根因常在
  `src/export_pages.py` 的 `_safe()` 跳过当日缺数据端点，`api/*` 可以合法 404）。
- **`python-patterns`** —— 动 `src/*_analysis.py`（首屏结论层已在 `e687f766` 落地，改它才需要）。
