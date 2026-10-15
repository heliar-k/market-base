# LLM 研判接入设计（试点：rates）

> 决策记录：DeepSeek / LLM 可写叙事 + 定性结论（禁新数字）/ 先试点 rates 再铺开。
> 前提事实：10 个 `*_analysis.py` 均已预留 `_llm_generate()` 同构 dict 钩子（返回 None 回落规则引擎）；
> 前端 `R.foot` + `genText('llm')` 已支持 generator 标记，**前端零改动**。

## 0. 一句话结论

build-time 生成：`export_pages` 时规则引擎先算全部事实 → DeepSeek 受控改写叙事 → 数字一致性校验 → 不过就回落规则引擎，静态 JSON 固化。新增共享模块 `src/llm.py`（~100 行），试点模块改动 ~15 行。

## 1. 架构与数据流

```
deploy-pages.yml（Actions，secret: DEEPSEEK_API_KEY）
  └─ uv run python -m src.export_pages
       └─ get_rates_analysis() → generate_analysis()
             ├─ _LLM_ENABLED（= env 有 DEEPSEEK_API_KEY）
             │    └─ _llm_generate()
             │          ├─ facts = {yield_curve_analysis(), overview_analysis()}   ← 规则引擎，数字唯一来源
             │          ├─ src/llm.py::generate_narrative("rates", facts, prompt)  ← DeepSeek, temp=0, JSON mode
             │          ├─ validate_sections(out, facts)                           ← 数字一致性
             │          └─ 过 → 同构 dict；不过/超时/任何异常 → None
             └─ None → 规则引擎结果（generator: "rules"）
  └─ astro build（研判固化进 frontend/public/api/*.json，站点运行时无 LLM）
```

- 数据日更 → 研判日更，静态 JSON 本身就是缓存，无需额外缓存层。
- 内容寻址缓存（2026-10-15 补，intraday 15 分钟班次不换数据不应重调）：键 = sha256(payload +
  prompt_hint + 模型 + base_url)，存 `data/cache/llm/{module}/{key}.json`（gitignore，
  CI 用 actions/cache 挂载）。数据不变 → 键不变 → 零调用；数据/提示词/换商任一变化 →
  新键自动失效，永无陈旧文案。仅存校验通过的输出；7 天 TTL 顺手清理。
- fail-open 是硬约束：key 缺失、API 挂、校验不过，页面**永远**出规则引擎研判，构建不红。

## 2. 新增 `src/llm.py`（共享客户端）

```python
"""LLM 叙事生成客户端（build-time 批量调用，规则引擎兜底）。"""

def llm_enabled() -> bool:
    """env 有 key 即启用；config 已自动加载 .env。"""
    return bool(os.environ.get("LLM_API_KEY"))

def generate_narrative(module: str, facts: dict, prompt_hint: str) -> dict | None:
    # 1. 系统提示（见 §3）+ facts JSON + prompt_hint（模块私有文风/结构要求）
    # 2. requests 直连 {LLM_BASE_URL}/chat/completions，OpenAI 兼容协议
    #    temperature=0, timeout=60, response_format={"type": "json_object"}
    #    供应商由三个 env 决定：LLM_BASE_URL（默认 deepseek 官方）/ LLM_API_KEY
    #    / LLM_MODEL（默认 deepseek-chat）——换 OpenAI 兼容商 = 改 env，零代码
    # 3. 解析前剥 ```json 围栏（无 JSON mode 的兼容商也能用）
    #    → json.loads → validate_sections(out, facts) → 过则返回，不过返回 None
    # 4. 任何异常 except Exception → stderr 一行标记 + None（不影响部署）：
    #    LLM_FALLBACK: {module}: {reason}   ← CI 据此发 warning 注解（见 §5）
