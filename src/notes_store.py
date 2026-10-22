"""伴读抽屉的笔记落盘（data/notes/）。

只做本地文件的命名/去重/写入；HTTP 状态码与限流留在 src/server.py 的路由层。
文件名消毒与去重规则和线上 functions/_lib/common.js 的 safeNoteName 保持一致。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import ROOT

NOTES_DIR = ROOT / "data" / "notes"
MAX_BYTES = 1_000_000
# JSON body 要容纳 markdown + thread + 转义；markdown 本身另有 MAX_BYTES 字节上限
BODY_MAX = 4 * 1024 * 1024

_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def note_filename(raw: str | None, fallback: str = "note") -> str:
    """只取 basename、去非法字符、补 .md——防 ../ 穿越与覆盖。"""
    name = Path((raw or "").strip()).name
    name = _BAD_FILENAME.sub("-", name).strip(" .-")
    if not name:
        name = fallback or "note"
    if not name.lower().endswith(".md"):
        name += ".md"
    if len(name) > 120:
        name = name[:-3][:117] + ".md"
    return name


def free_note_path(name: str, base_dir: Path | None = None) -> Path:
    """返回 base_dir 下一个不存在的 .md 路径（同名自动 -2/-3）。"""
    base = Path(base_dir) if base_dir else NOTES_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / name
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 2
    while True:
        candidate = base / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def save_note(
    markdown: str,
    filename: str | None = None,
    title: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    """把 markdown 写到 data/notes/（或测试给的 base_dir），返回实际路径。"""
    name = note_filename(filename or title, fallback="伴读对话")
    path = free_note_path(name, base_dir)
    path.write_text(markdown, encoding="utf-8")
    return path
