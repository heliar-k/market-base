"""Coinglass 全市场衍生品聚合快照（timsun 衍生品页 Coinglass 模块数据源）。

Coinglass 官网 JS 渲染 + 反爬，直连抓不到；经 Jina Reader (r.jina.ai) 抓取
https://www.coinglass.com/open-interest/BTC 的 Markdown 渲染文本并解析：
  - BTC 全市场期货 OI（OI 分布表 "All" 行：BTC 等价 + USD）
  - 全加密市场 OI（导航栏 Open Interest 全局值，注意与 BTC OI 区分）
  - 24h 清算总额 + 变化
  - 24h 多空比（Long/Short 百分比）
  - 交易所 OI 分布（页面顺序前 8 家：OI BTC / OI USD / 份额 / 24h 变化）

写入 data/coinglass/{date}.json（覆盖写，每日 Actions 跑）。
单个字段抓不到就跳过该字段，不放弃整个快照。

用法:
    uv run python -m src.fetchers.coinglass_fetcher
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from src.config import ROOT
from src.fetchers.jina_reader import jina_fetch

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.coinglass.com/open-interest/BTC"
DATA = ROOT / "data" / "coinglass"

_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}

# 导航栏：仅匹配 "$数字 变化%" 形态，避免误匹配正文的 "Open Interest" 字样
_NAV_OI_RE = re.compile(r"\[Open Interest \$([\d,]+) ([+-][\d.]+)%\]")
_NAV_LIQ_RE = re.compile(r"\[24h Liquidation \$([\d,]+) ([+-][\d.]+)%\]")
_NAV_LS_RE = re.compile(r"\[24h Long/Short ([\d.]+)%/([\d.]+)%\]")

# 交易所行（页面 OI 表）：
#   两行式: 1![Image 4: CME](url)\nCME 125.95K BTC$9.74B 17.56%+0.31%+0.05%+2.01%0.8277
#   一行式: 2[![Image 5: Binance](url) Binance](url)141.88K BTC$10.96B
#           19.77%+0.22%-0.19%-0.08%0.845[Binance](url)
# 组: 序号, 名称(alt), OI(BTC), OI(USD), 份额%, 1h/4h/24h 变化%
_ROW_RE = re.compile(
    r"(?m)^(\d+)\[?!\[Image \d+: ([^\]]+)\]\([^)]*\)[^\n]*?\]?(?:\([^)]*\))?"
    r"\s*(?:[A-Za-z][A-Za-z0-9.]*\s+)?([\d.]+[KM]?) BTC\$([\d.]+[KMB]?) "
    r"([\d.]+)%((?:[+-][\d.]+%)+)"
)
# 合计行: All 717.29K BTC$55.45B 100%+0.19%-0.37%-1.61%0.9345
_ALL_RE = re.compile(r"(?m)^All\s+([\d.]+[KM]?)\s+BTC\$([\d.]+[KMB]?)\s+([\d.]+)%")


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


def parse_top(content: str) -> dict:
    """导航栏：全市场 OI / 24h 清算 / 24h 多空比。单个字段缺失只跳过该字段。"""
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


def parse_oi_table(content: str) -> dict:
    """OI 分布表：BTC 合计（All 行）+ 交易所明细（前 8 家，页面顺序）。

    逐行解析：坏行智能跳过，不影响好行：
    整行不匹配（如 OI/份额字段 N/A）不计数，靠“<8 家告警”兜底；
    匹配但 token 异常（如多小数点）计入 skipped；
    变化列数 ≠3（页面加列/删列 → 列漂移）弃行并告警，防止 24h 值错标；
    匹配数 < 8 时 log warning（Jina 布局变化时静默丢数据的防线）。
    """
    out: dict = {}
    m = _ALL_RE.search(content)
    if m:
        out["btc_oi_btc"] = round(_qty(m.group(1)), 2)
        out["btc_oi_usd"] = round(_qty(m.group(2)), 2)
    rows = []
    skipped = 0
    drifted = 0
    for m in _ROW_RE.finditer(content):
        chg_tokens = re.findall(r"[+-][\d.]+%", m.group(6))
        if len(chg_tokens) != 3:  # 1h/4h/24h 恰 3 列；加列/删列 → 列漂移，弃行防错标
            drifted += 1
            continue
        name, oi, usd, share = m.group(2), m.group(3), m.group(4), m.group(5)
        try:
            rows.append(
                {
                    "name": name,
                    "oi_btc": round(_qty(oi), 2),
                    "oi_usd": round(_qty(usd), 2),
                    "share_pct": _pct(share),
                    "chg_1d_pct": _pct(chg_tokens[-1]),  # 第 3 列 = 24h
                }
            )
        except ValueError:
            # 多小数点等非法 token（regex 字符类较宽）→ 跳该行不丢整表
            skipped += 1
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
    """解析 Jina 渲染的 Coinglass Markdown → 快照 dict（字段可缺）。"""
    snap: dict = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "Coinglass 全市场衍生品聚合",
    }
    for parser in (parse_top, parse_oi_table):
        try:
            snap.update(parser(content))
        except Exception as e:
            logger.warning("解析失败 %s: %s", parser.__name__, e)
    return snap


def fetch_snapshot() -> dict:
    """经 Jina Reader 抓取 Coinglass BTC OI 页并解析。"""
    return parse_snapshot(jina_fetch(PAGE_URL))


def main() -> None:
    snap = fetch_snapshot()
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{datetime.now():%Y%m%d}.json"
    path.write_text(
        json.dumps(snap, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(f"快照 → {path}: 字段 {list(snap.keys())}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
