import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from anthropic.types.messages import MessageBatchIndividualResponse
from captured import make_captured, make_interaction
from vitality_usermodel.formats import OperationsMeta, read_file

from vitality_observer.config import Mode, ObserverConfig
from vitality_observer.provider.anthropic_api import BATCH_DEADLINE, CREDIT_WAIT, PRICES, AnthropicObserver
from vitality_observer.provider.interface import Outcome
from vitality_observer.request import PROMPT_VERSION
from vitality_observer.work import Interaction

GOOD = json.dumps({
    "transcript": "A terminal is open.",
    "operations": [{"text": "Opened a terminal", "confidence": 8}, {"text": "Typed ls", "confidence": 6}],
})
T0 = 1793715725.0   # 2026-11-03T14:22:05Z


def message_dict(text: str = GOOD, *, model: str = "claude-sonnet-5-5", stop_reason: str = "end_turn",
                 category: str | None = None, tokens: tuple[int, int, int | None] = (1000, 200, 0)) -> dict:
    inputs, outputs, cached = tokens
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_details": {"type": "refusal", "category": category} if stop_reason == "refusal" else None,
        "usage": {"input_tokens": inputs, "output_tokens": outputs, "cache_read_input_tokens": cached},
    }


def message(text: str = GOOD, **kwargs: Any) -> BetaMessage:
    return BetaMessage.model_validate(message_dict(text, **kwargs))


def status_error(cls: type[anthropic.APIStatusError], status: int, error_type: str, text: str,
                 headers: dict[str, str] | None = None) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, headers=headers or {}, request=request)
    return cls(text, response=response, body={"type": "error", "error": {"type": error_type, "message": text}})


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


Respond = Callable[[dict], BetaMessage]


class FakeMessages:
    def __init__(self, respond: Respond):
        self.respond = respond
        self.calls: list[dict] = []

    async def create(self, **kwargs: Any) -> BetaMessage:
        self.calls.append(kwargs)
        return self.respond(kwargs)


class FakeBatches:
    """Ends after one poll; returns `results` (dicts) reversed, so out of order."""

    def __init__(self, results: Callable[[list[dict]], list[dict]] | None = None, fail: Exception | None = None,
                 retrieve_errors: int = 0, ends: bool = True):
        self.make_results = results or (lambda requests: [
            {"custom_id": r["custom_id"], "result": {"type": "succeeded", "message": message_dict()}}
            for r in requests
        ])
        self.fail = fail
        self.retrieve_errors = retrieve_errors     # the first this many retrieves raise
        self.ends = ends
        self.requests: list[dict] = []
        self.created = 0
        self.retrieved = 0

    async def create(self, *, requests: list[dict]) -> Any:
        if self.fail is not None:
            raise self.fail
        self.created += 1
        self.requests = requests
        return SimpleNamespace(id="msgbatch_1", processing_status="in_progress")

    async def retrieve(self, batch_id: str) -> Any:
        assert batch_id == "msgbatch_1"
        self.retrieved += 1
        if self.retrieved <= self.retrieve_errors:
            raise connection_error()
        ended = self.ends and self.retrieved >= 2
        return SimpleNamespace(id=batch_id, processing_status="ended" if ended else "in_progress")

    async def results(self, batch_id: str) -> Any:
        assert batch_id == "msgbatch_1"
        items = [MessageBatchIndividualResponse.model_validate(r) for r in self.make_results(self.requests)]

        async def iterate():
            for item in reversed(items):
                yield item
        return iterate()


class FakeClient:
    def __init__(self, respond: Respond = lambda kwargs: message(), batches: FakeBatches | None = None):
        self.beta = SimpleNamespace(messages=FakeMessages(respond))
        self.messages = SimpleNamespace(batches=batches or FakeBatches())

    @property
    def calls(self) -> list[dict]:
        return self.beta.messages.calls


class Clock:
    """Starts at T0 and moves `step` seconds each time it is read."""

    def __init__(self, step: float = 0.5):
        self.now = T0
        self.step = step

    def __call__(self) -> float:
        now = self.now
        self.now += self.step
        return now


async def no_sleep(seconds: float) -> None:
    pass


