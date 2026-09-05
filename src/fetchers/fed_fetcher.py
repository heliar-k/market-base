"""
Fetch FOMC statements & Fed official speeches from federalreserve.gov.
数据源: federalreserve.gov 年度列表页 + 正文页（免费，无需认证）
  - FOMC 声明: /newsevents/pressreleases/{YYYY}-press-fomc.htm → monetary*.htm
  - FOMC 纪要: /monetarypolicy/fomccalendars.htm → fomcminutes*.htm
  - 官员演讲: /newsevents/{YYYY}-speeches.htm → speech/*.htm

输出:
  data/fed/statements.csv  — id, date, kind, title, url, body
  data/fed/speeches.csv    — id, date, speaker, title, url, body

存储模式: 文档库（非观测日序列），按 URL 去重增量——不走 AGENTS.md 决策 #2 的
upsert_timeseries / save_daily_csv 两种时间序列模式，与 treasury_fetcher 同类。

增量: 本地已有 URL 自动跳过（自然增量，无全量覆盖逻辑）。
默认范围: 声明 2020 起（覆盖疫情降息/22-23 加息/24-26 完整周期），
纪要 2021 起（HTML 版起始年），演讲近 2 个自然年。

用法:
  uv run python -m src.fetchers.fed_fetcher              # 默认增量
  uv run python -m src.fetchers.fed_fetcher --years 2026 # 指定年份范围
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date
from html import unescape
from pathlib import Path

import pandas as pd
import requests

from ..config import ROOT

logger = logging.getLogger(__name__)

BASE = "https://www.federalreserve.gov"
HEADERS = {"User-Agent": "Mozilla/5.0 (research script; +https://github.com/)"}
DELAY = 0.3  # 请求间隔，避免触发限流

# 直连会话：忽略 .env 中的 SOCKS5 代理（yfinance 用），federalreserve.gov 无需代理
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update(HEADERS)

OUT_DIR = ROOT / "data" / "fed"
STATEMENTS_CSV = OUT_DIR / "statements.csv"
SPEECHES_CSV = OUT_DIR / "speeches.csv"

# 正文容器：#article（声明/SEP/纪要页通用模板，含正文到页脚前）
_BODY_RE = re.compile(r'<div id="article"[^>]*>(.*?)<div class="row footer', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _get(url: str) -> str:
    r = _SESSION.get(url, timeout=20)
    r.raise_for_status()
    r.encoding = "utf-8"  # 站点未声明 charset，requests 默认 latin-1 会把 — 解成乱码
    time.sleep(DELAY)
    return r.text


def _extract_body(html: str) -> str:
    """提取正文纯文本（声明/演讲页共用容器）。"""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html
    # 视频无障碍说明块（sr-only，含 Accessible Keys for Video）不是正文
    body = re.sub(r'<div class="sr-only">.*?</div>', " ", body, flags=re.S)
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    # 标签→空格（内联标签不拆词），再折叠多余空白、保留段落
    text = unescape(_TAG_RE.sub(" ", body))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _statement_kind(title: str) -> str:
    """按标题分类：statement / minutes / sep / discount / other。"""
    t = title.lower()
    if "issues fomc statement" in t:
        return "statement"
    if "minutes of the federal open market committee" in t:
        return "minutes"
    if "economic projections" in t:
        return "sep"
    if "discount rate" in t:
        return "discount"
    return "other"


def _speaker_of(doc_id: str) -> str:
    """演讲文档 id（URL stem）以官员姓氏开头：jefferson20260716a → Jefferson。"""
    m = re.match(r"([a-z]+)\d{8}", doc_id)
    return m.group(1).capitalize() if m else ""


def _clean_title(title: str) -> str:
    """去掉站点前后缀：'Federal Reserve Board - X - Federal Reserve Board' → X。"""
    for prefix in ("Federal Reserve Board - ", "Speech by ", "Remarks by "):
        if title.startswith(prefix):
            title = title[len(prefix) :]
    for suffix in (" - Federal Reserve Board",):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    return title


def _fetch_items(
    urls: dict[str, str], out_csv: Path, cols: list[str], fixed: dict | None = None
) -> tuple[int, int]:
    """抓取 {id: url} 中的新条目，upsert 到 out_csv。返回 (新增, 跳过)。

    fixed: 每行固定值（如 {"kind": "minutes"}，用于页面 <title> 不含分类信息的文档）。
    """
    old = pd.DataFrame()
    if out_csv.exists():
        old = pd.read_csv(out_csv, dtype=str)
    seen = set(old["url"]) if not old.empty else set()

    rows, skipped = [], 0
    for rid, url in urls.items():
        if url in seen:
            skipped += 1
            continue
        try:
            html = _get(url)
        except Exception as e:  # noqa: BLE001 — 单条失败不阻断整体
            logger.warning("抓取失败 %s: %s", url, e)
            continue
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        title = unescape(title.group(1).strip()) if title else ""
        # 优先 #article 内的 h1/h2/h3（页面 <title> 常是通用站点名，如纪要页）
        h = re.search(
            r'<div id="article"[^>]*>.*?<(?:h1|h2|h3)[^>]*>(.*?)</(?:h1|h2|h3)>',
            html,
            re.S,
        )
        if h:
            title = re.sub(r"<[^>]+>", " ", h.group(1)).strip()
        title = _clean_title(title)
        body = _extract_body(html)
        if not body:
            logger.warning("正文为空 %s，跳过", url)
            continue
        rows.append(dict(zip(cols, [rid, "", title, url, body], strict=True)))
        if fixed:
            rows[-1].update(fixed)
    # 不传 columns：保留 rows 中 fixed 附加的键（如 kind）；空 rows 时直接沿用 old
    new_rows = pd.DataFrame(rows) if rows else old
    merged = pd.concat([old, new_rows], ignore_index=True)
    merged = merged.drop_duplicates(subset=["url"]).sort_values("date")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_csv, index=False)
    return len(rows), skipped


def _backfill(out_csv: Path, extra: dict[str, tuple[str, object]]) -> None:
    """重读 CSV 并回填派生列（幂等，每次全量重算）。

    extra: 列名 → (来源列, 计算函数)。派生列是现有列的纯函数
    （speaker ← id，kind ← title），无条件重算：否则 _fetch_items 追加的新行
    拿不到派生值（历史上因此丢过新演讲的 speaker）。
    """
    df = pd.read_csv(out_csv, dtype=str)
    df["date"] = df["id"].str.extract(r"(\d{8})")
    for col, (src, fn) in extra.items():
        df[col] = df[src].map(fn)
    df["title"] = df["title"].map(_clean_title)
    df.to_csv(out_csv, index=False)


# ── FOMC 声明 ────────────────────────────────────────────────────────────────


def fetch_statements(years: list[int]) -> tuple[int, int]:
    """拉取指定年份的 FOMC 货币政策类发布（声明/SEP/贴现率纪要等，不含纪要）。"""
    urls: dict[str, str] = {}
    for y in years:
        try:
            page = _get(f"{BASE}/newsevents/pressreleases/{y}-press-fomc.htm")
        except requests.HTTPError:
            logger.warning("%s-press-fomc.htm 不存在，跳过", y)
            continue
        for link in set(
            re.findall(r'href="([^"]*pressreleases/monetary\d{8}[a-z]\.htm)"', page)
        ):
            rid = Path(link).stem
            urls[rid] = BASE + link
    new, skipped = _fetch_items(
        urls, STATEMENTS_CSV, ["id", "date", "title", "url", "body"]
    )
    _backfill(STATEMENTS_CSV, {"kind": ("title", _statement_kind)})
    return new, skipped


# ── FOMC 纪要（独立发布，fomccalendars.htm 全量 HTML）────────────────────────


def fetch_minutes() -> tuple[int, int]:
    """拉取 FOMC 会议纪要（/monetarypolicy/fomccalendars.htm，2021 起 HTML 版）。"""
    page = _get(f"{BASE}/monetarypolicy/fomccalendars.htm")
    urls = {
        Path(link).stem: BASE + link
        for link in re.findall(r'href="([^"]*fomcminutes\d{8}\.htm)"', page)
    }
    new, skipped = _fetch_items(
        urls,
        STATEMENTS_CSV,
        ["id", "date", "title", "url", "body"],
        {"kind": "minutes"},
    )
    _backfill(STATEMENTS_CSV, {"kind": ("title", _statement_kind)})
    return new, skipped


# ── 官员演讲 ─────────────────────────────────────────────────────────────────


def fetch_speeches(years: list[int]) -> tuple[int, int]:
    """拉取指定年份的官员演讲。"""
    urls: dict[str, str] = {}
    for y in years:
        page = _get(f"{BASE}/newsevents/{y}-speeches.htm")
        for link in set(
            re.findall(r'href="([^"]*speech/[a-z0-9]+\d{8}[a-z]\.htm)"', page)
        ):
            rid = Path(link).stem
            urls[rid] = BASE + link
    new, skipped = _fetch_items(
        urls, SPEECHES_CSV, ["id", "date", "title", "url", "body"]
    )
    _backfill(SPEECHES_CSV, {"speaker": ("id", _speaker_of)})
    return new, skipped


def fetch_fed(speech_years: int = 2, statement_since: int = 2020) -> dict:
    """统一入口。返回 {statements, minutes, speeches: (new, skipped)}。"""
    this_year = date.today().year
    out = {
        "statements": fetch_statements(list(range(statement_since, this_year + 1))),
        "minutes": fetch_minutes(),
    }
    out["speeches"] = fetch_speeches(
        list(range(this_year - speech_years + 1, this_year + 1))
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch FOMC statements & Fed speeches")
    parser.add_argument("--years", type=int, nargs="*", help="指定年份（声明+演讲）")
    parser.add_argument("--speech-years", type=int, default=2, help="演讲回溯年数")
    parser.add_argument("--statement-since", type=int, default=2020, help="声明起始年")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.years:
        years = sorted(set(args.years))
        result = {
            "statements": fetch_statements(years),
            "speeches": fetch_speeches(years),
        }
    else:
        result = fetch_fed(args.speech_years, args.statement_since)
    for key, (new, skipped) in result.items():
        logger.info("%s: 新增 %d，跳过 %d（已有）", key, new, skipped)


if __name__ == "__main__":
    main()
