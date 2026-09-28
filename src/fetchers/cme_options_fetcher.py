"""CME BTC 期货期权墙快照（timsun 衍生品页"CME 机构期权"模块数据源）。

CME 官网是 Akamai + 重 JS（产品选择器不交互时表格渲染默认产品 KC 小麦），
经 Playwright 无头浏览器抓取
https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin/volume/options?productId=8875
（页面 Expiration 默认当前月，Trade Date 为空）：

  - Call/Put Total OI（Total 行第 8 个数值列）+ pcr_oi
  - call_wall/put_wall（按行权价聚合 OI 最大的行权价，同 strike 同侧 groupby 求和）
  - max_pain（标准 Max Pain：min_K Σ call_oi×max(0,K−k) + put_oi×max(0,k−K)）
  - top_calls/top_puts（OI 前 5，降序）
  - as_of（"Last Updated 22 Aug 2026 ..." → ISO 日期字符串）

写入 data/cme_options/{date}.json（覆盖写，每日 Actions 跑）。
解析失败/空内容返回 {}（不抛），单字段缺失只跳过该字段。

⚠ 2026-09 事故：此 fetcher 曾用 Jina Reader 抓页面，JS 不交互时表格永远渲染
默认产品（KC 小麦）——自 2026-08-23 上线起解析的一直是小麦期权（strike
2,500-18,750）。已改为 Playwright 真浏览器（daily-fetch 已装 chromium），
并保留 parse_options() 末端 sanity guard（total_oi>0 + 墙位 vs BTC 现价区间）
双保险，校验不过拒绝落盘。

本地注意：cmegroup.com 在本机网络不可直连（HTTP2 reset），此 fetcher 设计为
Actions（美国 IP）运行；本地调试需能访问 CME 的网络环境。

用法:
    uv run python -m src.fetchers.cme_options_fetcher
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import pandas as pd

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
    """Playwright 真浏览器抓 CME BTC 期权页，返回 Jina Markdown 同构文本。

    CME volume/options 页是 Akamai + 重 JS：产品选择器不交互时表格渲染默认
    产品（KC 小麦）——Jina 快照永远是小麦（该源曾因此发了一个月小麦数据）。
    真浏览器带 productId=8875 加载，若表格仍非 BTC 量级则尝试原生 select
    选择「Options on Bitcoin Futures」，最后把 DOM 行合成为 Jina Markdown
    同构文本（`| strike Call | …9 列… |` / `Call Total …` / `Last Updated …`），
    parse_options 与 sanity guard 不变。渲染结果仍由 guard 兜底，错产品拒落盘。
    """
    from playwright.sync_api import sync_playwright

    last_err: Exception | None = None
    for _ in range(3):  # Akamai 对数据中心 IP 间歇性 HTTP2 reset，换 context 重试
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(
                    f"{PAGE_URL}?productId=8875",
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )
                page.wait_for_selector("table", timeout=45_000)
                page.wait_for_timeout(5_000)
                if not _looks_like_btc(page):
                    _select_bitcoin_product(page)
                return _extract_markdown(page)
            except Exception as e:
                last_err = e
                logger.warning("CME 页面抓取重试：%s", str(e)[:120])
            finally:
                browser.close()
    raise last_err  # type: ignore[misc]


def _table_rows(page) -> list[list[str]]:
    """提取页面表格行（含 Total 行）。"""
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('table tr'))
            .map(r => Array.from(r.querySelectorAll('td,th'))
                .map(c => c.innerText.trim()))
            .filter(cells => cells.length >= 2)"""
    )


def _looks_like_btc(page) -> bool:
    """表格 strike 量级校验：BTC 期权 strike ≥ 20000；小麦表最大 ~18750。"""
    for cells in _table_rows(page):
        m = re.match(r"^([\d,]+(?:\.\d+)?)\s+(Call|Put)$", cells[0])
        if m and float(m.group(1).replace(",", "")) >= 20_000:
            return True
    return False


def _row_oi(cells: list[str]) -> str:
    """CME 表 OI 在倒数第二列（「At Close」，末列是 Change 可能为负数/粘连）。"""
    for c in (cells[-2:-1] or []) + list(reversed(cells[1:-2])):
        v = c.replace(",", "")
        if v.isdigit():
            return v
    return "0"


def _select_bitcoin_product(page) -> None:
    """原生 select 里选「Options on Bitcoin Futures」并等表格重渲染。"""
    changed = page.evaluate(
        """() => {
            const want = o => /Options on Bitcoin Futures/i.test(o.text);
            const sel = Array.from(document.querySelectorAll('select'))
                .find(s => Array.from(s.options).some(want));
            if (!sel) return false;
            sel.value = Array.from(sel.options).find(want).value;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }"""
    )
    if not changed:
        logger.warning("CME 页未找到产品下拉，表格保持默认产品")
        return
    for _ in range(10):  # 最多等 20s 表格切到 BTC 量级
        page.wait_for_timeout(2_000)
        if _looks_like_btc(page):
            return
    logger.warning("选择 Bitcoin 产品后表格仍未切到 BTC 量级")


