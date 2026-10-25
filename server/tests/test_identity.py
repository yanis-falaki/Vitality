import json

import pytest
from vitality_server.identity import TailscaleIdentity
from vitality_server.identity.tailscale import run_command, tailscale_ipv4


def runner(code, payload):
    async def run(argv): return code, json.dumps(payload).encode()
    return run


def raw_runner(code, stdout, calls=None):
    async def run(argv):
        if calls is not None:
            calls.append(list(argv))
        return code, stdout
    return run


async def test_who_returns_computed_name():
    ident = TailscaleIdentity(runner(0, {"Node": {"ComputedName": "laptop", "Name": "laptop.tail1.ts.net."}}))
    assert await ident.who("100.64.0.2") == "laptop"


async def test_who_falls_back_to_name_label():
    assert await TailscaleIdentity(runner(0, {"Node": {"Name": "laptop.tail1.ts.net."}})).who("x") == "laptop"


async def test_who_unknown_is_none():
    assert await TailscaleIdentity(runner(1, {})).who("1.2.3.4") is None


async def test_who_runs_tailscale_whois_for_the_ip():
    calls = []
    await TailscaleIdentity(raw_runner(0, b"{}", calls)).who("100.64.0.2")
    assert calls == [["tailscale", "whois", "--json", "100.64.0.2"]]


@pytest.mark.parametrize("stdout", [b"not json", b"[]", b"{}", b'{"Node": null}', b'{"Node": {}}',
                                    b'{"Node": {"ComputedName": 3}}'])
async def test_who_unparseable_is_none(stdout):
    assert await TailscaleIdentity(raw_runner(0, stdout)).who("1.2.3.4") is None


async def test_tailscale_ipv4_is_first_line():
    calls = []
    assert await tailscale_ipv4(raw_runner(0, b"100.64.0.1\n", calls)) == "100.64.0.1"
    assert calls == [["tailscale", "ip", "-4"]]


@pytest.mark.parametrize("code,stdout", [(1, b"100.64.0.1\n"), (127, b""), (0, b"\n")])
async def test_tailscale_ipv4_failure_raises(code, stdout):
    with pytest.raises(RuntimeError):
        await tailscale_ipv4(raw_runner(code, stdout))


async def test_run_command_missing_binary_is_127():
    assert await run_command(["vitality-no-such-binary-xyz"]) == (127, b"")


async def test_run_command_captures_stdout():
    assert await run_command(["echo", "hi"]) == (0, b"hi\n")
