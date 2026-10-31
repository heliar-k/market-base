"""段底注脚的折叠形态（R.secNote 的 fold）—— 口径/读法长文收成一行标签。

浏览器 API 打桩后把 rates-common.js 与断言体拼进同一临时脚本用 node 跑
（同 test_fmt_chg.py）。锁三件事：
  · 默认形态不变（div + 原文），页面里 20 多处短注脚不该被动到；
  · fold 后仍是 .re-sec-note（守卫 test_static_single_source 按这个类名判单源）；
  · summary 文案可覆盖（「读法」/「口径 · 读法」两档）。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "frontend" / "public" / "js" / "rates-common.js"

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
