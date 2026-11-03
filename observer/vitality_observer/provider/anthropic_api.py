"""Implements `Observer` (see interface.py) with the Anthropic Messages API, one request per interaction.

In `direct` mode the requests of a batch run at the same time (the loop hands over at most
`parallel`), each allowed to fall back server-side to another model. In `batch` mode they go out
as one Message Batch, at half price, which is polled until it ends; batches never fall back.

How each interaction's outcome is decided, most specific first:

  condition                                        ok     reached_model  wait_seconds        detail
  reply parsed, files written                      True   True           None                ""
  bad interaction (`interaction.json`, a screenshot
    or its time zone missing or invalid)           False  True           None                the message
  `stop_reason == "refusal"`                       False  True           None                refused (<category or unknown>)
  `stop_reason == "max_tokens"`, or a bad reply    False  True           None                reply cut off / invalid reply: ...
  billing error, or "credit balance is too low"    False  False          CREDIT_WAIT         out of API credits
  rate limited                                     False  False          retry-after or None rate limited
  other API error with status 400 or 413           False  True           None                api <status>: <message>
  other API error, connection error, timeout       False  False          None                short reason
  anything else (files not writable, a bug, ...)   False  False          None                <ExceptionType>: <message>, logged

A batch result that errored maps by its error type the same way: `billing_error` (or "credit
balance is too low") waits CREDIT_WAIT, `rate_limit_error` is "rate limited", and only an
`invalid_request_error` is `reached_model`; a canceled or expired result never is.
A failure of the batch as a whole is never its interactions' fault (never `reached_model`). A batch
that cannot be submitted, or whose results cannot be read, takes `wait_seconds` and `detail` from
the table for each interaction without a result (results already read are still used). A failing
status check while polling is logged once per streak and polling goes on; a batch not ended
BATCH_DEADLINE after submission fails each interaction with "batch did not finish". An interaction
that failed before submission is logged with `batch` null and `duration_ms` 0.
"""

# == Contract ==

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfoNotFoundError

import anthropic
from anthropic.types import Message
from anthropic.types.messages import MessageBatchResult
from anthropic.types.beta import BetaMessage
from vitality_protocol.messages import Interaction as Captured

from vitality_observer.config import ObserverConfig
from vitality_observer.provider.interface import Observer, Outcome
from vitality_observer.request import PROMPT_VERSION, build_params, parse_reply, read_captured
from vitality_observer.work import Interaction
from vitality_observer.writer import write_evidence

