# -*- coding: utf-8 -*-
"""Section 组件化 id 完整性校验(layout/section-rollout)。

对每页:提取 HTML 结构层(首个 <script is:inline> 之前)的所有 id="...",
比对基线(git show <base>:<path>)与工作区版本 —— 集合必须相等。
用法: python3 scripts_dev/check_section_ids.py <base_ref> [page ...]
不指定 page 则校验全部 .astro 页(除 geo,参考样例不入校验)。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "frontend/src/pages"


def ids_of(src: str) -> set[str]:
    html = re.split(r"<script is:inline>", src, maxsplit=1)[0]
    return set(re.findall(r'\bid="([\w-]+)"', html))


def git_show(ref: str, path: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout


def main() -> int:
    base = sys.argv[1]
    pages = sys.argv[2:]
    if not pages:
        pages = sorted(
            str(p.relative_to(ROOT)) for p in PAGES_DIR.rglob("*.astro") if "geo/" not in str(p)
        )
    bad = 0
    for page in pages:
        old = ids_of(git_show(base, page))
        new = ids_of(Path(ROOT / page).read_text())
        miss, extra = old - new, new - old
        if miss or extra:
            bad += 1
            detail = f"丢失:{sorted(miss) or '无'}  新增:{sorted(extra) or '无'}"
            print(f"FAIL {page}  {detail}")
        else:
            print(f"OK   {page}  ({len(new)} ids)")
    print(f"\n{len(pages)} 页校验,失败 {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