@pytest.fixture
def cfg(tmp_path: Path) -> ObserverConfig:
    return ObserverConfig(captures_dir=tmp_path / "captures", model_dir=tmp_path / "model", state_dir=tmp_path / "state")


def make_observer(cfg: ObserverConfig, client: FakeClient, mode: Mode = "direct",
                  clock: Clock | None = None) -> AnthropicObserver:
    config = ObserverConfig(**{**cfg.__dict__, "mode": mode})
    return AnthropicObserver(config, client=cast(Any, client), clock=clock or Clock(), poll_seconds=0.01, sleep=no_sleep)


def records(cfg: ObserverConfig) -> list[dict]:
    path = cfg.state_dir / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def interaction_at(tmp_path: Path, start: str = "2026-11-03T14:22:05Z", **kwargs: Any) -> Interaction:
    captured = make_captured(start)
    name = captured.start.strftime("%Y-%m-%d/%H-%M-%S")
    return make_interaction(tmp_path, captured.model_copy(update={"name": name}), **kwargs)


def three(tmp_path: Path) -> list[Interaction]:
    return [interaction_at(tmp_path, f"2026-11-03T14:2{n}:05Z") for n in range(3)]


async def observe_one(cfg: ObserverConfig, interaction: Interaction, client: FakeClient) -> Outcome:
    [outcome] = await make_observer(cfg, client).observe([interaction])
    return outcome


def failing(error: Exception) -> Respond:
    def respond(kwargs: dict) -> BetaMessage:
        raise error
    return respond


# == Direct mode ==

async def test_direct_success_writes_files_and_logs(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path)

    outcome = await observe_one(cfg, interaction, FakeClient())

    assert (outcome.name, outcome.ok, outcome.reached_model, outcome.detail, outcome.wait_seconds) == (
        interaction.name, True, True, "", None)
    assert interaction.observation.read_text().strip().endswith("A terminal is open.")
    ops, _ = read_file(interaction.operations, "operations")
    assert isinstance(ops, OperationsMeta)
    assert [o.text for o in ops.operations] == ["Opened a terminal", "Typed ls"]
    [record] = records(cfg)
    assert record == outcome.record
    assert record == {
        "time": "2026-11-03T14:22:05Z", "interaction": interaction.name, "ok": True, "detail": "",
        "model": "claude-sonnet-5-5", "prompt": PROMPT_VERSION, "batch": None, "screenshots": 2,
        "operations": 2, "duration_ms": 500, "input_tokens": 1000, "output_tokens": 200,
        "cache_read_input_tokens": 0, "cost": pytest.approx(0.004),
    }


async def test_direct_sends_fallbacks(tmp_path: Path, cfg: ObserverConfig):
    client = FakeClient()

    await observe_one(cfg, interaction_at(tmp_path), client)

    [call] = client.calls
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["timeout"] == cfg.call_timeout_seconds
    assert call["model"] == cfg.model and call["output_config"]["effort"] == cfg.effort


async def test_direct_keeps_order_and_logs_each(tmp_path: Path, cfg: ObserverConfig):
    batch = three(tmp_path)

    outcomes = await make_observer(cfg, FakeClient()).observe(batch)

    assert [o.name for o in outcomes] == [i.name for i in batch]
    assert all(o.ok for o in outcomes)
    assert sorted(r["interaction"] for r in records(cfg)) == sorted(i.name for i in batch)


async def test_refusal_is_a_strike_not_a_file(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path)
    client = FakeClient(lambda kwargs: message("", stop_reason="refusal", category="cyber"))

    outcome = await observe_one(cfg, interaction, client)

    assert (outcome.ok, outcome.reached_model, outcome.detail) == (False, True, "refused (cyber)")
    assert not interaction.observation.exists() and not interaction.operations.exists()
    assert outcome.record["operations"] == 0 and outcome.record["input_tokens"] == 1000


async def test_refusal_without_category(tmp_path: Path, cfg: ObserverConfig):
    client = FakeClient(lambda kwargs: message("", stop_reason="refusal"))

    outcome = await observe_one(cfg, interaction_at(tmp_path), client)

    assert outcome.detail == "refused (unknown)"