PRICES: dict[str, tuple[float, float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
"""Dollars per million (input, output, cache read) tokens; batches cost half."""

CREDIT_WAIT = 6 * 3600
"""Seconds to wait when the account is out of credits."""

BATCH_DEADLINE = 25 * 3600
"""Seconds after submission a batch may take to end before it counts as failed (the API's limit is 24 h)."""

FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""The beta that lets a direct request fall back to another model (`fallbacks="default"`)."""


# == Implementation ==

log = logging.getLogger("vitality_observer")


@dataclass(frozen=True)
class _Verdict:
    ok: bool
    reached_model: bool
    detail: str
    wait_seconds: float | None = None
    operations: int = 0         # operations written


class AnthropicObserver(Observer):
    """Observer calling the Anthropic API in `config.mode`; `client`, `clock` and `sleep` can be replaced in tests.

    Without a `client`, an `anthropic.AsyncAnthropic()` reads its key from the environment.
    In batch mode the batch is polled every `poll_seconds`.
    """

    def __init__(self, config: ObserverConfig, client: anthropic.AsyncAnthropic | None = None,
                 clock: Callable[[], float] = time.time, poll_seconds: float = 30,
                 sleep: Callable[[float], Awaitable[object]] = asyncio.sleep):
        self._config = config
        self._client = client if client is not None else anthropic.AsyncAnthropic()
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        self._calls_log_broken = False      # warned that calls.jsonl cannot be written

    async def observe(self, batch: Sequence[Interaction]) -> list[Outcome]:
        if self._config.mode == "batch":
            return await self._observe_batch(batch)
        settled = await asyncio.gather(*(self._observe_direct(i) for i in batch), return_exceptions=True)
        outcomes: list[Outcome] = []
        for interaction, outcome in zip(batch, settled):
            if isinstance(outcome, Outcome):
                outcomes.append(outcome)
            elif isinstance(outcome, Exception):    # a bug; the outcome is still owed
                outcomes.append(self._finish(interaction, None, _unexpected(outcome, interaction.name),
                                             None, self._clock(), 0, None))
            else:
                raise outcome                       # cancelled, or another BaseException
        return outcomes

    async def _observe_direct(self, interaction: Interaction) -> Outcome:
        config = self._config
        started = self._clock()
        captured, prepared = _prepare(interaction, config)
        message: BetaMessage | None = None
        if isinstance(prepared, _Verdict):
            verdict = prepared
        else:
            assert captured is not None
            try:
                message = cast(BetaMessage, await self._client.beta.messages.create(  # not streamed
                    **prepared, betas=[FALLBACK_BETA], fallbacks="default", timeout=config.call_timeout_seconds))
            except Exception as e:
                verdict = _from_error(e, interaction.name, config.call_timeout_seconds)
            else:
                verdict = _settle(interaction, captured, message)
        duration_ms = round((self._clock() - started) * 1000)
        return self._finish(interaction, captured, verdict, message, started, duration_ms, None)

    async def _observe_batch(self, batch: Sequence[Interaction]) -> list[Outcome]:
        prepared = [_prepare(interaction, self._config) for interaction in batch]
        requests = [
            {"custom_id": f"i{index}", "params": params}
            for index, (_, params) in enumerate(prepared) if not isinstance(params, _Verdict)
        ]
        batches = self._client.messages.batches
        started = self._clock()
        batch_id: str | None = None
        results: dict[str, MessageBatchResult] = {}
        failure = _Verdict(False, False, "no result in the batch")
        if requests:
            try:
                created = await batches.create(requests=requests)  # pyright: ignore[reportArgumentType]  # params from build_params
                batch_id = created.id
                if await self._wait_for_end(batch_id, created.processing_status, started):
                    async for item in await batches.results(batch_id):
                        results[item.custom_id] = item.result
                else:
                    failure = _Verdict(False, False, "batch did not finish")
            except Exception as e:
                # The batch as a whole failed: never the interactions' fault, whatever the error.
                failure = replace(_from_error(e, f"batch {batch_id or '(not submitted)'}", None), reached_model=False)
        duration_ms = round((self._clock() - started) * 1000)

        outcomes: list[Outcome] = []
        for index, (interaction, (captured, params)) in enumerate(zip(batch, prepared)):
            if isinstance(params, _Verdict):    # never submitted
                outcomes.append(self._finish(interaction, captured, params, None, started, 0, None))
                continue
            assert captured is not None
            try:
                verdict, message = _judge_result(interaction, captured, results.get(f"i{index}"), failure)
            except Exception as e:      # a bug; the other results must not be lost
                verdict, message = _unexpected(e, interaction.name), None
            outcomes.append(self._finish(interaction, captured, verdict, message, started, duration_ms, batch_id))
        return outcomes

    async def _wait_for_end(self, batch_id: str, status: str, submitted: float) -> bool:
        """Poll until the batch has ended (True) or BATCH_DEADLINE has passed since `submitted` (False).

        A failing status check is logged, once until one works again, and polling goes on.
        """
        failing = False
        while status != "ended":
            if self._clock() - submitted >= BATCH_DEADLINE:
                log.warning("batch %s has not ended after %d h; giving up on it", batch_id, BATCH_DEADLINE // 3600)
                return False
            await self._sleep(self._poll_seconds)
            try:
                status = (await self._client.messages.batches.retrieve(batch_id)).processing_status
            except Exception as e:
                if not failing:
                    log.warning("cannot check batch %s (%s); still polling", batch_id, _short(f"{type(e).__name__}: {e}"))
                failing = True
            else:
                failing = False
        return True

    def _finish(self, interaction: Interaction, captured: Captured | None, verdict: _Verdict,
                message: Message | BetaMessage | None, started: float, duration_ms: int,
                batch_id: str | None) -> Outcome:
        """The outcome for `interaction`, after appending its line to calls.jsonl."""
        usage = message.usage if message is not None else None
        model = message.model if message is not None else None
        cached = usage.cache_read_input_tokens if usage is not None else None
        record = {
            "time": datetime.fromtimestamp(started, UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "interaction": interaction.name,
            "ok": verdict.ok,
            "detail": verdict.detail,
            "model": model,
            "prompt": PROMPT_VERSION,
            "batch": batch_id,
            "screenshots": len(captured.shots) if captured is not None else None,
            "operations": verdict.operations,
            "duration_ms": duration_ms,
            "input_tokens": usage.input_tokens if usage is not None else None,
            "output_tokens": usage.output_tokens if usage is not None else None,
            "cache_read_input_tokens": cached,
            "cost": None if usage is None or model is None else _cost(
                model, usage.input_tokens, usage.output_tokens, cached or 0, halved=self._config.mode == "batch"),
        }
        self._log_call(self._config.state_dir / "calls.jsonl", record)
        return Outcome(interaction.name, verdict.ok, verdict.reached_model, verdict.detail,
                       verdict.wait_seconds, record)

    def _log_call(self, path: Path, record: dict) -> None:
        """Append `record` to `path`; a failure is only logged, once until a write works again."""
        error = _append(path, record)
        if error is not None and not self._calls_log_broken:
            log.warning("cannot add to %s (%s); calls go unrecorded until it works again", path, error)
        self._calls_log_broken = error is not None


def _prepare(interaction: Interaction, config: ObserverConfig) -> tuple[Captured | None, dict | _Verdict]:
    """The interaction's `interaction.json` (if readable) and its request parameters, or why it is bad."""
    captured: Captured | None = None
    try:
        captured = read_captured(interaction)
        return captured, build_params(interaction, config, captured)
    except ValueError as e:
        return captured, _Verdict(False, True, _short(str(e)))
    except ZoneInfoNotFoundError as e:
        return captured, _Verdict(False, True, _short(f"unknown time zone: {e.args[0] if e.args else e}"))
    except Exception as e:
        return captured, _unexpected(e, interaction.name)


def _judge_result(interaction: Interaction, captured: Captured, result: MessageBatchResult | None,
                  failure: _Verdict) -> tuple[_Verdict, Message | None]:
    """The verdict for a submitted interaction from its batch result, and the message if it succeeded.

    Without a result the verdict is `failure`, the batch's own.
    """
    if result is None:
        return failure, None
    if result.type == "succeeded":
        return _settle(interaction, captured, result.message), result.message
    if result.type == "errored":
        return _from_batch_error(result.error.error.type, result.error.error.message), None
    return _Verdict(False, False, f"request {result.type}"), None


def _settle(interaction: Interaction, captured: Captured, message: Message | BetaMessage) -> _Verdict:
    """Write the evidence files from the model's answer, or say why not."""
    if message.stop_reason == "refusal":
        category = message.stop_details.category if message.stop_details is not None else None
        return _Verdict(False, True, f"refused ({category or 'unknown'})")
    if message.stop_reason == "max_tokens":
        return _Verdict(False, True, "reply cut off")
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        return _Verdict(False, True, "invalid reply: no text")
    try:
        reply = parse_reply(text)
    except ValueError as e:
        return _Verdict(False, True, _short(str(e)))
    try:
        count = write_evidence(interaction, captured, reply)
    except Exception as e:
        return _unexpected(e, interaction.name)
    return _Verdict(True, True, "", operations=count)


def _from_error(error: Exception, label: str, timeout: float | None) -> _Verdict:
    """The verdict for an API call (or batch) for `label` that raised `error`."""
    if isinstance(error, anthropic.APIStatusError):
        text = _api_message(error)
        if _out_of_credits(error.type, text):
            return _Verdict(False, False, "out of API credits", CREDIT_WAIT)
        if isinstance(error, anthropic.RateLimitError):
            return _Verdict(False, False, "rate limited", _retry_after(error.response.headers.get("retry-after")))
        return _Verdict(False, error.status_code in (400, 413), f"api {error.status_code}: {_short(text)}")
    if isinstance(error, anthropic.APITimeoutError):
        return _Verdict(False, False, f"timed out after {timeout:g}s" if timeout is not None else "timed out")
    if isinstance(error, anthropic.APIConnectionError):
        return _Verdict(False, False, f"connection error: {_short(error.message)}")
    return _unexpected(error, label)


def _from_batch_error(error_type: str, message: str) -> _Verdict:
    """The verdict for a batch request whose result errored with `error_type`."""
    if _out_of_credits(error_type, message):
        return _Verdict(False, False, "out of API credits", CREDIT_WAIT)
    if error_type == "rate_limit_error":
        return _Verdict(False, False, "rate limited")
    return _Verdict(False, error_type == "invalid_request_error", f"{error_type}: {_short(message)}")


def _out_of_credits(error_type: str | None, message: str) -> bool:
    return error_type == "billing_error" or "credit balance is too low" in message.lower()


def _unexpected(error: Exception, label: str) -> _Verdict:
    detail = _short(f"{type(error).__name__}: {error}")
    log.warning("observing %s failed: %s", label, detail, exc_info=error)
    return _Verdict(False, False, detail)


def _api_message(error: anthropic.APIStatusError) -> str:
    """The API's own message (`error.message` in the body), else the SDK's."""
    body = error.body
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
    return error.message


def _retry_after(value: str | None) -> float | None:
    """A `retry-after` header in seconds; None if absent or not a number of seconds."""
    try:
        seconds = float(value) if value is not None else math.nan
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _cost(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int, *, halved: bool) -> float | None:
    """Dollars for a call by `PRICES`; None for a model without a price."""
    if model not in PRICES:
        return None
    price_in, price_out, price_cache_read = PRICES[model]
    cost = (input_tokens * price_in + output_tokens * price_out + cache_read_tokens * price_cache_read) / 1e6
    return cost / 2 if halved else cost


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _append(path: Path, record: dict) -> OSError | None:
    """Append `record` as one JSON line; return the error instead of raising it.

    Losing a log line must not fail a call whose files were already written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        return e
    return None
