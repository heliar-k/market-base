"""段底注脚的折叠形态（R.secNote 的 fold）—— 口径/读法长文收成一行标签。

浏览器 API 打桩后把 rates-common.js 与断言体拼进同一临时脚本用 node 跑
（同 test_fmt_chg.py）。锁四件事：
  · 默认形态不变（div + 原文），页面里 20 多处短注脚不该被动到；
  · fold 后仍是 .re-sec-note（守卫 test_static_single_source 按这个类名判单源）；
  · summary 文案可覆盖（能力保留），但全站折叠备注统一用默认标题「口径 · 读法」；
  · 位置归属：卡内的备注留在卡里当 footer，小节级的（credit 三处、pricing 累计与路径、
    metals 交叉验证、crypto-options 的图组口径）不套卡底分隔线。
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "frontend" / "public" / "js" / "rates-common.js"
PAGES = ROOT / "frontend" / "src" / "pages"
CSS = ROOT / "frontend" / "public" / "css"

# 全站折叠备注的唯一种标题（R.secNote 的默认值；手写 <details class="re-fold"> 同文）
DEFAULT_TITLE = "口径 · 读法"

BODY = """
globalThis.window = { addEventListener() {} };
const mk = (tag) => ({
  tag, className: '', _text: '', children: [],
  set textContent(v) { this._text = v; this.children = []; },
  get textContent() { return this._text; },
  append(...c) { this.children.push(...c); },
  appendChild(c) { this.children.push(c); return c; },
});
globalThis.document = { body: { classList: { contains: () => false } }, createElement: mk };

