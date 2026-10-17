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
# 票委名册（fomc.htm 的「{YYYY} Committee Members」段）：每年 1 月换人，此前写死在
# fed_analysis.FED_OFFICIALS 里导致过与官网完全对不上（鲍威尔仍标主席、已离任的理事仍在列）。
# 覆盖写：官网就是当前事实，不做增量。
ROSTER_CSV = OUT_DIR / "roster.csv"

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
        for link in set(re.findall(r'href="([^"]*pressreleases/monetary\d{8}[a-z]\.htm)"', page)):
            rid = Path(link).stem
            urls[rid] = BASE + link
    new, skipped = _fetch_items(urls, STATEMENTS_CSV, ["id", "date", "title", "url", "body"])
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
        for link in set(re.findall(r'href="([^"]*speech/[a-z0-9]+\d{8}[a-z]\.htm)"', page)):
            rid = Path(link).stem
            urls[rid] = BASE + link
    new, skipped = _fetch_items(urls, SPEECHES_CSV, ["id", "date", "title", "url", "body"])
    _backfill(SPEECHES_CSV, {"speaker": ("id", _speaker_of)})
    return new, skipped


# ── 票委名册 ──────────────────────────────────────────────────────────────────


# fomc.htm 的「{YYYY} Committee Members」段：一行一人「姓, 职务[, Chairman|Vice Chair]」。
# 官网直接给当前事实，比手写轮值规则可靠：名册换人/主席改选官网先改，本地不用跟。
_ROSTER_HEAD_RE = re.compile(r"(\d{4})\s*Committee\s+Members")
_ROSTER_ROW_RE = re.compile(
    r"^\s*([A-Z][\w.'\- ]+?)\s*,\s*(Board of Governors|New York|Cleveland|Boston|St\.? Louis|"
    r"Kansas City|Chicago|Philadelphia|Dallas|Minneapolis|Richmond|Atlanta|San Francisco)"
    r"(?:\s*,\s*(Chairman|Vice Chair))?\s*$",
    re.M,
)

# 演讲 URL 的姓氏（_speaker_of 从 id 解析）→ 中文名。官网只给英文名，中文靠这张表。
# 未登记的新人回落英文姓氏（表格照常可读，只是没中文）。
# 地区联储城市 → 中文（role 拼接用）
_CITY_ZH = {
    "New York": "纽约",
    "Cleveland": "克利夫兰",
    "Boston": "波士顿",
    "St. Louis": "圣路易斯",
    "St Louis": "圣路易斯",
    "Kansas City": "堪萨斯城",
    "Chicago": "芝加哥",
    "Philadelphia": "费城",
    "Dallas": "达拉斯",
    "Minneapolis": "明尼阿波利斯",
    "Richmond": "里士满",
    "Atlanta": "亚特兰大",
    "San Francisco": "旧金山",
}

_NAME_ZH = {
    "Warsh": "沃什",
    "Powell": "鲍威尔",
    "Williams": "威廉姆斯",
    "Barr": "巴尔",
    "Bowman": "鲍曼",
    "Cook": "库克",
    "Hammack": "哈马克",
    "Jefferson": "杰斐逊",
    "Kashkari": "卡什卡里",
    "Logan": "洛根",
    "Paulson": "保尔森",
    "Waller": "沃勒",
    "Kugler": "库格勒",
    "Musalem": "穆萨莱姆",
    "Schmid": "施密德",
    "Collins": "柯林斯",
    "Goolsbee": "古尔斯比",
    "Barkin": "巴尔金",
    "Daly": "戴利",
}


