"""hedge_planner 结构盈亏计算的最小校验"""

from datetime import date, timedelta

from src.hedge_planner import collar, naked_put, pick_expirations, put_spread, snap


def test_put_spread_math():
    # 会话实例：买 380 put + 卖 360 put，净成本 6.95
    s = put_spread(380, 360, 6.95)
    assert s["max_loss"] == 6.95
    assert abs(s["max_profit"] - 13.05) < 1e-9  # 间距 20 - 成本
    assert abs(s["breakeven"] - 373.05) < 1e-9  # 380 - 成本


def test_naked_put():
    p = naked_put(380, 18.60)
    assert p["max_loss"] == 18.60
    assert abs(p["breakeven"] - 361.40) < 1e-9
    assert p["max_profit"] is None  # 下方不封顶


def test_collar_net_credit():
    c = collar(380, 450, -1.40)  # 卖 call 收入超过 put 成本 → 负成本
    assert c["floor"] == 380
    assert c["cap"] == 450
    assert c["max_loss"] == -1.40


def test_snap_and_pick():
    assert snap([360, 370, 380, 390], 383) == 380
    # 用相对 today 的到期日，避免硬编码日期随时间漂移
    today = date.today()
    # 日期间隔取 targets 的一半，确保每个目标都有唯一最近月
    exps = [
        (today + timedelta(days=15)).isoformat(),
        (today + timedelta(days=30)).isoformat(),
        (today + timedelta(days=60)).isoformat(),
        (today + timedelta(days=90)).isoformat(),
    ]
    picked = pick_expirations(exps, targets=(30, 60, 90))
    assert len(picked) == 3 and len(set(picked)) == 3

    # 已过期合约必须被忽略（原失败模式：硬编码日期漂移后目标塌缩到同一合约）
    stale = [(today - timedelta(days=10)).isoformat(), *exps]
    picked = pick_expirations(stale, targets=(30, 60, 90))
    assert len(picked) == 3 and len(set(picked)) == 3

    # 全链已过期 → 返回空列表而非抛 ValueError
    all_stale = [
        (today - timedelta(days=10)).isoformat(),
        (today - timedelta(days=5)).isoformat(),
    ]
    assert pick_expirations(all_stale) == []
