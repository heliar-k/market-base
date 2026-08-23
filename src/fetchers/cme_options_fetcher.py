"""CME BTC 期货期权墙快照（timsun 衍生品页"CME 机构期权"模块数据源）。

CME 官网反爬，经 Jina Reader (r.jina.ai) 抓取
https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin/volume/options
的 Markdown 渲染文本并解析（页面 Expiration 默认当前月，Trade Date 为空）：

  - Call/Put Total OI（Total 行第 8 个数值列）+ pcr_oi
  - call_wall/put_wall（按行权价聚合 OI 最大的行权价，同 strike 同侧 groupby 求和）
  - max_pain（标准 Max Pain：min_K Σ call_oi×max(0,K−k) + put_oi×max(0,k−K)）
  - top_calls/top_puts（OI 前 5，降序）
  - as_of（"Last Updated 22 Aug 2026 ..." → ISO 日期字符串）

写入 data/cme_options/{date}.json（覆盖写，每日 Actions 跑）。
解析失败/空内容返回 {}（不抛），单字段缺失只跳过该字段。

用法:
    uv run python -m src.fetchers.cme_options_fetcher
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

PAGE_URL = (
    "https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin/volume/options"
)
DATA = ROOT / "data" / "cme_options"

# 表格数据行：| {strike} {Call|Put} | ...
_ROW_RE = re.compile(r"^\|\s*(\d+)\s+(Call|Put)\s*\|")
# Total 行：Call Total 83 0 0 83 0 0 0 458 44（OI 是第 8 个数值列）
_TOTAL_RE = re.compile(r"^(Call|Put)\s+Total\s+(.+)$")
# Last Updated 22 Aug 2026 12:21:19 AM CT.
_UPDATED_RE = re.compile(r"Last Updated\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")


def fetch_page() -> str:
    """经 Jina Reader 抓取 CME BTC 期权页，返回 Markdown 文本。"""
    proxies = (
        {"https": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    )
    resp = requests.get(
        f"https://r.jina.ai/{PAGE_URL}",
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0", "x-timeout": "30", "x-no-cache": "true"},
        proxies=proxies,
    )
    resp.raise_for_status()
    return resp.text


def _strike_rows(content: str) -> list[tuple[int, str, int]]:
    """解析表格数据行 → [(strike, side, oi)]，OI 为第 8 个数值列。"""
    rows: list[tuple[int, str, int]] = []
    for line in content.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        nums = [int(c) for c in cells[2:] if c.lstrip("+-").isdigit()]
        # 数据行共 9 个数（g,oo,pnt,tot_vol,blk,eoo,at_close,oi,chg），oi 是第 8 个
        if len(nums) >= 8:
            rows.append((int(m.group(1)), m.group(2), nums[7]))
    return rows


def _top(n: int, d: dict[int, int]) -> list[dict]:
    """OI 前 n 的行权价列表 [{strike, oi}]，降序、同 OI 按行权价升序。"""
    return [
        {"strike": k, "oi": oi}
        for k, oi in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    ]


def _parse_max_pain(
    strikes: list[int], call_oi: dict[int, int], put_oi: dict[int, int]
) -> dict | None:
    """标准 Max Pain：argmin_K Σ call_oi×max(0,k−kk) + put_oi×max(0,kk−k)
    （k=候选行权价，kk=遍历行权价；call 在行权价下方有损失，put 在上方有损失）。
    """
    ks = sorted(set(strikes))
    best: dict | None = None
    for k in ks:
        pain = sum(
            call_oi.get(kk, 0) * max(0, k - kk) + put_oi.get(kk, 0) * max(0, kk - k)
            for kk in ks
        )
        if best is None or pain < best["pain"]:
            best = {"strike": k, "pain": pain}
    return best


def parse_options(content: str) -> dict:
    """解析 Jina 渲染的 CME Markdown → 快照 dict（字段可缺，空内容返回 {}）。"""
    rows = _strike_rows(content)
    out: dict = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "CME BTC 期货期权墙",
    }

    m = _UPDATED_RE.search(content)
    if m:
        out["as_of"] = datetime.strptime(m.group(1), "%d %b %Y").strftime("%Y-%m-%d")

    call_oi: dict[int, int] = {}
    put_oi: dict[int, int] = {}
    for strike, side, oi in rows:
        bucket = call_oi if side == "Call" else put_oi
        bucket[strike] = bucket.get(strike, 0) + oi

    totals: dict[str, int] = {}
    for line in content.splitlines():
        m = _TOTAL_RE.match(line.strip())
        if not m:
            continue
        nums = [int(t) for t in m.group(2).split()]
        if len(nums) >= 8:
            totals[m.group(1)] = nums[7]
    if "Call" in totals and "Put" in totals:
        call_total, put_total = totals["Call"], totals["Put"]
        out["call_total_oi"] = call_total
        out["put_total_oi"] = put_total
        out["total_oi"] = call_total + put_total
        out["pcr_oi"] = round(put_total / call_total, 2) if call_total else None

    if call_oi:
        k, oi = min(call_oi.items(), key=lambda kv: (-kv[1], kv[0]))
        out["call_wall"] = k
        out["call_wall_oi"] = oi
    if put_oi:
        k, oi = min(put_oi.items(), key=lambda kv: (-kv[1], kv[0]))
        out["put_wall"] = k
        out["put_wall_oi"] = oi

    if rows:
        out["top_calls"] = _top(5, call_oi)
        out["top_puts"] = _top(5, put_oi)
        mp = _parse_max_pain(list(call_oi) + list(put_oi), call_oi, put_oi)
        if mp:
            out["max_pain"] = mp

    if not rows and not totals:
        return {}
    return out


def main() -> None:
    content = fetch_page()
    snap = parse_options(content)
    if not snap:
        logger.error("解析失败：页面为空或无表格结构")
        return
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
