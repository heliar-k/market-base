"""Coinglass 全市场衍生品聚合快照（timsun 衍生品页 Coinglass 模块数据源）。

Coinglass 官网 JS 渲染 + 反爬，直连只拿到空壳 HTML；渲染成 Markdown 再解析
https://www.coinglass.com/open-interest/BTC：
  - BTC 全市场期货 OI（OI 分布表 "All" 行：BTC 等价 + USD）
  - 全加密市场 OI（导航栏 Open Interest 全局值，注意与 BTC OI 区分）
  - 24h 清算总额 + 变化
  - 24h 多空比（Long/Short 百分比）
  - 交易所 OI 分布（页面顺序前 8 家：OI BTC / OI USD / 份额 / 24h 变化）

渲染通道：Cloudflare Browser Run（主）→ Firecrawl scrape（兜底）。两家都出 markdown，
排版差异由 _normalize 抹平，解析层只写一套（实际验证：151KB 与 153KB 两份输出解析出相同字段）。

写入 data/coinglass/{date}.json（覆盖写，crypto-hourly 每 6 小时一次 + daily-full 兜底）。
页面渲染不全（缺任一必需字段或交易所行不足 8 家）就抛错不写文件：过一小时的下一班还会重试，
留一个只有 ts/title 的空快照反而把当日模块静默置空。

环境变量：CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_BROWSER_TOKEN（需 Browser Run - Edit 权限）、
FIRECRAWL_API_KEY（兜底通道）。

用法:
    uv run python -m src.fetchers.coinglass_fetcher
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests

from src.config import ROOT

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.coinglass.com/open-interest/BTC"
DATA = ROOT / "data" / "coinglass"

# Browser Run 的 markdown 快扢動作（主通道）；Firecrawl scrape（兜底通道）
_CF_RENDER_URL = "https://api.cloudflare.com/client/v4/accounts/{acct}/browser-run/markdown"
_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}

# 导航栏：仅匹配 "$数字 变化%" 形态，避免误匹配正文的 "Open Interest" 字样
_NAV_OI_RE = re.compile(r"\[Open Interest\s+\$([\d,]+)\s+([+-][\d.]+)%\]")
_NAV_LIQ_RE = re.compile(r"\[24h Liquidation\s+\$([\d,]+)\s+([+-][\d.]+)%\]")
_NAV_LS_RE = re.compile(r"\[24h Long/Short\s+([\d.]+)%\s*/\s*([\d.]+)%\]")

# 交易所行（Browser Run 渲染出的 markdown 管道表，一格一个字段）：
#   | 1 | ![CME](url) CME | 92.56K BTC | $5.47B | 14.09% | -0.07% | -0.47% | +1.42% | 0.8124 | |
#   |  | ![All](url) All | 656.28K BTC | $38.78B | 100% | +0.04% | +0.72% | +1.48% | 0.6039 | |
# 列序：序号 / 交易所 / OI(BTC) / OI(USD) / 份额% / 1h / 4h / 24h / OI 比 / Trade
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PCT_CELL_RE = re.compile(r"^[+-][\d.]+%$")
# 两家渲染器的写法差异：CF 把导航栏排成一行，Firecrawl 用 `\\` 硬换行拆成多行；
# 交易所名 CF 用空格分隔、Firecrawl 用 `<br>`。解析前先抹平。
_HARD_BREAK_RE = re.compile(r"(?:\\+\s*\n\s*)+")
_BR_RE = re.compile(r"\\*\s*<br\s*/?>", re.I)
# 表头名称 → 快照字段；按名称定位列，不靠下标
_HEADER_KEYS = {
    "name": "Exchanges",
    "oi_btc": "OI(BTC)",
    "oi_usd": "OI",
    "share": "Rate",
    "chg": "OI Change (24h)",
}


# 快照必备字段（缺任一即视为页面未渲染完，parse_snapshot 抛错）
_REQUIRED = (
    "all_open_interest_usd",
    "liq24h_usd",
    "ls_ratio",
    "btc_oi_btc",
    "btc_oi_usd",
    "exchanges",
)


def _qty(tok: str) -> float:
    """数量 token → 数值：'125.95K'→125950.0，'9.74B'→9.74e9，
    '992.38'→992.38（无单位）。"""
    m = re.fullmatch(r"([\d.]+)([KMB]?)", tok.strip())
    if not m:
        raise ValueError(f"无法解析数量: {tok!r}")
    return float(m.group(1)) * _UNIT.get(m.group(2), 1)


def _pct(tok: str) -> float:
    """百分比 token → 数值：'+2.01%'→2.01，'-1.61%'→-1.61（保留符号）。"""
    return float(tok.strip().rstrip("%"))


def _normalize(content: str) -> str:
    """抹平两家渲染器的排版差异（反斜杠硬换行 / `<br>` 分隔）后再交给正则。"""
    return _BR_RE.sub(" ", _HARD_BREAK_RE.sub(" ", content))


def parse_top(content: str) -> dict:
    """导航栏：全市场 OI / 24h 清算 / 24h 多空比。单个字段缺失只跳过该字段。"""
    content = _normalize(content)
    out: dict = {}
    m = _NAV_OI_RE.search(content)
    if m:
        out["all_open_interest_usd"] = int(m.group(1).replace(",", ""))
    m = _NAV_LIQ_RE.search(content)
    if m:
        out["liq24h_usd"] = int(m.group(1).replace(",", ""))
        out["liq24h_chg_pct"] = _pct(m.group(2))
    m = _NAV_LS_RE.search(content)
    if m:
        out["ls_ratio"] = {"long_pct": _pct(m.group(1)), "short_pct": _pct(m.group(2))}
    return out


def _qty_cell_btc(tok: str) -> str:
    """OI(BTC) 格 `92.56K BTC` → `92.56K`。"""
    return tok.strip().split(" ")[0]


def _qty_cell_usd(tok: str) -> str:
    """OI(USD) 格 `$5.47B` → `5.47B`。"""
    return tok.strip().lstrip("$")


def _name(cell: str) -> str:
    """交易所格去掉图片与链接壳：`[![Binance](img) Binance](url)` → `Binance`。"""
    return _LINK_RE.sub(r"\1", _IMG_RE.sub("", cell)).strip()


def parse_oi_table(content: str) -> dict:
    """OI 分布表：BTC 合计（All 行）+ 交易所明细（前 8 家，页面顺序）。

    按表头名称定位列，不写死下标：加/删列后仍能取对 24h 那一列；
    必需列（Exchanges / OI(BTC) / OI / Rate / OI Change (24h)）任一缺失就不出表并告警；
    行宽与表头不一致、或 24h 格不是百分比 → 弃行计 drifted；
    取值异常（N/A、脏 token）计 skipped；
    行数 < 8 时 log warning（表布局变化时静默丢数据的防线）。
    """
    out: dict = {}
    rows = []
    skipped = 0
    drifted = 0
    header: dict[str, int] | None = None
    width = 0
    for line in _normalize(content).splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if "Exchanges" in cells and "OI(BTC)" in cells:
                header = {k: cells.index(v) for k, v in _HEADER_KEYS.items() if v in cells}
                width = len(cells)
                missing_col = [v for k, v in _HEADER_KEYS.items() if k not in header]
                if missing_col:
                    logger.warning("Coinglass OI 表缺列 %s（页面可能改版）", missing_col)
                    return out
            continue
        if "Exchanges" in cells:  # 页面第二张表（空表头）重复出现列名
            continue
        if len(cells) != width:
            drifted += 1
            continue
        name = _name(cells[header["name"]])
        if not name:
            continue
        try:
            if name == "All":
                out["btc_oi_btc"] = round(_qty(_qty_cell_btc(cells[header["oi_btc"]])), 2)
                out["btc_oi_usd"] = round(_qty(_qty_cell_usd(cells[header["oi_usd"]])), 2)
                continue
            if not cells[0].isdigit():  # 分隔行
                continue
            chg_cell = cells[header["chg"]]
            if not _PCT_CELL_RE.match(chg_cell):
                drifted += 1
                continue
            rows.append(
                {
                    "name": name,
                    "oi_btc": round(_qty(_qty_cell_btc(cells[header["oi_btc"]])), 2),
                    "oi_usd": round(_qty(_qty_cell_usd(cells[header["oi_usd"]])), 2),
                    "share_pct": _pct(cells[header["share"]]),
                    "chg_1d_pct": _pct(chg_cell),
                }
            )
        except ValueError:
            # 多小数点等非法 token（regex 字符类较宽）→ 跳该行不丢整表
            skipped += 1
    if header is None:
        logger.warning("Coinglass 未找到 OI 分布表（表头缺失）")
    if skipped:
        logger.warning("Coinglass 交易所行跳过 %d 行（坏 token）", skipped)
    if drifted:
        logger.warning(
            "Coinglass 交易所行 %d 行变化列缺失/异常（<3 或 >3；"
            "整行 N/A 由 <8 告警兜底；若为头部行将影响 Top 8 名单）",
            drifted,
        )
    if rows:
        out["exchanges"] = rows[:8]
    if len(rows) < 8:
        # 0 行也告警：整表 N/A/改版时不留静默空窗
        logger.warning("Coinglass 交易所行仅 %d 家（<8，页面可能改版）", len(rows))
    return out


def parse_snapshot(content: str) -> dict:
    """解析渲染出的 Markdown → 快照 dict（CF 与 Firecrawl 两种排版都吃）。

    页面渲染不全时就地抛错：只写 ts/title 的空快照会让当日模块静默无数据，
    而过一小时的下一班还会重试。
    """
    snap: dict = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "Coinglass 全市场衍生品聚合",
    }
    snap.update(parse_top(content))
    snap.update(parse_oi_table(content))
    missing = [k for k in _REQUIRED if k not in snap]
    if missing:
        raise RuntimeError(f"Coinglass 页面渲染不全，缺 {missing}（响应 {len(content)} 字符）")
    if len(snap["exchanges"]) < 8:
        raise RuntimeError(f"Coinglass 交易所行仅 {len(snap['exchanges'])} 家（<8，页面可能改版）")
    return snap


def _render_cf(url: str, timeout: int) -> str:
    """Cloudflare Browser Run → markdown（主通道）。"""
    token = os.environ.get("CLOUDFLARE_BROWSER_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token or not account:
        raise RuntimeError("缺 CLOUDFLARE_BROWSER_TOKEN / CLOUDFLARE_ACCOUNT_ID，取不到渲染通道")
    resp = requests.post(
        _CF_RENDER_URL.format(acct=account),
        json={"url": url, "gotoOptions": {"waitUntil": "networkidle0"}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Browser Run 渲染失败: {data.get('errors') or data}")
    return data["result"]


def _render_firecrawl(url: str, timeout: int) -> str:
    """Firecrawl scrape → markdown（兜底通道，免费档 1000 credits/月）。"""
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 FIRECRAWL_API_KEY，兜底渲染通道不可用")
    resp = requests.post(
        _FIRECRAWL_URL,
        json={
            "url": url,
            "formats": ["markdown"],
            # 默认 true 会砍掉页头，而全市场 OI / 24h 清算 / 多空比就在页头那一行
            "onlyMainContent": False,
            "maxAge": 0,  # 默认 2 天会直接回缓存，数据在变必须现抓
            "waitFor": 3000,  # 等图表渲染完
            "timeout": 60000,
            "proxy": "auto",
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Firecrawl 抓取失败: {data.get('error') or data}")
    return data["data"]["markdown"]


def render_markdown(url: str = PAGE_URL, timeout: int = 180) -> str:
    """渲染页面 → markdown：Cloudflare Browser Run 主，Firecrawl 兜底。

    两家都输出 markdown，差异（`\\` 硬换行、`\\<br>` 分隔）由 _normalize 抹平；
    计费口径不同：Browser Run 按浏览器时长（免费档每天 10 分钟），Firecrawl 按次计 credit。
    """
    try:
        return _render_cf(url, timeout)
    except Exception as e:
        logger.warning("Browser Run 渲染失败（%s），改用 Firecrawl 兜底", e)
    return _render_firecrawl(url, timeout)


def fetch_snapshot() -> dict:
    """渲染 Coinglass BTC OI 页并解析。"""
    return parse_snapshot(render_markdown())


def main() -> None:
    snap = fetch_snapshot()
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{datetime.now():%Y%m%d}.json"
    path.write_text(
        json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    logger.info(f"快照 → {path}: 字段 {list(snap.keys())}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