async def test_missing_screenshot_reached_model(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path, write_shots=False)
    client = FakeClient()

    outcome = await observe_one(cfg, interaction, client)

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds) == (False, True, None)
    assert "0000.webp" in outcome.detail
    assert client.calls == []
    [record] = records(cfg)
    assert record["ok"] is False and record["model"] is None and record["screenshots"] == 2


async def test_unknown_time_zone_reached_model(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path)
    path = interaction.folder / "interaction.json"
    path.write_text(path.read_text().replace("America/Toronto", "Nowhere/Atlantis"))

    outcome = await observe_one(cfg, interaction, FakeClient())

    assert (outcome.ok, outcome.reached_model) == (False, True)
    assert "Nowhere/Atlantis" in outcome.detail


async def test_credits_exhausted_waits(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.BadRequestError, 400, "billing_error", "Your credit balance is too low")

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds, outcome.detail) == (
        False, False, 6 * 3600, "out of API credits")
    assert CREDIT_WAIT == 6 * 3600


async def test_credits_exhausted_by_message(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.BadRequestError, 400, "invalid_request_error",
                         "Your credit balance is too low to access the Anthropic API.")

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.reached_model, outcome.wait_seconds) == (False, CREDIT_WAIT)


async def test_rate_limit_uses_retry_after(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.RateLimitError, 429, "rate_limit_error", "slow down", {"retry-after": "42"})

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds, outcome.detail) == (
        False, False, 42.0, "rate limited")


async def test_rate_limit_without_retry_after(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.RateLimitError, 429, "rate_limit_error", "slow down")

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.wait_seconds, outcome.detail) == (None, "rate limited")


async def test_bad_request_is_a_strike(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.BadRequestError, 400, "invalid_request_error", "image too large")

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds, outcome.detail) == (
        False, True, None, "api 400: image too large")


async def test_server_error_not_a_strike(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded")

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds) == (False, False, None)
    assert "529" in outcome.detail


async def test_connection_error_not_a_strike(tmp_path: Path, cfg: ObserverConfig):
    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(connection_error())))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds) == (False, False, None)
    assert outcome.detail.startswith("connection error")


async def test_timeout_not_a_strike(tmp_path: Path, cfg: ObserverConfig):
    error = anthropic.APITimeoutError(httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))

    outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(error)))

    assert (outcome.ok, outcome.reached_model) == (False, False)
    assert outcome.detail == "timed out after 600s"


async def test_invalid_json_reply(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path)

    outcome = await observe_one(cfg, interaction, FakeClient(lambda kwargs: message("{not json")))

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds) == (False, True, None)
    assert outcome.detail.startswith("invalid reply: ")
    assert not interaction.observation.exists()


async def test_cut_off_reply(tmp_path: Path, cfg: ObserverConfig):
    client = FakeClient(lambda kwargs: message('{"transcript": "A ter', stop_reason="max_tokens"))

    outcome = await observe_one(cfg, interaction_at(tmp_path), client)

    assert (outcome.ok, outcome.reached_model, outcome.detail) == (False, True, "reply cut off")


async def test_unexpected_error_never_raises(tmp_path: Path, cfg: ObserverConfig, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="vitality_observer"):
        outcome = await observe_one(cfg, interaction_at(tmp_path), FakeClient(failing(RuntimeError("boom"))))

    assert (outcome.ok, outcome.reached_model, outcome.detail) == (False, False, "RuntimeError: boom")
    assert "boom" in caplog.text


async def test_cannot_write_files_not_a_strike(tmp_path: Path, cfg: ObserverConfig):
    interaction = interaction_at(tmp_path)
    (tmp_path / "model").write_text("not a folder")

    outcome = await observe_one(cfg, interaction, FakeClient())

    assert (outcome.ok, outcome.reached_model) == (False, False)
    assert outcome.detail
    assert records(cfg)[0]["ok"] is False


async def test_calls_log_unwritable_warns_once(tmp_path: Path, cfg: ObserverConfig, caplog: pytest.LogCaptureFixture):
    cfg.state_dir.parent.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.write_text("not a folder")
    observer = make_observer(cfg, FakeClient())

    with caplog.at_level(logging.WARNING, logger="vitality_observer"):
        outcomes = await observer.observe(three(tmp_path))

    assert all(o.ok for o in outcomes)
    assert caplog.text.count("calls.jsonl") == 1


