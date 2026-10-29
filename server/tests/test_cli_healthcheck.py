from vitality_server.cli.healthcheck import healthcheck
from vitality_server.shared.config import ServerConfig


async def tailnet_address() -> str:
    return "100.1.2.3"


def test_healthy_when_health_answers_200(capsys):
    urls = []

    def fetch(url):
        urls.append(url)
        return 200
    assert healthcheck(ServerConfig(), tailnet_address, fetch) == 0
    assert urls == ["http://100.1.2.3:8100/health"]
    assert capsys.readouterr().err == ""


def test_uses_the_configured_port():
    urls = []
    healthcheck(ServerConfig(port=9123), tailnet_address, lambda url: urls.append(url) or 200)
    assert urls == ["http://100.1.2.3:9123/health"]


def test_unhealthy_on_another_status(capsys):
    assert healthcheck(ServerConfig(), tailnet_address, lambda url: 503) == 1
    err = capsys.readouterr().err
    assert err.startswith("vitality-server: unhealthy: ") and "503" in err
    assert len(err.splitlines()) == 1


def test_unhealthy_when_the_server_cannot_be_reached(capsys):
    def refused(url):
        raise OSError(111, "Connection refused")
    assert healthcheck(ServerConfig(), tailnet_address, refused) == 1
    err = capsys.readouterr().err
    assert "Connection refused" in err and len(err.splitlines()) == 1


def test_unhealthy_without_a_tailscale_address(capsys):
    async def no_address() -> str:
        raise RuntimeError("tailscale is not running")
    assert healthcheck(ServerConfig(), no_address, lambda url: pytest_fail()) == 1
    err = capsys.readouterr().err
    assert "tailscale is not running" in err and len(err.splitlines()) == 1


def pytest_fail():
    raise AssertionError("must not fetch without an address")
