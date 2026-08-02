"""ibkr_fetcher.connect_ib 单一化的 TDD 测试（C3）。

钉住:
  1. 4002→4001 回退顺序: 4002 失败后试 4001;
  2. readonly: 4001 自动 readonly=True，4002 不传 readonly;
  3. 全部失败 raise IBKRConnectionError（不再是 sys.exit / 返回 None）;
  4. 成功返回 (ib, port)。
"""

import pytest

from src.fetchers.ibkr_fetcher import IBKRConnectionError, connect_ib


class _FakeIB:
    """记录每次连接尝试；_fail_ports 集合中的端口抛 ConnectionRefusedError。"""

    def __init__(self, fail_ports: set[int]):
        self.fail_ports = fail_ports
        self.attempts: list[int] = []
        self.connect_kwargs: dict | None = None
        self.disconnects = 0

    def connect(self, host, port, **kwargs):
        self.attempts.append(port)
        if port in self.fail_ports:
            raise ConnectionRefusedError(f"port {port} refused")
        self.connect_kwargs = kwargs
        return None

    def disconnect(self):
        self.disconnects += 1


def _install_fake(monkeypatch, fail_ports: set[int]) -> _FakeIB:
    fake = _FakeIB(fail_ports)
    monkeypatch.setattr("src.fetchers.ibkr_fetcher.IB", lambda: fake)
    return fake


# ── 回退顺序 ────────────────────────────────────────────────────────────────


def test_fallback_4002_then_4001(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports={4002})

    ib, port = connect_ib()

    assert fake.attempts == [4002, 4001]
    assert port == 4001
    assert ib is fake


def test_first_port_success_no_fallback(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports=set())

    _, port = connect_ib()

    assert fake.attempts == [4002]
    assert port == 4002


def test_failed_port_disconnects(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports={4002, 4001})

    with pytest.raises(IBKRConnectionError):
        connect_ib()

    assert fake.disconnects == 2


# ── readonly 约定 ────────────────────────────────────────────────────────────


def test_readonly_on_4001(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports={4002})

    connect_ib()

    assert fake.connect_kwargs["readonly"] is True


def test_not_readonly_on_4002(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports=set())

    connect_ib()

    assert fake.connect_kwargs["readonly"] is False


# ── 失败语义 ─────────────────────────────────────────────────────────────────


def test_all_fail_raises_connection_error(monkeypatch):
    _install_fake(monkeypatch, fail_ports={4002, 4001})

    with pytest.raises(IBKRConnectionError):
        connect_ib()


def test_custom_ports(monkeypatch):
    fake = _install_fake(monkeypatch, fail_ports=set())

    ib, port = connect_ib(ports=(4444,))

    assert fake.attempts == [4444]
    assert port == 4444
    assert ib is fake
