#!/bin/bash
# 每日全量数据拉取 cron 入口（本地）
#
# ⚠️ 分工（2026-08 起）：不依赖 IBKR 的纯 API 数据源（fred/cboe/shapiro/sce/
# treasury/yfinance）已由 GitHub Actions daily-fetch 每日北京时间 05:00 自动拉取
# 并 commit + push，本地 git pull 即得——不需要跑本脚本。
# 本脚本仅当需要拉取 IBKR 依赖数据（ibkr/options/commodities/指数/韩股等）时
# 手动执行，执行前先启动 TWS 或 IB Gateway（4001 实盘 / 4002 模拟）。
#
# 手动触发 Actions：gh workflow run daily-fetch.yml
# 历史 crontab 示例（已过时，勿用）:
#   0 5 * * 1-5 cd /Users/guankai/code/python/market-base && bash src/run_fetch.sh >> logs/cron.log 2>&1

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 日志目录
mkdir -p logs

# bin/ 下全部 fetch_* 脚本，按依赖轻→重排序：纯 API 优先，TWS 依赖放后
FETCHERS=(
    bin/fetch_fred
    bin/fetch_fsi
    bin/fetch_srf
    bin/fetch_cboe
    bin/fetch_shapiro
    bin/fetch_sce
    bin/fetch_yfinance
    bin/fetch_commodities
    bin/fetch_ibkr
    bin/fetch_options
)

set +e  # 单个失败不阻断后续
for fetch in "${FETCHERS[@]}"; do
    name="$(basename "$fetch")"
    echo "===== [$(date '+%F %T')] START $name ====="

    if bash "$fetch"; then
        echo "===== [$(date '+%F %T')] OK    $name ====="
    else
        echo "===== [$(date '+%F %T')] FAIL  $name (exit $?) ====="
    fi
done

echo "----- [$(date '+%F %T')] run_fetch.sh done -----"