def fetch_roster() -> tuple[int, int]:
    """拉取官网当年 FOMC 票委名册（12 人），覆盖写 data/fed/roster.csv。

    此前名册写死在 fed_analysis.FED_OFFICIALS，已与官网完全对不上
    （鲍威尔仍标主席、已离任理事仍在列、地区主席轮值未跟）。
    返回 (写入行数, 0)。
    """
    page = _get(f"{BASE}/monetarypolicy/fomc.htm")
    text = unescape(_TAG_RE.sub("", page.replace("<br />", "\n").replace("<br>", "\n")))
    head = _ROSTER_HEAD_RE.search(text)
    if not head:
        raise RuntimeError("fomc.htm 未找到「YYYY Committee Members」段（页面结构变了？）")
    year = int(head.group(1))
    seg = text[head.end() :]
    alt = seg.find("Alternate Members")  # 段末是候补成员
    if alt > 0:
        seg = seg[:alt]

    def _parse(block: str, votes: bool) -> list[dict]:
        out = []
        for full, place, title in _ROSTER_ROW_RE.findall(block):
            # 官网行是「First [Middle] Last, 职务」——姓氏 = 最后一个词，演讲 URL 用姓氏
            surname = full.strip().split()[-1]
            role = (
                "理事" if place == "Board of Governors" else f"{_CITY_ZH.get(place, place)}联储主席"
            )
            if title == "Chairman":
                role = "美联储主席"
            elif title == "Vice Chair":
                role = "美联储副主席" if place == "New York" else "副主席"
            out.append(
                {
                    "year": year,
                    "speaker": surname,
                    "name_zh": _NAME_ZH.get(surname, surname),
                    "role": role,
                    "votes": votes,
                }
            )
        return out

    rows = _parse(seg, votes=True)
    # 候补成员（Alternate Members）：今年不投票，但会在别人缺席时代投 —— 一起抓进来，
    # votes=False。这样「投票权」列有真实区分，不再恒 True 恒被前端隐藏；
    # 也让页面能看出谁在会场里但没票（如 2026 的 Goolsbee）。
    # 「First Vice President, New York」这类临时代表不在 _ROSTER_ROW_RE 的城市表里，自然跳过。
    alt_start = text.find("Alternate Members", head.end())
    if alt_start > 0:
        # 段末锚点「Federal Reserve Bank Rotation」是轮值表标题；锚点没了就退到
        # 「票委段结尾的合理边界」—— 固定 800 字符窗会截断候补名单（实测 2 人只抓到 1 人），
        # 所以退到下一个「Committee Members」/ 文件末尾，而不是硬编码长度。
        # 实测安全：轮值表每行是「New York」独立一行，没有「, 城市」结构，
        # _ROSTER_ROW_RE 匹配不到，窗口多带进几行也不会误抓（另有重复姓氏校验兜底）。
        alt_end = text.find("Federal Reserve Bank Rotation", alt_start)
        anchor_missing = alt_end < 0  # 先记下来：下面会把 alt_end 改写成有效边界
        if anchor_missing:
            nxt = text.find("Committee Members", alt_start)
            alt_end = nxt if nxt > 0 else len(text)
        alt_seg = text[alt_start:alt_end]
        rows += _parse(alt_seg, votes=False)
        # 注意用 anchor_missing 而不是 `not alt_end`：str.find 找不到返回 -1（truthy），
        # `if not alt_end` 永远为 False，这条 warning 不会响（这个 bug 真实发生过）。
        if anchor_missing:
            logger.warning("fomc.htm 缺「Federal Reserve Bank Rotation」锚点，候补段退到段边界解析")
    # 抓不全就硬失败，绝不把坏名册写盘：
    # 官网结构变 / 页面半截返回时，写进去会让 fed 页把 5 个真人静默标成「中性/—」，
    # 或干脆只剩几个人（2026-10 硬编码错 6/12 就是这样长期没被发现）。
    # 抛错 → bin/fetch_fed 退出码非 0 → Actions 的 FAILED_LIST 标红当天 job。
    # 宁可名册停在上一次的好数据，也不让半截名册覆盖它。
    voters = [r for r in rows if r["votes"]]
    # 12 是满编（7 理事 + 纽约 + 4 轮值），但理事席位常有空缺 —— 空缺期官网就只列
    # 11 人，硬要求 12 会让 fetch_fed 每天炸、名册永远停在旧数据（比缺一个人更糟）。
    # 所以按「合理区间」判：<10 说明解析退化了（正常最少 11 = 6 理事 + 纽约 + 4 轮值，
    # 留 1 个余量给极端空缺），>12 说明把轮值表/候补段误抓进了票委段。
    if not 10 <= len(voters) <= 12:
        raise RuntimeError(
            f"票委解析出 {len(voters)} 人（合理区间 10-12）——fomc.htm 结构可能变了，"
            f"已中止写盘（保留上一份 roster.csv）。检查 _ROSTER_ROW_RE / _ROSTER_HEAD_RE"
        )
    surnames = [r["speaker"] for r in rows]
    if len(set(surnames)) != len(surnames):
        raise RuntimeError(f"名册出现重复姓氏 {surnames}——解析可能错位，已中止写盘")
    if not any(r["role"] == "美联储主席" for r in voters):
        raise RuntimeError("票委名册里没有「美联储主席」——Chairman 标记未匹配，已中止写盘")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["year", "speaker", "name_zh", "role", "votes"]).to_csv(
        ROSTER_CSV, index=False
    )
    return len(rows), 0


def fetch_fed(speech_years: int = 2, statement_since: int = 2020) -> dict:
    """统一入口。返回 {statements, minutes, speeches, roster: (new, skipped)}。"""
    this_year = date.today().year
    out = {
        "statements": fetch_statements(list(range(statement_since, this_year + 1))),
        "minutes": fetch_minutes(),
        "roster": fetch_roster(),
    }
    out["speeches"] = fetch_speeches(list(range(this_year - speech_years + 1, this_year + 1)))
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
