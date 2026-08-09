# 计划：补齐资金层数据源（FINRA 沽空量 + SEC Form 4 内部人交易）

> 背景：`planning-trades` skill 阶段 0「资金流」项目前标注"暂无 fetcher，跳过"。
> 两个数据源均已实测可达、免费公开，本计划交给执行 agent 落地。
> 完成后资金层从"可选/定性"变成全自动，并回改 SKILL.md 去掉跳过标注。

## 项目约定（必须遵守）

- `uv run` 执行一切 Python；禁止 `sys.path.insert`/PYTHONPATH hack
- fetcher 放 `src/fetchers/xxx_fetcher.py`，实现 `fetch_*() -> DataFrame`，CLI 用 `uv run python -m src.fetchers.xxx_fetcher`
- bin 包装照抄 `bin/fetch_cot`（5 行 bash）
- 宽表 upsert 复用 `src/fetchers/_io.py` 的 `upsert_timeseries`（参考 `cot_fetcher.py`，列命名 `{SYM}_{METRIC}`）
- 注释/文档用中文，函数签名带类型注解
- 测试放 `tests/`，`tmp_path` 隔离，**不碰网络**（用样例字符串测解析）；跑 `uv run python -m pytest` 全量
- 完成后更新 `docs/DATA_CATALOG.md` + `.github/workflows/daily-fetch.yml` 第 35 行的循环列表
- commit 风格：`feat(fetchers): ...`（中文描述）

---

## 任务 A：FINRA 每日沽空量（`finra_fetcher.py`）

### 数据源（已实测 2026-08-09）

```
GET https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
```

- T+1 发布，免费，无需 key
- **实测：中国大陆 IP 直连被 WAF 拦（307 → error.waf.finra.org）；走项目 SOCKS5 代理（`127.0.0.1:7890`，`.env` 的 `https_proxy`）→ 200 OK**
- GitHub Actions runner 是美国 IP，**大概率直连即可**。实现顺序：**先直连，307/403/超时 → fallback 读 `https_proxy` env 走代理**（需要 PySocks，先查 `pyproject.toml` 有没有，没有 `uv add pysocks`）
- 实测返回样例（pipe 分隔，首行表头）：

```
Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260807|A|316055.595634|21|554553.519523|B,Q,N
```

⚠️ 注意 ShortVolume/TotalVolume 出现小数的怪象——先打几行确认是否为常态，若普遍为小数需查证该文件口径（可能是按 TRF 加权），把结论写进模块 docstring。

- 周末/假日无文件（404）→ 跳过不报错
- 历史深度未知：**首次运行时向前二分探测**能拿到多久（预计 1-2 年），backfill 全量后每日增量

### 处理逻辑

1. watchlist = `data/stocks/*.csv` 的文件名集合（项目交易宇宙），`--symbols` 可覆盖
2. 解析全市场文件，过滤 watchlist，计算 `short_ratio = ShortVolume / TotalVolume`
3. 写 `data/short_selling/finra_daily.csv`，宽表 upsert（观测日 = 交易日期）：
   列 `{SYM}_short_ratio`、`{SYM}_short_vol`（复用 `upsert_timeseries`）

### CLI

```bash
./bin/fetch_finra                    # 最近 5 个交易日增量（缺啥补啥）
./bin/fetch_finra --backfill         # 探测历史深度并全量
./bin/fetch_finra --symbols TSM,MU   # 指定标的
```

---

## 任务 B：SEC Form 4 内部人交易（`insider_fetcher.py`）

### 数据源（已实测 2026-08-09）

```
GET https://www.sec.gov/files/company_tickers.json        # ticker → CIK 映射（每次运行拉一次，不缓存）
GET https://data.sec.gov/submissions/CIK{10位CIK}.json     # 公司近期 filings（实测 AAPL 含 587 条 Form 4）
GET https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession去横线}/index.json   # filing 内文件列表
GET https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession去横线}/{xxx.xml}    # ownership XML
```

- 免费，**必须带 User-Agent 且含联系方式**（如 `market-base/1.0 (联系邮箱)`，参考 `sec_fetcher.py` 现有写法）
- 限速 ≤10 req/s，请求间 `sleep(0.2)`
- 复用 `sec_fetcher.py` 的 EDGAR 基建（UA、重试、CIK 处理），别重造

### 处理逻辑

1. watchlist 同上（`data/stocks/*.csv`），`--symbols` 覆盖；默认回溯 2 年（对齐 sec_fetcher）
2. submissions JSON 过滤 `form in ("4", "5")`（5 = 年报补漏）
3. 每条 filing 按 accession 去重（已存则跳过）→ 拉 index.json 找 ownership XML → 解析：
   - `reportingOwnerName` / `officerTitle`（或 director）
   - `nonDerivativeTransaction`：`transactionDate`、`transactionCoding.code`、`transactionShares`、`transactionPricePerShare`、`sharesOwnedFollowing`
   - 交易代码：**P=公开市场买入、S=公开市场卖出**（只有这两个算信号）；A=授予 M=行权 F=缴税代扣 G=赠与（记录但不算信号）
4. 写 `data/insider/{SYMBOL}.csv` 长表：
   `filing_date, transaction_date, insider_name, title, code, shares, price, value, shares_after, accession`
   按 accession 去重 upsert（`_io` 的助手是 date-keyed，这里自己写个简单的 accession 集合去重即可）
5. CLI 结尾打印每标的**近 90 天 open-market 净买卖汇总**（P 减 S 的金额），这是 skill 要用的信号

### CLI

```bash
./bin/fetch_insider                  # 全部持仓标的增量
./bin/fetch_insider --symbols AAPL --days 730
```

---

## 收尾清单（执行 agent 逐项勾）

- [ ] `src/fetchers/finra_fetcher.py` + `bin/fetch_finra` + `tests/test_finra_fetcher.py`
- [ ] `src/fetchers/insider_fetcher.py` + `bin/fetch_insider` + `tests/test_insider_fetcher.py`
- [ ] `.github/workflows/daily-fetch.yml` 循环列表加 `fetch_finra fetch_insider`
- [ ] `docs/DATA_CATALOG.md` 加两个数据源条目
- [ ] `.agents/skills/planning-trades/SKILL.md` 阶段 0 第 3 项：去掉"暂无 fetcher/跳过"标注，改为指向 `./bin/fetch_finra` 和 `./bin/fetch_insider`（数据路径 `data/short_selling/`、`data/insider/`）
- [ ] `uv run python -m pytest` 全绿；两个 bin 脚本本地各实测跑一次（FINRA 注意验证代理 fallback）

## 验收标准

```bash
./bin/fetch_finra --symbols AAPL,TSM --backfill   # 产出 data/short_selling/finra_daily.csv
./bin/fetch_insider --symbols AAPL                # 产出 data/insider/AAPL.csv + 90 天汇总打印
```

工作量估计：每个 fetcher 150–250 行 + 50 行测试，合计半天。