def _extract_markdown(page) -> str:
    """DOM 行 → Jina Markdown 同构文本（parse_options 不变）。

    Jina 表格行格式：`| {strike} {Call|Put} | {8 列数值，OI 为第 8 列} |`；
    只保 parser 用到的列（OI / Total OI），其余列补 0。CME DOM 行首列形如
    「105000 Call」，Total 行含「Call Total」。
    """
    lines: list[str] = []
    for cells in _table_rows(page):
        first = cells[0].replace("\n", " ")
        m = re.match(r"^([\d,]+(?:\.\d+)?)\s+(Call|Put)$", first)
        if m:
            strike_f = float(m.group(1).replace(",", ""))
            # parser 的 _ROW_RE 只认整数 strike（CME BTC 行权价均为整数）
            strike = str(int(strike_f)) if strike_f.is_integer() else str(strike_f)
            oi = _row_oi(cells)
            lines.append(
                f"| {strike} {m.group(2)} | 0 | 0 | 0 | 0 | 0 | 0 | 0 | {oi} | 0 |"
            )
            continue
        mt = re.match(r"^(Call|Put)\s+Total$", first)
        if mt:
            lines.append(f"{mt.group(1)} Total 0 0 0 0 0 0 0 {_row_oi(cells)} 0")
    body = page.evaluate("document.body.innerText")
    upd = re.search(r"Last Updated\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", body or "")
    if upd:
        lines.insert(0, f"Last Updated {upd.group(1)} 12:00:00 AM CT.")
    return "\n".join(lines)


def _strike_rows(content: str) -> list[tuple[int, str, int]]:
    """解析表格数据行 → [(strike, side, oi)]，OI 为第 8 个数值列。"""
    rows: list[tuple[int, str, int]] = []
    for line in content.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        nums = [
            int(c.replace(",", ""))
            for c in cells[2:]
            if c.lstrip("+-.").replace(",", "").isdigit()
        ]
        # 数据行共 9 个数（g,oo,pnt,tot_vol,blk,eoo,at_close,oi,chg），oi 是第 8 个；
        # 恰 9 列才接受（列漂移防护：少于/多于 9 列弃行，行数不足由上层告警兜底）
        if len(nums) == 9:
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


def _btc_spot() -> float | None:
    """BTC 现价锚（yfinance 资产日线，与全站价格面板同源）；取不到返回 None。"""
    path = ROOT / "data" / "yfinance" / "asset_prices.csv"
    if not path.exists():
        return None
    try:
        s = pd.read_csv(path)["BTC"].dropna()
        return float(s.iloc[-1]) if len(s) else None
    except Exception:
        return None


def snapshot_plausible(out: dict, spot: float | None) -> bool:
    """快照合理性校验：CME 表格回落到默认产品（小麦）时 strike 量级完全不符。

    规则：total_oi 解析出且为 0（已到期月份清零）→ 拒；无现价锚 → 拒
    （本源已失信，宁可空缺不可误导）；墙位须在 [0.15x, 6x] 现价区间内。
    """
    if "total_oi" in out and not out["total_oi"]:
        return False
    if not spot:
        return False
    return all(
        0.15 * spot <= out[k] <= 6 * spot
        for k in ("call_wall", "put_wall")
        if out.get(k) is not None
    )


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
        # Preliminary 阶段 Total 行可能含 "N/A"（int 会崩）→ 非数字过滤
        nums = [
            int(t.replace(",", ""))
            for t in m.group(2).split()
            if t.lstrip("+-.").replace(",", "").isdigit()
        ]
        if len(nums) >= 8:
            totals[m.group(1)] = nums[7]
    if "Call" in totals:
        out["call_total_oi"] = totals["Call"]
    if "Put" in totals:
        out["put_total_oi"] = totals["Put"]
    if "Call" in totals and "Put" in totals:
        call_total, put_total = totals["Call"], totals["Put"]
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

    # 最小完整性门槛：totals 与墙都缺 → 视为解析失败（不覆盖当日好快照）；
    # totals 与墙任一存在即可（Preliminary 阶段可能只有 strike 行或只有 Total）
    if not rows and not totals:
        return {}
    if not totals and not call_oi and not put_oi:
        logger.warning("CME 期权解析不完整（rows=%d），放弃写入", len(rows))
        return {}
    if not snapshot_plausible(out, _btc_spot()):
        logger.error(
            "CME 期权快照未通过合理性校验（total_oi=%s call_wall=%s put_wall=%s）"
            "——疑似页面回落到默认产品表，拒绝落盘",
            out.get("total_oi"),
            out.get("call_wall"),
            out.get("put_wall"),
        )
        return {}
    return out


def main() -> None:
    try:
        content = fetch_page()
    except Exception as e:  # 浏览器/网络失败：保昨日快照，freshness 监控会报红
        logger.error("CME 页面抓取失败：%s", e)
        return
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
