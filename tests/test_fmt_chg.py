"""跨资产表数值格式化单源（R.fmtLast / R.fmtChg）——daily 页与仪表盘共用。

浏览器 API 打桩后把 rates-common.js 与断言体拼进同一临时脚本用 node 跑
（同 test_legend_sync.py 的做法）。锁两类曾经真出过的错：
  · 符号位置：bn 单位曾渲染成 `$-2B`（读起来像「负美元」）
  · 零值着色：RRP Δ=-0.4M 四舍五入成 $0M 却仍标红
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
globalThis.document = { body: { classList: { contains: () => false } } };
const out = {
  bnNeg: R.fmtChg('bn', -2000),
  bnPos: R.fmtChg('bn', 110000),
  bnTiny: R.fmtChg('bn', -0.4),
  pctNeg: R.fmtChg('pct', -0.99),
  bpPos: R.fmtChg('bp', 39),
  ptPos: R.fmtChg('pt', 1.16),
  nullChg: R.fmtChg('bp', null),
  lastZero: R.fmtLast('bn', 0),
  lastB: R.fmtLast('bn', 948700),
  lastT: R.fmtLast('bn', 5790000),
  lastPctBp: R.fmtLast('pct_bp', 5.5),
};
console.log(JSON.stringify(out));
"""


def _run() -> dict:
    if shutil.which("node") is None:
        pytest.skip("无 node，跳过格式化检查")
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


def test_sign_goes_outside_the_dollar() -> None:
    out = _run()
    assert out["bnNeg"] == '<span class="down">-$2B</span>'
    assert out["bnPos"] == '<span class="up">+$110B</span>'


def test_rounded_to_zero_is_not_colored() -> None:
    """显示值四舍五入到 0 就没有方向：红色 $0M 比灰色更误导。"""
    out = _run()
    assert "class" not in out["bnTiny"], out["bnTiny"]  # 无 up/down，只给灰色
    assert "$0M" in out["bnTiny"]


def test_units_keep_their_own_suffix() -> None:
    out = _run()
    assert out["pctNeg"] == '<span class="down">-0.99%</span>'
    assert out["bpPos"] == '<span class="up">+39.0bp</span>'
    assert out["ptPos"] == '<span class="up">+1.16pt</span>'
    assert "class" not in out["nullChg"]  # 缺值只给灰色 —


def test_fmt_last_scales_millions() -> None:
    """bn 的单位是百万：<1e3 → M，≥1e3 → B，≥1e6 → T；0 不写成 $0M。"""
    out = _run()
    assert out["lastZero"] == "$0"
    assert out["lastB"] == "$948.7B"
    assert out["lastT"] == "$5.79T"
    assert out["lastPctBp"] == "5.50%"
