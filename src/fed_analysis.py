"""美联储鹰鸽倾向规则引擎 — 对 FOMC 声明/官员演讲正文确定性打分。

规则引擎先行（同 rates_analysis 模式）：基于关键词短语词表对英文正文打分，
输出 -5（极鸽）~ +5（极鹰），聚合出：
  - 整体鹰鸽指示器（最近 N 条声明+演讲的均值）
  - 各官员最新立场（按演讲人聚合）
  - 鹰鸽时间线（全部评分点，供前端时间线图）

LLM 预留：`generate_fed_analysis()` 是唯一入口，内部先尝试 `_llm_generate()`
（当前返回 None = 未启用），None 则回落规则引擎。接入方式同
src/rates_analysis.py 的 `_llm_generate()`。

数据: data/fed/statements.csv + data/fed/speeches.csv（./bin/fetch_fed 拉取）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd

from src.config import FOMC_MEETINGS, ROOT

STATEMENTS_CSV = ROOT / "data" / "fed" / "statements.csv"
SPEECHES_CSV = ROOT / "data" / "fed" / "speeches.csv"

# ═══════════════════════════════════════════════════════════════════════════════
# 词表：短语 → 分数（命中累加，声明/演讲共用）
# 动作词权重最高，语气词次之；声明正文中的"决定"类动作用专门规则处理
# ═══════════════════════════════════════════════════════════════════════════════

_ACTION_WORDS: list[tuple[str, float]] = [
    # 鹰派动作
    ("raise the target range", 4.0),
    ("increase the target range", 4.0),
    ("raise rates", 3.0),
    ("raising rates", 3.0),
    ("rate hike", 3.0),
    ("further tightening", 2.5),
    ("tighten policy", 2.5),
    ("tightening", 1.0),
    ("raise the federal funds rate", 3.0),
    # 鸽派动作
    ("lower the target range", -4.0),
    ("reduce the target range", -4.0),
    ("decrease the target range", -4.0),
    ("cut rates", -3.0),
    ("cutting rates", -3.0),
    ("rate cuts", -3.0),
    ("rate cut", -3.0),
    ("lower rates", -3.0),
    ("lowering rates", -3.0),
    ("ease policy", -2.5),
    ("policy easing", -2.0),
    ("easing", -1.0),
    ("loosen", -2.0),
    ("reducing the federal funds rate", -3.0),
]

_INFLATION_WORDS: list[tuple[str, float]] = [
    # 鹰派：通胀顽固
    ("inflation remains elevated", 1.5),
    ("inflation is elevated", 1.5),
    ("inflation remains above", 1.5),
    ("inflation is running above", 1.5),
    ("elevated inflation", 1.5),
    ("sticky inflation", 1.5),
    ("inflation has been elevated", 1.0),
    ("above the committee's 2 percent goal", 1.0),
    ("above the 2 percent goal", 1.0),
    ("inflationary pressures", 1.0),
    ("upside risks to inflation", 1.5),
    ("upside risk to inflation", 1.0),
    ("risks are skewed to the upside", 1.0),
    ("will deliver price stability", 1.0),
    # 鸽派：通胀回落
    ("inflation has eased", -1.5),
    ("inflation eased", -1.5),
    ("inflation is moving toward", -1.0),
    ("moving down toward 2 percent", -1.0),
    ("returning to the committee's 2 percent goal", -1.0),
    ("returning to 2 percent", -1.0),
    ("inflation is running below", -1.5),
    ("below the 2 percent goal", -1.0),
    ("downside risks to inflation", -1.0),
    ("disinflation", -1.0),
    ("inflation has moderated", -1.5),
    ("inflation continues to moderate", -1.0),
]

_LABOR_WORDS: list[tuple[str, float]] = [
    # 鹰派：就业强
    ("labor market remains strong", 0.5),
    ("labor market is strong", 0.5),
    ("strong labor market", 0.5),
    ("job gains have remained strong", 0.5),
    ("tight labor market", 0.5),
    # 鸽派：就业弱
    ("labor market has cooled", -1.0),
    ("labor market is cooling", -1.0),
    ("cooling labor market", -1.0),
    ("job gains have slowed", -1.0),
    ("job gains slowed", -1.0),
    ("weakening labor market", -1.0),
    ("downside risks to employment", -1.0),
    ("employment is below", -1.0),
    ("labor market has weakened", -1.5),
    ("labor market weakness", -1.5),
    ("unemployment has risen", -1.0),
    ("unemployment rate has risen", -1.0),
]

_TONE_WORDS: list[tuple[str, float]] = [
    # 鹰派语气
    ("strongly committed", 1.0),
    ("vigilant", 1.0),
    ("will not hesitate", 0.5),
    ("prepared to tighten", 1.0),
    ("resolute", 0.5),
    ("forcefully", 0.5),
    # 鸽派语气
    ("supporting maximum employment", -0.5),
    ("support maximum employment", -0.5),
    ("will be prepared to adjust", -0.5),
    ("data dependent", 0.0),
    ("remain patient", 0.0),
    ("patient", -0.5),
]

# 反对票段（"voting against ... preferred to raise/lower"）：按段落先剥离再扫描，
# 否则 "preferred to raise the target range" 会被通用动作词表重复计分（±4 + ±1）。
# 正文已按 <p> 分行（_extract_body），反对票为独立段，逐行判断即可。
_DISSENT_WORDS = ("raise", "lower", "increase", "decrease", "reduce")
_DISSENT_RE = re.compile(r"preferred to (raise|lower|increase|decrease|reduce)")
_DISSENT_SCORE = {
    "raise": 1.0,
    "increase": 1.0,
    "lower": -1.0,
    "decrease": -1.0,
    "reduce": -1.0,
}

# 动作词短语集合（供权重区分：声明决议可靠，演讲提及需降权）
_ACTION_PHRASES = {w for w, _ in _ACTION_WORDS}


# 2026 年 FOMC 投票成员（7 理事 + 5 地区联储主席，federalreserve.gov 官网）
# speaker_key: 演讲 URL 姓氏，用于关联 stances；地区联储演讲不在 Board 站点，无评分
# 票委名册每年 1 月更新：更新 FED_OFFICIALS 时同步改 VOTING_YEAR，前端标题随之刷新
VOTING_YEAR = 2026

FED_OFFICIALS: list[dict] = [
    {"speaker_key": "Powell", "name_zh": "鲍威尔", "role": "美联储主席", "votes": True},
    {
        "speaker_key": "Jefferson",
        "name_zh": "杰斐逊",
        "role": "美联储副主席",
        "votes": True,
    },
    {
        "speaker_key": "Barr",
        "name_zh": "巴尔",
        "role": "负责监管的副主席",
        "votes": True,
    },
    {"speaker_key": "Bowman", "name_zh": "鲍曼", "role": "理事", "votes": True},
    {"speaker_key": "Cook", "name_zh": "库克", "role": "理事", "votes": True},
    {"speaker_key": "Kugler", "name_zh": "库格勒", "role": "理事", "votes": True},
    {"speaker_key": "Waller", "name_zh": "沃勒", "role": "理事", "votes": True},
    {
        "speaker_key": "Williams",
        "name_zh": "威廉姆斯",
        "role": "纽约联储主席",
        "votes": True,
    },
    {
        "speaker_key": "Collins",
        "name_zh": "柯林斯",
        "role": "波士顿联储主席",
        "votes": True,
    },
    {
        "speaker_key": "Musalem",
        "name_zh": "穆萨莱姆",
        "role": "圣路易斯联储主席",
        "votes": True,
    },
    {
        "speaker_key": "Schmid",
        "name_zh": "施密德",
        "role": "堪萨斯城联储主席",
        "votes": True,
    },
    {
        "speaker_key": "Goolsbee",
        "name_zh": "古尔斯比",
        "role": "芝加哥联储主席",
        "votes": True,
    },
]


def _score_text(text: str, action_weight: float = 1.0) -> tuple[float, list[str]]:
    """正文 → (分数, 命中短语)。分数 clamp 到 [-5, 5]。

    action_weight: 动作词（加息/降息）权重系数。声明=1.0（决议可靠），
    演讲=0.5（提及/讨论 ≠ 倾向，避免 'market expects rate cuts' 类误伤）。

    短语命中采用最长匹配 + 重叠去重（审计 P1-⑫）：'rate cuts' 命中后，
    其子串 'rate cut' 不再单独计分；同一短语多处出现只计一次分（审计 C-1，
    避免 hits 列表与前端渲染出现 'rate cuts · rate cuts' 重复）。
    """
    low = text.lower()
    score = 0.0
    hits: list[str] = []
    # 反对票段（独立段落）剥离开单列（±1 内部压力），避免其动作词被通用扫描重复计分
    scan_lines = []
    for line in low.split("\n"):
        if "voting against" in line:
            m = _DISSENT_RE.search(line)
            if m:
                score += _DISSENT_SCORE[m.group(1)]
                hits.append(f"dissent: preferred to {m.group(1)}")
                continue
        scan_lines.append(line)
    low = "\n".join(scan_lines)

    # 收集全部候选短语的匹配区间（同一短语多处出现各自成区间）
    spans: list[tuple[int, int, str, float]] = []
    for words, s in [*_ACTION_WORDS, *_INFLATION_WORDS, *_LABOR_WORDS, *_TONE_WORDS]:
        start = 0
        while True:
            i = low.find(words, start)
            if i < 0:
                break
            spans.append((i, i + len(words), words, s))
            start = i + 1
    # 最长优先；重叠（含子串/交叉）只保留首个；同一短语多处出现只计一次分
    spans.sort(key=lambda p: (-(p[1] - p[0]), p[0]))
    covered: list[tuple[int, int]] = []
    used: set[str] = set()
    for start, end, words, s in spans:
        if any(start < ce and end > cs for cs, ce in covered):
            continue  # 与已命中短语重叠（子串/交叉）→ 跳过
        covered.append((start, end))  # 即使已计过分的短语，区间也作为覆盖物
        if words in used:
            continue  # 同短语至多计一次分（旧实现语义）
        used.add(words)
        score += s * (action_weight if words in _ACTION_PHRASES else 1.0)
        hits.append(words)
    return round(max(-5.0, min(5.0, score)), 1), hits


def stance_label(score: float) -> str:
    """分数 → 中文档位（七档：极鹰/鹰派/偏鹰/中性/偏鸽/鸽派/极鸽）。"""
    if score >= 4:
        return "极鹰"
    if score >= 2:
        return "鹰派"
    if score > 0.5:
        return "偏鹰"
    if score < -4:
        return "极鸽"
    if score <= -2:
        return "鸽派"
    if score < -0.5:
        return "偏鸽"
    return "中性"


def _pre_meeting_indicator(
    sp: pd.DataFrame, days: int = 14, today: pd.Timestamp | None = None
) -> dict | None:
    """上一次 FOMC 会议前 days 天窗口内演讲的平均鹰鸽分（无演讲则 None）。

    ponytail: 窗口固定会前 14 天（黑窗期前演讲最密），需要多窗口再参数化。
    """
    if sp.empty or "date" not in sp or "score" not in sp:
        return None
    ref = today or pd.Timestamp.today().normalize()
    past = [m for m in FOMC_MEETINGS if pd.Timestamp(m.year, m.month, m.end_day) <= ref]
    if not past:
        return None
    m = max(past, key=lambda x: (x.year, x.month, x.end_day))
    end = pd.Timestamp(m.year, m.month, m.end_day)
    start = end - pd.Timedelta(days=days)
    win = sp[
        (sp["date"] >= start.strftime("%Y%m%d"))
        & (sp["date"] <= end.strftime("%Y%m%d"))
    ]
    if win.empty:
        return None
    avg = round(win["score"].astype(float).mean(), 1)
    return {
        "score": avg,
        "label": stance_label(avg),
        "sample": int(len(win)),
        "meeting": end.strftime("%Y-%m-%d"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 数据装载与聚合
# ═══════════════════════════════════════════════════════════════════════════════


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str).fillna("")


def _docs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """声明 + 演讲（正文补 score 列；声明动作词权重 1.0，演讲 0.5）。"""
    st, sp = _read(STATEMENTS_CSV), _read(SPEECHES_CSV)
    for df, aw in [(st, 1.0), (sp, 0.5)]:
        if not df.empty:
            scored = df["body"].map(lambda t: _score_text(t, aw))
            df["score"] = scored.map(lambda x: x[0])
            df["hits"] = scored.map(lambda x: x[1])
    return st, sp


def _hits_list(v) -> list[str]:
    """CSV 读回的 hits 字符串 → list。"""
    if isinstance(v, str):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return []
    return list(v or [])


def _doc_rows(df: pd.DataFrame, extra: dict) -> list[dict]:
    """DataFrame 行 → API 文档条目（评分/标签/摘要），extra 为每行附加字段。"""
    rows = []
    for _, r in df.iterrows():
        score = float(r["score"]) if r["score"] else 0.0
        rows.append(
            {
                "id": r["id"],
                "date": r["date"],
                "title": r["title"],
                "url": r["url"],
                "score": score,
                "label": stance_label(score),
                "hits": _hits_list(r["hits"]),
                "excerpt": (r["body"][:400] + "…")
                if len(r["body"]) > 400
                else r["body"],
                **{k: r[v] for k, v in extra.items()},
            }
        )
    return rows


def generate_fed_analysis() -> dict:
    """统一入口：LLM 优先（预留），规则引擎兜底。"""
    if _LLM_ENABLED:
        llm_out = _llm_generate()
        if llm_out is not None:
            return {**llm_out, "generator": "llm"}
    return {**fed_analysis(), "generator": "rules"}


def fed_analysis(n_sample: int = 20) -> dict:
    """规则引擎核心：整体指示器 + 声明/演讲列表 + 官员立场 + 时间线。"""
    st, sp = _docs()
    if st.empty and sp.empty:
        return {"error": "data/fed/ 缺失，先运行 ./bin/fetch_fed"}

    # ── 声明/演讲列表（含评分）──
    statements = sorted(
        _doc_rows(st, {"kind": "kind"}), key=lambda x: x["date"], reverse=True
    )
    speeches = sorted(
        _doc_rows(sp, {"speaker": "speaker"}), key=lambda x: x["date"], reverse=True
    )

    # ── 整体指示器：最近 n 条（声明 statement 类优先 + 演讲）──
    pool = [s for s in statements if s["kind"] == "statement"][: n_sample // 2]
    pool += speeches[: n_sample - len(pool)]
    scores = [p["score"] for p in pool]
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0

    # ── 官员立场：每人最新一条演讲 ──
    stances = []
    for speaker in sorted({s["speaker"] for s in speeches if s["speaker"]}):
        latest = next(s for s in speeches if s["speaker"] == speaker)
        stances.append(
            {
                "speaker": speaker,
                "score": latest["score"],
                "label": latest["label"],
                "date": latest["date"],
                "title": latest["title"],
                "url": latest["url"],
            }
        )
    stances.sort(key=lambda x: x["score"], reverse=True)

    # ── FOMC 投票成员一览：官方名单 × 演讲评分（无演讲的成员显示中性/暂无）──
    by_speaker = {s["speaker"]: s for s in stances}
    roster = []
    for o in FED_OFFICIALS:
        stance = by_speaker.get(o["speaker_key"])
        roster.append(
            {
                "speaker": o["speaker_key"],
                "name_zh": o["name_zh"],
                "role": o["role"],
                "votes": o["votes"],
                "score": stance["score"] if stance else 0.0,
                "label": stance["label"] if stance else "中性",
                "date": stance["date"] if stance else None,
                "title": stance["title"] if stance else None,
                "url": stance["url"] if stance else None,
            }
        )

    # ── 时间线：全部评分点（声明 statement + 演讲），升序供折线图 ──
    timeline = [
        {
            "date": s["date"],
            "score": s["score"],
            "name": "FOMC 声明",
            "type": "statement",
        }
        for s in statements
        if s["kind"] == "statement"
    ] + [
        {"date": s["date"], "score": s["score"], "name": s["speaker"], "type": "speech"}
        for s in speeches
    ]
    timeline.sort(key=lambda x: x["date"])

    return {
        "indicator": {
            "score": avg,
            "label": stance_label(avg),
            "sample": len(pool),
            "pre_meeting": _pre_meeting_indicator(sp),
        },
        "statements": statements,
        "speeches": speeches,
        "stances": stances,
        "roster": roster,
        "timeline": timeline,
        "voting_year": VOTING_YEAR,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LLM 预留
# ═══════════════════════════════════════════════════════════════════════════════


def _llm_generate() -> dict | None:
    """LLM 生成入口（预留）：返回与 `fed_analysis()` 同构 dict。未接入返回 None。"""
    return None


_LLM_ENABLED = False


if __name__ == "__main__":
    # 自检：跑通 + 打印关键结果
    import json

    out = fed_analysis()
    print(
        json.dumps(
            {k: out[k] for k in ("indicator", "stances")},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        "statements:",
        len(out.get("statements", [])),
        "| speeches:",
        len(out.get("speeches", [])),
    )
