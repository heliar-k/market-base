"""把一段 ESM 脚本交给 node 跑，返回 stdout 的 JSON（JS 级测试共用）。

ai-md / ai-config / Pages Functions 的测试都需要「无 node 就 skip、临时 .mjs、
subprocess、json.loads」这套壳；单源在这里，测试文件只写脚本与断言。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

NODE = shutil.which("node")


def run_node(script: str, timeout: int = 60) -> dict:
    if NODE is None:
        pytest.skip("无 node，跳过 JS 检查")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(script)
        tmp = f.name
    try:
        r = subprocess.run(
            [NODE, tmp], capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def node_check(path: Path) -> None:
    """语法检查一个 JS 文件（失败直接抛断言）。"""
    if NODE is None:
        pytest.skip("无 node")
    r = subprocess.run(
        [NODE, "--check", str(path)], capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 0, f"{path}: {r.stderr}"
