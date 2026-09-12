"""LLM access. One interface, two implementations.

AnthropicLLM   real calls through the official SDK, structured JSON output, on-disk cache.
MockLLM        offline heuristic engine used by the tests and by --mock. It answers the same
               tasks with keyword and regex rules. It is not a model; do not judge extraction
               quality by it. It exists so the whole pipeline can be exercised without a key.

Every call goes through complete_json(task, model, system, user, schema) and returns
(payload, Usage). Pipeline stages never import anthropic directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from .schemas import Usage

log = logging.getLogger(__name__)


class LLM(Protocol):
    mocked: bool

    def complete_json(self, *, task: str, model: str, system: str, user: str,
                      schema: dict[str, Any], max_tokens: int = 16000) -> tuple[dict[str, Any], Usage]: ...


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class AnthropicLLM:
    """Real client. Reads ANTHROPIC_API_KEY from the environment (loaded from .env by settings)."""

    mocked = False

    def __init__(self, api_key: str | None = None, cache_dir: Path | None = None, use_cache: bool = True):
        import anthropic  # imported lazily so the mock path never needs the SDK configured
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._cache_dir = cache_dir if use_cache else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, model: str, system: str, user: str, schema: dict[str, Any]) -> str:
        h = hashlib.sha256()
        h.update(json.dumps([model, system, user, schema], sort_keys=True).encode("utf-8"))
        return h.hexdigest()

    def complete_json(self, *, task: str, model: str, system: str, user: str,
                      schema: dict[str, Any], max_tokens: int = 16000) -> tuple[dict[str, Any], Usage]:
        key = self._cache_key(model, system, user, schema)
        if self._cache_dir:
            hit = self._cache_dir / f"{key}.json"
            if hit.exists():
                cached = json.loads(hit.read_text("utf-8"))
                return cached["payload"], Usage(model=model, task=task, input_tokens=0, output_tokens=0)
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model refused task {task}: {getattr(response, 'stop_details', None)}")
        text = "".join(b.text for b in response.content if b.type == "text")
        payload = json.loads(text)
        usage = Usage(model=model, task=task,
                      input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
        if self._cache_dir:
            (self._cache_dir / f"{key}.json").write_text(json.dumps({"payload": payload}), "utf-8")
        return payload, usage


class MockLLM:
    """Offline stand-in. Dispatches on task name to heuristic handlers in mock_handlers.py."""

    mocked = True

    def __init__(self):
        from . import mock_handlers
        self._handlers = mock_handlers.HANDLERS

    def complete_json(self, *, task: str, model: str, system: str, user: str,
                      schema: dict[str, Any], max_tokens: int = 16000) -> tuple[dict[str, Any], Usage]:
        handler = self._handlers.get(task)
        if handler is None:
            raise KeyError(f"MockLLM has no handler for task {task!r}")
        payload = handler(user)
        usage = Usage(model=model, task=task, input_tokens=_approx_tokens(system + user),
                      output_tokens=_approx_tokens(json.dumps(payload)), mocked=True)
        return payload, usage


def make_llm(settings) -> LLM:
    if settings.llm_mode == "mock":
        return MockLLM()
    if not settings.api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example) or run with --mock.")
    return AnthropicLLM(api_key=settings.api_key, cache_dir=settings.cache_dir)


def usage_cost(usages: list[Usage], pricing: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Aggregate tokens per model and price them at the configured list rates."""
    per_model: dict[str, dict[str, float]] = {}
    for u in usages:
        row = per_model.setdefault(u.model, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "usd": 0.0})
        row["input_tokens"] += u.input_tokens
        row["output_tokens"] += u.output_tokens
        row["calls"] += 1
    total = 0.0
    for model, row in per_model.items():
        p = pricing.get(model, {"input": 0.0, "output": 0.0})
        row["usd"] = row["input_tokens"] / 1e6 * p["input"] + row["output_tokens"] / 1e6 * p["output"]
        total += row["usd"]
    return {"per_model": per_model, "total_usd": round(total, 4), "mocked": any(u.mocked for u in usages)}