```

校验层（trust boundary，不可省）——「叙事+定性结论」档的规则：

1. **结构**：输出键与 facts 同构；每个 `{title, body}` 的 title ⊆ 输入 titles。
2. **数字一致性**：regex 抽取 body 中所有数字（百分数 / bp / 价格 / 手数），归一化（去千分位、统一小数位数）后必须存在于 facts JSON 的数字集合中。LLM 写「约 4%」这类四舍五入视为违规 → 回落规则引擎（宁严勿松，误杀无损）。
3. **定性结论放行**：不含数字的句子不校验（情绪、方向、因果串联自由写）。
4. **长度上限（UI 护栏）**：每段 body 长度 ≤ max(1.5 × 对应规则引擎 body 长度, 200 字)，
   超长即整段回落规则引擎（与数字校验同一套 fail-open）。基准用「输入 body 长度」自适应
   各段角色（sig-row 格一句 ~40 字 / overview 段 ~150 字），模块无需维护字数表。
   不用 CSS 截断（line-clamp 藏内容，违反「不白屏不静默」）；风险容器：`.sig-row` 一格一句、
   `.re-duo` 两卡等高对齐、移动端长段落 —— 校验在源头一处拦住所有页面。
5. facts 里的数字集合从 `json.dumps(facts)` 全文提取，不做字段级白名单——简单且覆盖派生数字。

## 3. 提示词框架

通用系统提示（存 `src/llm.py`）：

- 角色：美股宏观研究站「观澜台」的研判撰写员；读者是懂市场的自己人。
- 输入是规则引擎**已算好的事实**（全部数字、区间、信号标签），你的职责是改写成专业简体中文叙事。
- 允许：重组段落、因果串联、补充**无数字的**定性判断（资金面情绪、风险偏好、跨段呼应）。
- 长度：每段 body 与输入对应段长度相当（不超过约 1.5 倍）——研判段落是卡片版式里的
  定长格子，超长会挤破版面节奏；模块 `prompt_hint` 可对特殊段给更紧的字数（如 sig-row 格 ≤ 40 字）。
- 禁止：出现 facts JSON 之外的任何数字；编造数据；推翻事实给出的结论方向（事实说 contango 不得写 backwardation）。
- 输出：与输入同构的 JSON，只改写 body 文字，title 可微调但不得超过输入集合。

模块私有 `prompt_hint`（存各 `*_analysis.py`）：说明本模块 sections 的业务含义与文风（如 rates 的 yield_curve 段重指标、overview 段重叙事连贯）。

## 4. rates 试点改动（~15 行）

`src/rates_analysis.py`：

```python
_LLM_ENABLED = llm_enabled()   # 替换硬编码 False

def _llm_generate() -> dict | None:
    if not _LLM_ENABLED:
        return None
    facts = {"yield_curve": yield_curve_analysis(), "overview": overview_analysis()}
    return generate_narrative("rates", facts, RATES_PROMPT_HINT)
