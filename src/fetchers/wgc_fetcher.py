"""世界黄金协会（WGC）Goldhub 数据抓取 —— 会话通道 + ETF flows 解析。

架构（调研 2026-10）：
- gold.org 的 Cloudflare 不拦匿名 GET（数据页/首页都公开），只拦**下载**：
  403 页面明确返回「DU1: You are not logged in」——纯登录墙，非指纹拦截。
- 登录在 user.gold.org（Drupal），表单挂 Cloudflare Turnstile → 无法纯 HTTP 登录。
  方案：patchright 持久化 profile（data/wgc/browser-profile，gitignore），
  bin/wgc_login 开真窗口人工登录一次，cookie 落在 profile 里长期复用。
- fetch 流程：匿名 GET 数据页 HTML（公开）→ 正则提取最新 ETF_Flows_*.xlsx 链接
  → 带 profile 会话下载 → 校验 DU1（会话过期时明确报错，不静默）→ 解析入库。
- WGC xlsx 是「区域 × 月份」的月度净流入（USD mn）+ 持仓（tonnes），
  详见 parse_etf_flows()；CSV 落 data/wgc/etf_flows.csv（观测月 upsert）。
  另解析 Holdings by month（全球合计 + GLD 持仓，吨）→ wgc_holdings.csv，
  与 All flows by fund（单基金快照）→ fund_flows_latest.json（覆盖写）。
  Demand by month 全球列有错位 artifact（值 = 上月持仓），不解析。

会话过期症状：下载响应含 "DU1" / 403 → 抛 SessionExpiredError，
提示重跑 bin/wgc_login。数据新鲜度由 src/data_freshness.py 兜底。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pandas as pd
import requests

from ..config import ROOT

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DATA_PAGE = "https://www.gold.org/goldhub/data/gold-etfs-holdings-and-flows"
PROFILE_DIR = ROOT / "data" / "wgc" / "browser-profile"
OUT_DIR = ROOT / "data" / "wgc"
XLSX_CACHE = OUT_DIR / "etf_flows_latest.xlsx"

REGIONS = ["North America", "Europe", "Asia", "Other"]

# Holdings/Demand by month 的全球合计列（Date / 金价 US$/oz / Ounces / Tonnes / Value）
GLOBAL_COLS = {"gold_usd_oz": 1, "global_tonnes": 3, "global_value_usd": 4}


class SessionExpiredError(RuntimeError):
    """WGC 会话过期/缺失（下载返回 DU1/403）。

    提示必须显眼：本地直接跑的人（不一定是 agent）要知道怎么修。
    """

    def __init__(self, detail: str = ""):
        msg = (
            "\n" + "=" * 62 + "\n"
            "🐝 WGC 登录会话已过期或缺失！\n"
            "   请在终端运行：  ./bin/wgc_login\n"
            "   浏览器窗口会弹出，登录一次即可（约 1 分钟）。\n"
            "   GitHub Actions 侧则需重新导出 WGC_COOKIES secret。\n" + "=" * 62 + "\n"
        )
        if detail:
            msg += f"\n细节：{detail}"
        super().__init__(msg)


# ── 浏览器会话（patchright 持久化 profile）─────────────────────────────────


def export_cookies() -> str:
    """从本地 profile 导出会话 cookie 头（贴到 GitHub secret WGC_COOKIES 用）。"""
    if not PROFILE_DIR.exists():
        raise SessionExpiredError("本地 profile 不存在，先跑 ./bin/wgc_login 登录一次")
    hdr = _session_cookie_header() if not os.environ.get("WGC_COOKIES") else None
    if not hdr:
        # _session_cookie_header 在设置了 WGC_COOKIES 时会优先用 env；强制读 profile
        from patchright.sync_api import sync_playwright

        auth_names = {
            "wgcAuth_session",
            "wgcAuth_cookie",
            "wgcApiAuth_cookie",
            "wgcAuthLang",
            "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d",
        }
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    str(PROFILE_DIR), headless=True, channel="msedge"
                )
            except Exception:
                ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True)
            cookies = ctx.cookies(["https://www.gold.org", "https://user.gold.org"])
            ctx.close()
        hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c["name"] in auth_names)
    if not hdr:
        raise SessionExpiredError("profile 里没有 auth cookie，重新登录")
    return hdr


def open_login_window() -> None:
    """开真浏览器窗口（持久 profile），人工完成登录 + Turnstile。

    轮询 cookie 自动检测登录成功（wgc_user / SSESS*，两域名都查），
    成功后访问数据页验证下载链接出现再关窗口；10 分钟超时退出。
    """
    import time

    from patchright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False, channel="msedge"
            )
        except Exception:
            ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.new_page()
        page.goto(
            "https://user.gold.org/login"
            "?destination=https%3A%2F%2Fwww.gold.org%2Fgoldhub%2Fdata"
            "%2Fgold-etfs-holdings-and-flows"
        )
        print("== 请在浏览器窗口完成登录（Turnstile / 扫码）==")
        print("== 脚本每 3 秒自动检测登录态，成功后自动关窗，无需回车 ==")
        deadline = time.time() + 600
        logged_in = False
        while time.time() < deadline:
            time.sleep(3)
            names = set()
            for origin in ("https://www.gold.org", "https://user.gold.org"):
                names |= {c["name"] for c in ctx.cookies(origin)}
            # user.gold.org 是 Laravel：登录态 = wgcAuth_session / wgcApiAuth_cookie
            if names & {"wgcAuth_session", "wgcAuth_cookie", "wgcApiAuth_cookie"}:
                logged_in = True
                break
        if not logged_in:
            print("⚠️ 10 分钟内未检测到登录 cookie，退出（可重跑 ./bin/wgc_login）")
            ctx.close()
            raise SystemExit(1)
        # 验证：带着会话访问数据页，应出现下载链接（登录后才渲染下载块）
        page.goto(DATA_PAGE, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        m = re.search(r'href="(/download/file/\d+/[^"]+\.xlsx)"', page.content())
        if m:
            print(f"✅ 登录态有效，数据页下载链接：{m.group(1)}")
        else:
            print("")

        ctx.close()


def _session_cookie_header() -> str | None:
    """登录会话 cookie，两个来源（优先级从上到下）：

    1. 环境变量 WGC_COOKIES（GitHub Actions secret）：格式
       `name1=value1; name2=value2`（浏览器导出的原始 Cookie 头）
    2. 本地 profile（patchright 持久 context）里的 auth cookie

    实测（2026-10）：纯 requests 带这 5 个 auth cookie 即可下载（200 xlsx），
    不需要浏览器指纹 —— Cloudflare 只拦匿名，不拦带会话的普通客户端。
    """
    env = os.environ.get("WGC_COOKIES", "").strip()
    if env:
        return env
    if not PROFILE_DIR.exists():
        return None
    try:
        from patchright.sync_api import sync_playwright

        auth_names = {
            "wgcAuth_session",
            "wgcAuth_cookie",
            "wgcApiAuth_cookie",
            "wgcAuthLang",
            "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d",
        }
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    str(PROFILE_DIR), headless=True, channel="msedge"
                )
            except Exception:
                ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True)
            cookies = ctx.cookies(["https://www.gold.org", "https://user.gold.org"])
            ctx.close()
        hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c["name"] in auth_names)
        return hdr or None
    except Exception as e:  # noqa: BLE001 —— profile 损坏时降级到无会话（会拿 DU1 报错）
        logger.warning("读本地 profile 会话失败：%s", e)
        return None


def download_xlsx(out_path: Path | None = None) -> Path:
    """下载最新 ETF flows xlsx，返回本地路径。

    匿名 GET 数据页拿文件链接（公开）；下载带会话 cookie（登录墙）。
    会话来源：WGC_COOKIES 环境变量（Actions）→ 本地 profile（手动登录）。
    """
    out = out_path or XLSX_CACHE
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1) 匿名抓数据页 → 提取最新下载链接
    r = requests.get(DATA_PAGE, timeout=60, headers={"User-Agent": UA})
    r.raise_for_status()
    links = re.findall(r'href="(/download/file/\d+/[^"]+\.xlsx)"', r.text)
    if not links:
        raise RuntimeError("数据页未找到 ETF flows 下载链接（页面结构可能改版）")
    url = "https://www.gold.org" + links[0]
    logger.info("下载 %s", url)

    # 2) 带会话 cookie 纯 HTTP 下载（实测不需要浏览器指纹）
    cookie_hdr = _session_cookie_header()
    if not cookie_hdr:
        raise SessionExpiredError(
            "无 WGC 会话：Actions 配置 WGC_COOKIES secret，或本地跑 ./bin/wgc_login"
        )
    resp = requests.get(url, timeout=180, headers={"User-Agent": UA, "Cookie": cookie_hdr})
    body = resp.content
    if resp.status_code == 403 or b"DU1" in body[:5000]:
        raise SessionExpiredError(
            "WGC 会话过期（下载返回 403/DU1）——重新导出 WGC_COOKIES secret或本地跑 ./bin/wgc_login"
        )
    if resp.status_code != 200 or not body.startswith(b"PK"):
        raise RuntimeError(f"下载异常：HTTP {resp.status_code}，{len(body)} bytes")
    out.write_bytes(body)
    logger.info("已保存 %s（%d bytes）", out, len(body))
    return out


# ── xlsx 解析 ──────────────────────────────────────────────────────────────


def parse_etf_flows(xlsx_path: Path) -> pd.DataFrame:
    """解析 WGC ETF flows xlsx → 宽表 (date × region, flow_musd)。

    用「Fund flows by month」sheet（表头明示 All units in us$mn）：
    前 5 列 = Date / Gold US$/oz / Ounces / Tonnes / Value，第 6 列起每列一只基金，
    第 4 行（idx 3）是该基金的 Region（North America / Europe / Asia / Other）。
    逐月把区域内基金求和 = 区域净流入（WGC 官方口径，可与 Key Tables 对账：
    2026-09 → NA 3961.9 / EU 3630.6 / Asia 2255.4 / Other 104.3）。

    注意不能用 Charts Data sheet：8 个图表块并排，跨块单位不一（USD/US$mn），
    且部分块只有近 16 个月——已踩过坑。
    """
    df = pd.read_excel(xlsx_path, sheet_name="Fund flows by month", header=None)
    if df.shape[0] < 7 or df.shape[1] < 6:
        raise RuntimeError("Fund flows by month sheet 结构异常（行列数不足，文件可能改版）")
    region_row = df.iloc[3]
    # 基金列 → 区域映射（第 6 列起）；前 5 列是日期/金价/全球合计，跳过
    col_region: dict[int, str] = {}
    for j in range(5, df.shape[1]):
        reg = str(region_row.iloc[j]).strip()
        if reg in REGIONS:
            col_region[j] = reg
    if not col_region:
        raise RuntimeError("Fund flows by month sheet 缺 Region 行（文件结构可能改版）")
    recs: list[dict] = []
    for _, r0 in df.iloc[6:].iterrows():
        dt = pd.to_datetime(r0.iloc[0], errors="coerce")
        if pd.isna(dt):
            continue  # 小计/说明行
        month = dt.strftime("%Y-%m")
        sums: dict[str, float] = {}
        for j, reg in col_region.items():
            v = pd.to_numeric(r0.iloc[j], errors="coerce")
            if pd.notna(v):
                sums[reg] = sums.get(reg, 0.0) + float(v)
        for reg, total in sums.items():
            recs.append({"date": month, "region": reg, "flow_musd": round(total, 1)})
    if not recs:
        raise RuntimeError("Fund flows by month sheet 无数据行")
    out = pd.DataFrame(recs).drop_duplicates(subset=["date", "region"], keep="last")
    wide = out.pivot(index="date", columns="region", values="flow_musd")
    return wide[sorted(wide.columns)]


def parse_holdings(xlsx_path: Path) -> pd.DataFrame:
    """解析 Holdings by month → 月末持仓宽表：全球合计 + GLD 列（吨）。

    前 5 列是全球合计（Date / 金价 US$/oz / Ounces / Tonnes / Value USD），
    第 6 列起每列一只基金。只取全球合计 + gld us equity —— 全部 237 只基金
    入库无展示需求，GLD 列用于与本地日频 CSV 交叉验证（实测月末偏差 <0.04%）。
    注意 Demand by month 的全球合计列有错位（值 = 上月持仓），不解析该 sheet。
    """
    df = pd.read_excel(xlsx_path, sheet_name="Holdings by month", header=None)
    if df.shape[0] < 8 or df.shape[1] < 6:
        raise RuntimeError("Holdings by month sheet 结构异常（行列数不足，文件可能改版）")
    tickers = df.iloc[0]
    gld_col = next((j for j in range(5, df.shape[1]) if "gld us" in str(tickers.iloc[j])), None)
    if gld_col is None:
        raise RuntimeError("Holdings by month 未找到 GLD 列（文件结构可能改版）")
    recs: list[dict] = []
    for _, r0 in df.iloc[6:].iterrows():
        dt = pd.to_datetime(r0.iloc[0], errors="coerce")
        if pd.isna(dt):
            continue
        row = {"date": dt.strftime("%Y-%m-%d")}
        for key, j in GLOBAL_COLS.items():
            row[key] = pd.to_numeric(r0.iloc[j], errors="coerce")
        row["gld_tonnes"] = pd.to_numeric(r0.iloc[gld_col], errors="coerce")
        recs.append(row)
    if not recs:
        raise RuntimeError("Holdings by month 无数据行")
    return pd.DataFrame(recs).set_index("date")


def parse_fund_snapshot(xlsx_path: Path) -> dict:
    """解析 All flows by fund → 单基金最新快照（月/季流入流出 + 持仓 + AUM）。

    表头 idx2，idx3 起数据行；Region 列空 = 延续上一区域，Total/GrandTotal 行跳过。
    覆盖写 JSON（快照语义，非时间序列）：data/wgc/fund_flows_latest.json。
    """
    df = pd.read_excel(xlsx_path, sheet_name="All flows by fund", header=None)
    if df.shape[0] < 5 or df.shape[1] < 11:
        raise RuntimeError("All flows by fund sheet 结构异常（行列数不足，文件可能改版）")
    as_of = next(
        (
            "-".join(reversed(str(v).split("As Of Date")[-1].strip().split("/")))
            for v in df.iloc[1]
            if v and "As Of Date" in str(v)
        ),
        None,
    )
    funds: list[dict] = []
    region = ""
    for _, r0 in df.iloc[3:].iterrows():
        name = r0.iloc[2]
        if pd.isna(name) or not str(name).strip():
            continue
        label = str(r0.iloc[1]).strip() if pd.notna(r0.iloc[1]) else ""
        if label in ("Total", "GrandTotal"):
            continue
        if label:
            region = label
        if region not in REGIONS:
            continue

        def num(j: int) -> float | None:
            v = pd.to_numeric(r0.iloc[j], errors="coerce")
            return None if pd.isna(v) else round(float(v), 2)

        funds.append(
            {
                "name": str(name).strip(),
                "ticker": str(r0.iloc[3]).strip() if pd.notna(r0.iloc[3]) else "",
                "region": region,
                "country": str(r0.iloc[4]).strip() if pd.notna(r0.iloc[4]) else "",
                "holdings_t": num(5),
                "aum_musd": num(7),
                "m_flows_musd": num(9),
                "q_flows_musd": num(11),
            }
        )
    if not funds:
        raise RuntimeError("All flows by fund 无数据行")
    return {"as_of": as_of, "funds": funds}


def run() -> pd.DataFrame:
    """下载 + 解析 + upsert，返回区域流宽表（date × region, flow_musd）。"""
    xlsx = download_xlsx()
    wide = parse_etf_flows(xlsx)
    out_csv = OUT_DIR / "etf_flows.csv"
    if out_csv.exists():
        old = pd.read_csv(out_csv, index_col="date")
        wide = wide.combine_first(old)
    wide.to_csv(out_csv, index_label="date")
    # 月末持仓（观测日 upsert：同日新值覆盖，缺失保留旧值）+ 单基金快照（覆盖写）
    hold = parse_holdings(xlsx)
    hold_csv = OUT_DIR / "wgc_holdings.csv"
    if hold_csv.exists():
        hold = hold.combine_first(pd.read_csv(hold_csv, index_col="date"))
    hold.to_csv(hold_csv, index_label="date")
    snap = parse_fund_snapshot(xlsx)
    (OUT_DIR / "fund_flows_latest.json").write_text(
        json.dumps(snap, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "etf_flows.csv：%d 月 × %d 区域；wgc_holdings.csv：%d 月；"
        "fund_flows_latest.json：%d 只基金",
        len(wide),
        wide.shape[1],
        len(hold),
        len(snap["funds"]),
    )
    return wide


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(run().tail(6).to_string())
