#!/bin/bash
# 每日全量数据拉取 cron 入口
# 依次调用 bin/fetch_* 拉取 IBKR/FRED/CBOE/yfinance/期货/期权数据。
# 单个 fetch 失败不阻断后续——IBKR/options 依赖 TWS 可能失败，不应拖垮纯 API 拉取。
# 要单独拉某个品种请直接用 ./bin/fetch_xxx。
#
# crontab 示例（每个交易日美股收盘后执行，北京时间 05:00）:
#   0 5 * * 1-5 cd /Users/guankai/Documents/ticker-toolkit && bash src/run_fetch.sh >> logs/cron.log 2>&1

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 日志目录
mkdir -p logs

# bin/ 下全部 fetch_* 脚本，按依赖轻→重排序：纯 API 优先，TWS 依赖放后
FETCHERS=(
    bin/fetch_fred
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