```

调用方 `generate_analysis()` 改动从零改为一行：LLM 已启用但回落时置 `generator: "rules-fallback"`（区别于未接入的 `"rules"`，见 §5 页脚三态）。

## 5. 配置与部署

- `.env`：`LLM_API_KEY=sk-...`（本地，config.py 已自动加载 .env）；`LLM_BASE_URL` / `LLM_MODEL` 可选，默认 DeepSeek。
- GitHub secret `LLM_API_KEY` → `deploy-pages.yml` 的 export step 注入 env（只此一个 workflow）。
- 换供应商两档：OpenAI 兼容协议的（OpenAI / Kimi / Qwen / GLM / MiniMax…）改三个 env 即零代码切换；
  Anthropic / Gemini 原生 API 协议不同，需在 `src/llm.py` 加 ~30 行适配分支，模块层与校验层不动。
- key 失效/欠费：`llm_enabled()` False 或调用异常 → 静默回落规则引擎，stderr 留一行警告。
- **fail-open 故障矩阵**（任何一格出问题，行为一致：回落规则引擎，构建不红，异常不外抛）：

  | 故障 | 拦截点 | 用户可见信号 |
  |---|---|---|
  | key 未填 | `llm_enabled()` → False，零网络调用 | 研判区徽章「规则引擎」（中性） |
  | key 填错 / 欠费（401/402/403） | HTTP 异常 → except → None | 研判区徽章「已回落规则引擎」（橙）+ stderr 标记 |
  | 模型名错 / 参数错（400/404） | 同上 | 同上 |
  | 网络不通 / 超时（timeout=60s） | 同上 | 同上 |
  | 限流 429 / 5xx | 同上（v1 不重试，同日下次部署自愈） | 同上 |
  | 返回非 JSON / 围栏剥不开 | 解析异常 → None | 同上 |
  | 键序错 / 结构坏 / 幻觉数字 / 超长 | `validate_sections` → None | 同上 |

  三个设计性质：① 兕底路径与规则引擎常规路径走**同一份代码**（facts 收集失败则两边一起失败），
  降级可靠性 = 现网已验证的可靠性，不存在「第二实现」；② 故障**模块级隔离**，一页回落不影响其他页，
  各页页脚各说各的，不假装一致；③ 同一次部署允许混合状态（rates=llm、credit=rules）。

- **CI 主动报警（deploy-pages.yml +2 行）**：export step 改 `2>&1 | tee /tmp/export.log`
  （首行 `set -o pipefail`，防管道吞掉非零退出码），后续 step
  `grep -q LLM_FALLBACK /tmp/export.log && echo "::warning::LLM 研判回落规则引擎" || true`
  → run 上出现橙色告警注解、job 保持绿（不 exit 1：部署已成功，标红会为非问题报警）。
  key 配错当天就能看见，不依赖你恰好在看页脚。
- **研判徽章三态（贴研判区，不复用页头 TIERS 徽章位置，试点一起做）**：
  后端仍需第三态 `generator: "rules-fallback"`（LLM 已启用但回落，见 §4）；前端单源：

  | generator | 徽章 | 色 | tooltip |
  |---|---|---|---|
  | `llm` | LLM | brand（`--accent-ink`） | LLM 基于规则引擎事实改写叙事；数字经一致性校验，超限自动回落 |
  | `rules` | 规则引擎 | 中性（`--text-dim`） | 规则引擎确定性生成（该专题尚未接入 LLM） |
  | `rules-fallback` | 已回落规则引擎 | warning 橙（同 est） | LLM 调用失败或校验未过，已自动回落；详见部署日志 LLM_FALLBACK |

  **位置：研判区附近，不放页头**（窄屏页头是每段独占一行的时效行，加段会多一行，
  且徽章脱离研判上下文语义悬空）。徽章样式复用 `.re-tier` 胶囊（圆角胶囊 + tooltip），
  单源 helper `R.genBadge(generator)` 返回徽章 HTML（内置 GEN_DEF 三态表），两种挂点：
  ① 静态研判小节（有 Section 标题）→ `slot="head"`，与现有状态胶囊同款位置；
  ② JS 动态卡片组（如 rates 首页 `#re-sections` 四段 judgeCard，无静态标题）→ 卡组上方
  一行 12px 弱化注记「研判生成 [徽章]」，复用 `.re-sec-note` 样式，不新增卡片不加边框。
  页头 `setAsOf` 不动；页脚「研判生成」文字行退役（同页不重复两次），`R.foot` 的
  gen 参数保留传 null 向后兼容。无研判段的纯数据页不调 `genBadge` 即不渲染。
- 网络直连：api.deepseek.com 本地与 Actions（美国出口）均可直连，无需代理。

## 6. 测试（`tests/test_llm_narrative.py`）

全部 mock HTTP，不真调 API：

1. mock 合规返回 → `generate_analysis()` 得 `generator: "llm"`。
2. mock 返回含 facts 之外的数字 → 回落 `generator: "rules"`。
3. key 缺失 / 请求超时 / JSON 解析失败 → `rules`。
4. 数字归一化用例：`3.9` vs `3.90`、千分位 `1,234` 放行；`4.1`（无中生有）拦截。
5. 超长 body（> 1.5 × 输入长度）→ 回落 `rules`；恰在限内 → 通过。
6. 真链路手动验证：`LLM_API_KEY=... uv run python -m src.rates_analysis`（现有自检入口）。

## 7. 成本量级

rates 试点 2 段/次 × ~15 次部署/天 ≈ 30 次调用/天；deepseek-chat 计价下每天几毛钱、每月个位数人民币。全铺 10 模块约 ×5，仍在忽略不计区间。

## 8. 铺开（试点验收后）

- 每模块 `_llm_generate()` 换成同款薄封装：`facts` = 该模块各段规则引擎输出，`prompt_hint` 一段话。每模块 3-5 行。
- `liquidity` 的 `_llm_generate_dashboard`、`assets` 的 cross/equity/radar 多段模块同构适用。
- 纯数据表页（bonds/commodities/etfs/fx/positioning）不接——代码已注明无叙事。

## 9. 明确不做

- 运行时调用（Cloudflare Worker 代理 / 浏览器直调）：静态站 + 日更数据用不上，白引入 key 暴露面与按 PV 成本。
- LLM 自由研判原始 CSV：幻觉风险与 token 成本都高，留待「叙事+定性结论」档跑稳后再评估。
- 多模型 fallback / 段落级选择性重写：v1 不需要（内容寻址缓存已解决重复调用，见 §1）。
