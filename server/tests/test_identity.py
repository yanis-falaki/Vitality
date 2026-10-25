import json

import pytest
from vitality_server.identity import TailscaleIdentity
from vitality_server.identity.tailscale import run_command, tailscale_ipv4


def raw_runner(code, stdout, calls=None):
    async def run(argv):
        if calls is not None:
            calls.append(list(argv))
        return code, stdout
    return run


SELF = {"Self": {"DNSName": "vitality-librarian.tail1234.ts.net."}}


def tailnet(whois, status=SELF, whois_code=0, status_code=0, calls=None):
    """A fake `tailscale`: `whois --json <ip>` gives `whois`, `status --json` gives `status`
    (each a dict to encode, or raw bytes)."""
    def encode(x): return x if isinstance(x, bytes) else json.dumps(x).encode()

    async def run(argv):
        if calls is not None:
            calls.append(list(argv))
        if argv[1] == "status":
            return status_code, encode(status)
        return whois_code, encode(whois)
    return run


def node(name, **extra):
    return {"Node": {"Name": name, **extra}}


async def test_who_is_the_first_label_of_a_node_on_this_tailnet():
    assert await TailscaleIdentity(tailnet(node("laptop.tail1234.ts.net."))).who("100.64.0.2") == "laptop"


async def test_who_ignores_computed_name():
    whois = node("laptop.tail1234.ts.net.", ComputedName="renamed-by-the-os")
    assert await TailscaleIdentity(tailnet(whois)).who("x") == "laptop"


async def test_who_ignores_case_and_the_trailing_dot_of_the_suffix():
    status = {"Self": {"DNSName": "Vitality-Librarian.TAIL1234.ts.net"}}
    assert await TailscaleIdentity(tailnet(node("laptop.tail1234.ts.net."), status)).who("x") == "laptop"


@pytest.mark.parametrize("whois", [
    node("laptop.other-tailnet.ts.net."),                                  # shared in, same label
    node("laptop.evil.tail1234.ts.net."),                                  # suffix only ends alike
    node("laptop.ts.net."),
    node("laptop"),
    node("tail1234.ts.net."),
    node(".tail1234.ts.net."),
    node("laptop.other-tailnet.ts.net.", ComputedName="laptop"),           # ComputedName is not trusted
    {"Node": {"ComputedName": "laptop"}},                                  # no Name at all
    node(3),
])
async def test_who_outside_this_tailnet_is_none(whois):
    assert await TailscaleIdentity(tailnet(whois)).who("x") is None


@pytest.mark.parametrize("status, code", [
    ({}, 0),                                                               # no Self
    ({"Self": None}, 0),
    ({"Self": {}}, 0),
    ({"Self": {"DNSName": ""}}, 0),
    ({"Self": {"DNSName": "vitality-librarian."}}, 0),                     # no tailnet part
    ({"Self": {"DNSName": 3}}, 0),
    (b"not json", 0),
    (b"[]", 0),
    (SELF, 1),                                                             # tailscale failed
])
async def test_who_without_this_tailnets_name_is_none(status, code):
    assert await TailscaleIdentity(tailnet(node("laptop.tail1234.ts.net."), status, status_code=code)).who("x") is None


async def test_the_tailnet_name_is_fetched_once_and_cached():
    calls = []
    ident = TailscaleIdentity(tailnet(node("laptop.tail1234.ts.net."), calls=calls))
    assert await ident.who("100.64.0.2") == "laptop" and await ident.who("100.64.0.3") == "laptop"
    assert calls.count(["tailscale", "status", "--json"]) == 1
    assert calls.count(["tailscale", "whois", "--json", "100.64.0.2"]) == 1


async def test_an_unreadable_status_is_retried_on_the_next_request():
    calls, answers = [], [(1, b""), (0, json.dumps(SELF).encode())]

    async def run(argv):
        calls.append(list(argv))
        if argv[1] == "status":
            return answers.pop(0)
        return 0, json.dumps(node("laptop.tail1234.ts.net.")).encode()

    ident = TailscaleIdentity(run)
    assert await ident.who("x") is None and await ident.who("x") == "laptop"


async def test_who_unknown_is_none():
    assert await TailscaleIdentity(tailnet({}, whois_code=1)).who("1.2.3.4") is None


async def test_who_runs_tailscale_whois_for_the_ip():
    calls = []
    await TailscaleIdentity(tailnet({}, calls=calls)).who("100.64.0.2")
    assert ["tailscale", "whois", "--json", "100.64.0.2"] in calls


@pytest.mark.parametrize("stdout", [b"not json", b"[]", b"{}", b'{"Node": null}', b'{"Node": {}}',
                                    b'{"Node": {"Name": 3}}'])
async def test_who_unparseable_is_none(stdout):
    assert await TailscaleIdentity(tailnet(stdout)).who("1.2.3.4") is None


async def test_tailscale_ipv4_is_first_line():
    calls = []
    assert await tailscale_ipv4(raw_runner(0, b"100.64.0.1\n", calls)) == "100.64.0.1"
    assert calls == [["tailscale", "ip", "-4"]]


@pytest.mark.parametrize("code,stdout", [(1, b"100.64.0.1\n"), (127, b""), (0, b"\n"), (0, b"hello\n"),
                                         (0, b"fd7a::1\n"), (0, b"100.64.0.1.5\n"), (0, b"0.0.0.0 x\n")])
async def test_tailscale_ipv4_failure_raises(code, stdout):
    with pytest.raises(RuntimeError):
        await tailscale_ipv4(raw_runner(code, stdout))


async def test_run_command_missing_binary_is_127():
    assert await run_command(["vitality-no-such-binary-xyz"]) == (127, b"")


async def test_run_command_captures_stdout():
    assert await run_command(["echo", "hi"]) == (0, b"hi\n")


async def test_run_command_unexecutable_is_127(tmp_path):
    not_executable = tmp_path / "tailscale"
    not_executable.write_text("#!/bin/sh\n")
    not_executable.chmod(0o644)
    assert await run_command([str(not_executable)]) == (127, b"")
