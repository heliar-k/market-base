"""全站研判「行契约」守卫（rates/index 与三段式页共用 R.sigBlocks 渲染）。

契约：引擎叙事用 \n 分「结论 / 依据 / 触发」，最多 3 行；行内不再自带
「验证指标：/ 触发条件：」这类前缀——标签由前端 LINE_TITLES / sig-title 给，
文案里再写一遍就是重复（且切了标签的单源）。
"""

import src.assets_analysis as assets
import src.credit_analysis as credit
import src.inflation_analysis as inflation
import src.labor_analysis as labor
import src.liquidity_analysis as liquidity
import src.rates_analysis as rates
import src.treasury_analysis as treasury
import src.volatility_dashboard as vdash

# 前缀已交给渲染层标签，文案里出现即为回归
BANNED = ("验证指标：", "验证指标——", "触发条件：", "关键触发点：")


def _bodies() -> list[tuple[str, str]]:
    """(来源, 叙事文本) 全清单。"""
    out: list[tuple[str, str]] = []
    ov = rates.overview_analysis()
    out += [(f"rates/{s['title']}", s["body"]) for s in ov.get("sections", [])]
    ev = liquidity.liquidity_snapshot().get("evaluation") or {}
    out += [(f"liquidity/{k}", v["text"]) for k, v in ev.items() if isinstance(v, dict)]
    cr = credit.generate_credit_overview().get("signals") or {}
    out += [(f"credit/{k}", v) for k, v in cr.items() if isinstance(v, str)]
    for mod, fn, pick in (
        ("inflation", inflation.generate_inflation_overview, lambda d: d["signals"]),
        ("labor", labor.generate_labor_overview, lambda d: d["signals"]),
        ("treasury", treasury.generate_treasury_overview, lambda d: d["signals"]),
    ):
        for s in pick(fn()) or []:
            out.append((f"{mod}/{s['title']}", s["text"]))
    an = assets.overview().get("analysis") or {}
    out += [(f"assets/{k}", v["text"]) for k, v in an.items() if isinstance(v, dict)]
    # 波动率页七段叙事（第 7 段 parts 另走 .vol-basic-grid，这里取每段 text）
    out += [
        (f"volatility/{s['title']}", s["text"])
        for s in vdash.generate_dashboard().get("narrative") or []
    ]
    return out


def test_no_duplicate_trigger_prefixes():
    """条件句前缀只由渲染层标签表达，文案不重复写。"""
    bad = [f"{src}: {p}" for src, body in _bodies() for p in BANNED if p in body]
    assert not bad, "研判文案仍自带条件前缀：" + "; ".join(bad)


def test_line_count_within_contract():
    """分行数 ≤3（结论 / 依据 / 触发），超行说明引擎把两段拼成了一行或反之。"""
    bad = [
        f"{src}={len(body.splitlines())} 行" for src, body in _bodies() if len(body.split("\n")) > 3
    ]
    assert not bad, "研判叙事超过 3 行：" + "; ".join(bad)


def test_every_body_non_empty():
    assert _bodies(), "引擎未产出任何研判叙事（接口改名？同步本守卫）"
    assert all(body.strip() for _, body in _bodies()), "存在空叙事文本（前端会渲染空 sig-block）"
