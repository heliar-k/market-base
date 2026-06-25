#!/bin/bash
# IBKR 日线拉取 cron 包装脚本
# 用法: bash code/run_fetch.sh [--symbols SPX,AAPL] [--days 365]
#
# crontab 示例（每个交易日美股收盘后执行，北京时间 05:00）:
#   0 5 * * 1-5 cd /Users/guankai/Documents/K线分析 && bash code/run_fetch.sh >> logs/cron.log 2>&1

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 日志目录
mkdir -p logs

# 使用 uv 运行
uv run python code/fetch_ibkr.py "$@"