const plain = R.secNote('短句口径');
const folded = R.secNote('长文口径', { fold: true });
const titled = R.secNote('长文读法', { fold: true, title: '读法' });
console.log(JSON.stringify({
  plainTag: plain.tag,
  plainCls: plain.className,
  plainText: plain.textContent,
  plainKids: plain.children.length,
  foldTag: folded.tag,
  foldCls: folded.className,
  foldSummary: folded.children[0].textContent,
  foldBody: folded.children[1].textContent,
  titledSummary: titled.children[0].textContent,
}));
"""


def _run() -> dict:
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过注脚折叠检查")
    src = "\n".join([COMMON.read_text(encoding="utf-8"), BODY])
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        r = subprocess.run(["node", tmp], capture_output=True, text=True, encoding="utf-8")
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[:400]
    return json.loads(r.stdout)


def test_short_note_stays_plain_div() -> None:
    out = _run()
    assert out["plainTag"] == "div"
    assert out["plainCls"] == "re-sec-note"
    assert out["plainText"] == "短句口径"
    assert out["plainKids"] == 0


def test_fold_note_is_details_under_the_same_class() -> None:
    out = _run()
    assert out["foldTag"] == "details"
    assert out["foldCls"] == "re-sec-note"
    assert out["foldSummary"] == "口径 · 读法"
    assert out["foldBody"] == "长文口径"


def test_fold_summary_title_is_overridable() -> None:
    assert _run()["titledSummary"] == "读法"


# ── 标题单源：全站折叠备注只有「口径 · 读法」一种 summary ────────────────


def _skip_string(src: str, i: int) -> int:
    """i 落在引号上，返回该字面量结束后的下标（模板里 ${} 整体跳过）。"""
    q, i = src[i], i + 1
    while i < len(src):
        if src[i] == "\\":
            i += 2
        elif src[i] == q:
            return i + 1
        else:
            i += 1
    raise AssertionError("字符串字面量未闭合")


def _secnote_args(src: str) -> list[str]:
    """每个 R.secNote(...) 的实参文本（括号配平，跳过字符串字面量）。"""
    out = []
    for m in re.finditer(r"R\.secNote\(", src):
        i = start = m.end()
        depth = 1
        while i < len(src) and depth:
            ch = src[i]
            if ch in "'\"`":
                i = _skip_string(src, i)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(src[start:i])
    return out


def _page_sources() -> list[tuple[str, str]]:
    return [
        (str(p.relative_to(PAGES)), p.read_text(encoding="utf-8"))
        for p in sorted(PAGES.rglob("*.astro"))
    ]


def test_fold_notes_pass_no_custom_summary_title() -> None:
    """折叠备注统一使用默认标题，正文各自保留。"""
    overrides, folded = [], 0
    for name, src in _page_sources():
        for args in _secnote_args(src):
            if "fold" not in args:
                continue
            folded += 1
            if "title:" in args:
                overrides.append(f"{name}: {' '.join(args.split())[:90]}")
    assert folded, "没有找到任何折叠备注，扫描口径失效"
    assert not overrides, (
        f"折叠备注的 summary 标题统一为「{DEFAULT_TITLE}」，"
        f"请在 R.secNote 里省略 title：{overrides}"
    )


def test_handwritten_fold_summaries_use_the_same_title() -> None:
    """crypto 两页手写的 <details class="re-fold"> 走同一句话。"""
    bad, hand = [], 0
    for name, src in _page_sources():
        for m in re.finditer(r'<details class="re-fold"><summary>(.*?)</summary>', src, re.S):
            hand += 1
            if m.group(1) != DEFAULT_TITLE:
                bad.append(f"{name}: <summary>{m.group(1)}</summary>")
    assert hand, "没有找到任何手写折叠块，扫描口径失效"
    assert not bad, f"手写折叠块的 summary 标题也要统一为「{DEFAULT_TITLE}」：{bad}"


# ── 位置归属：备注跟着它的卡 / 小节 ────────────────────────────────


def test_pricing_card_notes_hang_inside_their_cards() -> None:
    """pricing 三处段底注脚 append 到卡面容器；append 到 Section 会落在卡片外。"""
    src = (PAGES / "rates" / "pricing.astro").read_text(encoding="utf-8")
    for pid in ("rp-dist-panel", "rp-zq-panel", "rp-triple-panel"):
        assert f'<div class="re-panel" id="{pid}">' in src, f"{pid} 必须是卡面（.re-panel）容器"
        assert f"getElementById('{pid}').appendChild(R.secNote(" in src, (
            f"{pid} 的口径注脚没挂进卡内（注释与图/表要同在一张卡）"
        )
    # 卡片装的就是原图本身：图表 id 与 re-plain（保持原高度、不与卡面叠双框）留在静态层
    for cid in ("zq-path-chart", "triple_chart"):
        assert f'<div class="re-chart re-plain" id="{cid}"></div>' in src


def test_polymarket_note_hangs_on_the_chart_group() -> None:
    """crypto-options 的口径块说明各标的走势图，挂在图组层级。"""
    src = (PAGES / "assets" / "crypto-options.astro").read_text(encoding="utf-8")
    overview = src[
        src.index("head.innerHTML = R.secTitle('加密价位市场'") : src.index(
            "host.appendChild(head)"
        )
    ]
    assert "re-fold" not in overview, "图组口径折叠块挂在图组层级"
    assert "host.insertAdjacentHTML('beforeend', `<details class=\"re-fold\">" in src
    note_at = src.index(f"summary>{DEFAULT_TITLE}")
    assert src.index("for (const t of pm.topics)") < note_at
    assert note_at < src.index("if (pm.unmatched && pm.unmatched.count)"), (
        "口径块要排在未命中告警之前"
    )


def test_section_level_notes_drop_the_card_footer_rule() -> None:
    """不在卡面内的折叠备注去掉卡底分隔线；card=true 的 Section（.re-panel）仍保留分隔线。"""
    css = (CSS / "special.css").read_text(encoding="utf-8")
    m = re.search(r"\.re-sec:not\(\.re-panel\) > details\.re-sec-note,[^{]*\{([^}]*)\}", css)
    assert m, "缺少「小节级备注不套卡底分隔线」的规则"
    body = m.group(1)
    for decl in ("margin-top: 0", "padding-top: 0", "border-top: 0"):
        assert decl in body, f"小节级备注应去掉 {decl.split(':')[0]}：{body.strip()}"
    selector = m.group(0).split("{")[0]
    total = len(re.findall(r"\.re-sec(?![\w-])", selector))
    guarded = len(re.findall(r"\.re-sec:not\(\.re-panel\)", selector))
    assert total == guarded, (
        f"选择器必须排除 card=true 的 .re-panel（期权页卡内备注要保留分隔线）：{selector}"
    )