# == Cost ==

async def test_cost_counts_cache_reads(tmp_path: Path, cfg: ObserverConfig):
    client = FakeClient(lambda kwargs: message(model="claude-opus-5-5", tokens=(1000, 200, 2000)))

    outcome = await observe_one(cfg, interaction_at(tmp_path), client)

    assert PRICES["claude-opus-5-5"] == (4.0, 20.0, 0.20)
    assert outcome.record["model"] == "claude-opus-5-5"
    assert outcome.record["cost"] == pytest.approx((1000 * 4.0 + 200 * 20.0 + 2000 * 0.20) / 1e6)


@pytest.mark.parametrize("model, expected", [
    ("claude-sonnet-5-5", (1000 * 2.0 + 200 * 10.0 + 5000 * 0.10) / 1e6),
    ("claude-haiku-5-5", (1000 * 0.10 + 200 * 0.50 + 5000 * 0.01) / 1e6),
])
async def test_cache_read_price_per_model(tmp_path: Path, cfg: ObserverConfig, model: str, expected: float):
    client = FakeClient(lambda kwargs: message(model=model, tokens=(1000, 200, 5000)))

    outcome = await observe_one(cfg, interaction_at(tmp_path), client)

    assert outcome.record["cost"] == pytest.approx(expected)


async def test_unknown_model_cost_none(tmp_path: Path, cfg: ObserverConfig):
    client = FakeClient(lambda kwargs: message(model="claude-mystery-9"))

    outcome = await observe_one(cfg, interaction_at(tmp_path), client)

    assert outcome.ok and outcome.record["cost"] is None and outcome.record["model"] == "claude-mystery-9"


async def test_cost_halved_in_batch(tmp_path: Path, cfg: ObserverConfig):
    [outcome] = await make_observer(cfg, FakeClient(), "batch").observe([interaction_at(tmp_path)])

    assert outcome.ok
    assert outcome.record["cost"] == pytest.approx(0.004 / 2)


# == Batch mode ==

async def test_batch_maps_results_out_of_order(tmp_path: Path, cfg: ObserverConfig):
    batch = three(tmp_path)

    def results(requests: list[dict]) -> list[dict]:
        # i1 is answered with a different transcript, so a mix-up would show.
        return [
            {"custom_id": r["custom_id"], "result": {"type": "succeeded", "message": message_dict(
                json.dumps({"transcript": f"Screen {r['custom_id']}.",
                            "operations": [{"text": "Looked", "confidence": 5}]}))}}
            for r in requests
        ]

    batches = FakeBatches(results)
    outcomes = await make_observer(cfg, FakeClient(batches=batches), "batch").observe(batch)

    assert [r["custom_id"] for r in batches.requests] == ["i0", "i1", "i2"]
    assert [o.name for o in outcomes] == [i.name for i in batch]
    assert all(o.ok for o in outcomes)
    for index, interaction in enumerate(batch):
        assert interaction.observation.read_text().strip().endswith(f"Screen i{index}.")
    for outcome in outcomes:
        assert outcome.record["batch"] == "msgbatch_1"
        assert outcome.record["time"] == "2026-11-03T14:22:05Z"
    assert batches.retrieved == 2


async def test_batch_sends_no_fallbacks(tmp_path: Path, cfg: ObserverConfig):
    batches = FakeBatches()

    await make_observer(cfg, FakeClient(batches=batches), "batch").observe([interaction_at(tmp_path)])

    [request] = batches.requests
    params = request["params"]
    assert "fallbacks" not in params and "betas" not in params and "timeout" not in params
    assert params["model"] == cfg.model


async def test_batch_failed_results(tmp_path: Path, cfg: ObserverConfig):
    batch = [*three(tmp_path), interaction_at(tmp_path, "2026-11-03T14:23:05Z")]

    def results(requests: list[dict]) -> list[dict]:
        def errored(kind: str) -> dict:
            return {"type": "errored", "error": {"type": "error", "error": {"type": kind, "message": "nope"}}}
        return [
            {"custom_id": "i0", "result": errored("invalid_request_error")},
            {"custom_id": "i1", "result": errored("overloaded_error")},
            {"custom_id": "i2", "result": {"type": "expired"}},
            {"custom_id": "i3", "result": {"type": "canceled"}},
        ]

    outcomes = await make_observer(cfg, FakeClient(batches=FakeBatches(results)), "batch").observe(batch)

    assert [o.ok for o in outcomes] == [False] * 4
    assert [o.reached_model for o in outcomes] == [True, False, False, False]
    assert all(o.detail for o in outcomes)
    assert len(records(cfg)) == 4


