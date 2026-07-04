# ADR 0002 — 源码包从 `code/` 重命名为 `src/`

**状态**：已接受
**日期**：2026-07-04

## 背景

项目原将 Python 源码置于根目录 `code/` 包下，命令为 `uv run python -m code.analyze`。此包名与 Python 标准库 `code` 模块同名。

在引入 pytest 测试框架时发现：pytest 在 `pytest_configure` 阶段会 `import pdb`，而 `pdb` 模块内部 `import code`（stdlib 的交互式解释器模块）。由于项目根在 `sys.path` 中且 `code/` 包（目录）优先级高于 stdlib `code.py`（单文件模块），`import code` 拿到的是我们的包而非 stdlib，导致 `AttributeError: module 'code' has no attribute 'InteractiveConsole'`，pytest 完全无法启动。

Python 错误信息明确建议："consider renaming `code/__init__.py` since it has the same name as the standard library module named `code`"。

此前此问题被掩盖，因为：
- `python -m code.analyze` 用 `-m` 把 `code` 当包路径处理，不触发裸 `import code`
- 项目内代码用 `from code.xxx import` 或相对导入，不裸 `import code`
- 但任何第三方库（pytest/pdb 等）裸 `import code` 都会崩

## 决策

将源码包从 `code/` 重命名为 `src/`。所有命令、导入、文档同步更新：

- 目录 `code/` → `src/`
- 命令 `python -m code.analyze` → `python -m src.analyze`
- `bin/fetch_*` 内部 `-m code.fetchers.*` → `-m src.fetchers.*`
- 导入 `from code.indicators` → `from src.indicators`
- `pyproject.toml` 的 `known-first-party = ["code"]` → `["src"]`
- `AGENTS.md`、docstring 全量更新
- 删除根 `conftest.py`（先前为绕过此问题写的 stdlib `code` 预注入逻辑，不再需要）

## 备选方案

- **保留 `code/` + 根 conftest 绕过**：在根 `conftest.py` 把 stdlib `code` 预注入 `sys.modules`。已验证可行，但属症状修法——硬编码 stdlib `code.py` 路径跨平台脆弱，且未来任何裸 `import code` 的第三方库仍会撞坑。否决。
- **重命名为 `kl/`**（K线缩写）：短但不英文直觉。`src/` 更通用、符合 Python 社区惯例。

## 后果

- 优点：根因消除，pytest 正常运行，未来无第三方库撞坑风险；包名 `src` 通用无歧义。
- 代价：破坏性变更，所有历史命令、cron 配置、外部脚本引用 `code.` 的需更新。本次已全量迁移（AGENTS.md / bin/ / 源码 / pyproject），零残留。
- 注意：`src/` 作为包名较泛，若未来项目对外发布为包，需考虑更语义化的名字。当前为内部工具，可接受。
