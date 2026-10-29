"""CF Worker 的 cron 触发串与 workflow yml 的 cron 必须指向同一批工作日。

Cloudflare 的 weekday 字段是 1-7 且 1 = 周日（官方文档：`mon-fri` ≡ `2-6`），
GitHub 用标准 cron（0/7 = 周日，1-5 = 周一到周五）。照抄 yml 的 1-5 会让 CF 侧跑到
周一到周四 + 周日、周五整天没有班次，所以两边必须写不同字符串。
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRANGLER = ROOT / "cf-cron-dispatch" / "wrangler.jsonc"
WORKER = ROOT / "cf-cron-dispatch" / "src" / "worker.js"

# Cloudflare：weekday 1-7，1 = 周日
CF_DOW = {"sun": 1, "mon": 2, "tue": 3, "wed": 4, "thu": 5, "fri": 6, "sat": 7}
# 标准 cron（GitHub Actions）：0-6，0 = 周日
GH_DOW = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
WEEKDAYS = {"mon", "tue", "wed", "thu", "fri"}


def _strip_jsonc(text: str) -> str:
    text = re.sub(r'("(?:[^"\\]|\\.)*")|//[^\n]*', lambda m: m.group(1) or "", text)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _crons() -> list[str]:
    return json.loads(_strip_jsonc(WRANGLER.read_text(encoding="utf-8")))["triggers"]["crons"]


def _worker_map() -> dict[str, str]:
    block = re.search(
        r"const CRON_WORKFLOWS = \{(.*?)\};", WORKER.read_text(encoding="utf-8"), re.S
    ).group(1)
    return dict(re.findall(r'^\s*"([^"]+)":\s*"([^"]+)"', block, re.MULTILINE))


def _num(token: str, table: dict[str, int]) -> int:
    return int(token) if token.isdigit() else table[token]


def _weekdays(field: str, table: dict[str, int]) -> set[str]:
    """把 cron 的 weekday 字段展开成星期名集合"""
    rev = {v: k for k, v in table.items()}
    out: set[str] = set()
    for part in field.split(","):
        part = part.strip().lower()
        if part == "*":
            return set(table)
        if "-" in part:
            lo, hi = part.split("-", 1)
            out |= {rev[i] for i in range(_num(lo, table), _num(hi, table) + 1)}
        else:
            out.add(rev[_num(part, table)])
    return out


def _dow(cron: str) -> str:
    return cron.split()[4]


def test_worker_map_matches_triggers() -> None:
    """worker.js 的键与 wrangler.jsonc 的 crons 必须逐字一致，否则 dispatch 进来查不到表"""
    assert set(_worker_map()) == set(_crons())
    for workflow in _worker_map().values():
        assert (ROOT / ".github/workflows" / workflow).exists(), workflow


def test_worker_dispatches_every_configured_cron(tmp_path: Path) -> None:
    """每条 cron（含大写缩写变体）都能正确发到对应 workflow：真跑一遍 scheduled 处理函数"""
    # 目录里没有 package.json，.js 会被当成 CommonJS；另存为 .mjs 才能按 ESM import
    module = tmp_path / "worker.mjs"
    module.write_bytes(WORKER.read_bytes())
    driver = tmp_path / "dispatch.mjs"
    driver.write_text(
        f"""
import worker from {module.as_uri()!r};
const calls = [];
const log = console.log;
console.log = () => {{}};  // worker 自己也打日志，不能混进 std 输出的 JSON
globalThis.fetch = async (url) => {{
  calls.push(url);
  return {{ ok: true, status: 204, text: async () => "" }};
}};
for (const cron of process.argv.slice(2)) {{
  await worker.scheduled({{ cron }}, {{ REPO: "o/r", REF: "main", GH_DISPATCH_TOKEN: "t" }}, {{}});
}}
log(JSON.stringify(calls));
""",
        encoding="utf-8",
    )
    cases = list(_crons())
    # event.cron 的缩写大小写与配置不同也要能查到表
    cases.append("7,22,37,52 13-21 * * MON-FRI")
    out = subprocess.run(["node", str(driver), *cases], capture_output=True, text=True, check=True)
    urls = json.loads(out.stdout)
    assert len(urls) == len(cases)
    expected = _worker_map()
    for url, cron in zip(urls, cases):
        workflow = expected[cron.lower()]
        assert url.endswith(f"/actions/workflows/{workflow}/dispatches"), (cron, url)


def test_cf_cron_covers_monday_to_friday() -> None:
    """CF 侧的工作日必须真是周一到周五"""
    intraday = next(c for c in _crons() if c.split()[1].startswith("13-21"))
    assert _weekdays(_dow(intraday), CF_DOW) == WEEKDAYS


def test_github_cron_covers_monday_to_friday() -> None:
    text = (ROOT / ".github/workflows/intraday-refresh.yml").read_text(encoding="utf-8")
    gh_cron = re.search(r'- cron: "([^"]+)"', text).group(1)
    assert _weekdays(_dow(gh_cron), GH_DOW) == WEEKDAYS


def test_cf_weekday_numbering_differs_from_standard() -> None:
    """同一字符串 1-5 在 CF 上是周日到周四：两边必须写不同字符串的原因"""
    assert _weekdays("1-5", CF_DOW) == {"sun", "mon", "tue", "wed", "thu"}
    assert _weekdays("1-5", GH_DOW) == WEEKDAYS
