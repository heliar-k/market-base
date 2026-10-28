"""入库数据产物的格式契约。

`.pre-commit-config.yaml` 里 `pretty-format-json` 按 `^data/` 排除（compact 单行是
fetcher 定的，格式化工具插手会让 git diff 不可读、体积翻倍），于是只剩
`end-of-file-fixer` 会去改 data/ 下的文件。写盘处漏补换行时，那次 commit 会被 hook
顺手多改一行产物，看起来像数据变动 —— 所以由 fetcher 自己补，这里守住。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data"
# 不入库的本地状态（.gitignore）：浏览器 profile 缓存、指标缓存、笔记
NOT_COMMITTED = {"cache", "gex", "notes", "browser-profile"}


def test_committed_json_artifacts_end_with_newline() -> None:
    bad = [
        str(p.relative_to(ROOT))
        for p in sorted(ROOT.rglob("*.json"))
        if not NOT_COMMITTED.intersection(p.relative_to(ROOT).parts)
        and not p.read_bytes().endswith(b"\n")
    ]
    assert not bad, f"JSON 写盘未补尾部换行（end-of-file-fixer 会替它补）：{bad[:10]}"


def test_options_chain_json_written_with_newline(tmp_path) -> None:
    """期权链参数快照（data/options/{sym}_chain.json）写盘同样补换行。"""
    from src.fetchers.options_fetcher import save_chain_json

    save_chain_json("AAPL", [], tmp_path)
    assert (tmp_path / "AAPL_chain.json").read_bytes().endswith(b"\n")