async def test_batch_bad_interaction_not_sent(tmp_path: Path, cfg: ObserverConfig):
    good = interaction_at(tmp_path)
    bad = interaction_at(tmp_path, "2026-11-03T14:21:05Z", write_shots=False)
    batches = FakeBatches()

    outcomes = await make_observer(cfg, FakeClient(batches=batches), "batch").observe([bad, good])

    assert [r["custom_id"] for r in batches.requests] == ["i1"]
    assert [(o.ok, o.reached_model) for o in outcomes] == [(False, True), (True, True)]


async def test_batch_level_failure(tmp_path: Path, cfg: ObserverConfig):
    error = status_error(anthropic.BadRequestError, 400, "billing_error", "Your credit balance is too low")
    batch = three(tmp_path)

    outcomes = await make_observer(cfg, FakeClient(batches=FakeBatches(fail=error)), "batch").observe(batch)

    assert [o.name for o in outcomes] == [i.name for i in batch]
    assert all((o.ok, o.reached_model, o.wait_seconds) == (False, False, CREDIT_WAIT) for o in outcomes)
    assert len(records(cfg)) == 3


async def test_batch_missing_result(tmp_path: Path, cfg: ObserverConfig):
    batches = FakeBatches(lambda requests: [])

    [outcome] = await make_observer(cfg, FakeClient(batches=batches), "batch").observe([interaction_at(tmp_path)])

    assert (outcome.ok, outcome.reached_model) == (False, False)
    assert outcome.detail


def errored(kind: str, text: str = "nope") -> dict:
    return {"type": "errored", "error": {"type": "error", "error": {"type": kind, "message": text}}}


@pytest.mark.parametrize("kind, expected", [
    ("billing_error", (False, CREDIT_WAIT, "out of API credits")),
    ("rate_limit_error", (False, None, "rate limited")),
    ("overloaded_error", (False, None, "overloaded_error: nope")),
    ("api_error", (False, None, "api_error: nope")),
    ("invalid_request_error", (True, None, "invalid_request_error: nope")),
])
async def test_batch_errored_maps_by_type(tmp_path: Path, cfg: ObserverConfig, kind: str, expected: tuple):
    batches = FakeBatches(lambda requests: [{"custom_id": "i0", "result": errored(kind)}])

    [outcome] = await make_observer(cfg, FakeClient(batches=batches), "batch").observe([interaction_at(tmp_path)])

    assert outcome.ok is False
    assert (outcome.reached_model, outcome.wait_seconds, outcome.detail) == expected


async def test_batch_retrieve_errors_keep_polling(tmp_path: Path, cfg: ObserverConfig,
                                                  caplog: pytest.LogCaptureFixture):
    batches = FakeBatches(retrieve_errors=2)
    batch = three(tmp_path)

    with caplog.at_level(logging.WARNING, logger="vitality_observer"):
        outcomes = await make_observer(cfg, FakeClient(batches=batches), "batch").observe(batch)

    assert all(o.ok for o in outcomes)
    assert batches.created == 1 and batches.retrieved == 3   # two failures, then ended
    assert caplog.text.count("cannot check batch") == 1


async def test_batch_gives_up_after_deadline(tmp_path: Path, cfg: ObserverConfig):
    batches = FakeBatches(retrieve_errors=1000, ends=False)
    clock = Clock(step=3600)

    [outcome] = await make_observer(cfg, FakeClient(batches=batches), "batch", clock).observe([interaction_at(tmp_path)])

    assert (outcome.ok, outcome.reached_model, outcome.wait_seconds, outcome.detail) == (
        False, False, None, "batch did not finish")
    assert batches.created == 1
    assert batches.retrieved == BATCH_DEADLINE // 3600 - 1
    assert outcome.record["batch"] == "msgbatch_1"
