"""LLM-agnostic router — the vendor-swappable layer.

Reads ``hermes/data/models.yaml`` and routes each call by task class to a
primary model, falling back to a different provider on failure, and
escalating to a judgment model when the primary returns low confidence.
This is the piece that keeps RSG from being evicted by any single LLM
vendor: swap providers/models by editing YAML, not code.

All providers expose an OpenAI-compatible ``/chat/completions`` endpoint, so
the existing ``openai`` SDK drives every call by swapping base_url + api_key.
The SDK is imported lazily so this module loads without it installed.

Guarantees (from 00b_model_selection_guide.md):
  * cost-first routing per task class             - §2
  * primary -> fallback (different provider)       - §5
  * escalation ladder: default -> Opus -> human    - §3
  * PII never routed to non-US providers           - §6
  * per-agent monthly cost cap with 80% alert      - §7
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_MODELS_YAML = Path(__file__).resolve().parent.parent / "data" / "models.yaml"


class LLMRouterError(Exception):
    """Base router error."""


class PIIPolicyError(LLMRouterError):
    """Raised when a PII task would route to a non-US provider."""


class BudgetExceeded(LLMRouterError):
    """Raised when an agent has burned its monthly cap."""


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    task_class: str
    confidence: float | None = None
    cost_estimate: float = 0.0
    fell_back: bool = False
    escalated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.content)


# ---------------------------------------------------------------------------
# Cost ledger — in-process, per agent, with 80% alert + hard cap.
# ---------------------------------------------------------------------------


class CostLedger:
    """Tracks cumulative monthly spend per agent against models.yaml caps."""

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}

    def add(self, agent: str, dollars: float) -> float:
        total = self._spent.get(agent, 0.0) + dollars
        self._spent[agent] = total
        return total

    def spent(self, agent: str) -> float:
        return self._spent.get(agent, 0.0)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class LLMRouter:
    """Task-class -> model router with fallback, escalation, PII + cost gates."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        config: dict[str, Any] | None = None,
        client_factory: Callable[[str, str], Any] | None = None,
        ledger: CostLedger | None = None,
    ) -> None:
        if config is not None:
            self.cfg: dict[str, Any] = config
        else:
            import yaml  # lazy: only needed when reading models.yaml from disk

            path = Path(config_path) if config_path else _MODELS_YAML
            with open(path, "r", encoding="utf-8") as fh:
                self.cfg = yaml.safe_load(fh) or {}
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self.ledger = ledger or CostLedger()

    # -- config accessors ---------------------------------------------------

    def providers(self) -> dict[str, Any]:
        return self.cfg.get("providers", {})

    def models(self) -> dict[str, Any]:
        return self.cfg.get("models", {})

    def task_classes(self) -> dict[str, Any]:
        return self.cfg.get("task_classes", {})

    def budgets(self) -> dict[str, Any]:
        return self.cfg.get("agent_budgets", {})

    def resolve_task_class(self, task_class: str) -> dict[str, Any]:
        tc = self.task_classes().get(task_class)
        if not tc:
            raise LLMRouterError(f"unknown task_class: {task_class}")
        return tc

    # -- client construction ------------------------------------------------

    def _make_client(self, provider: str) -> Any:
        """Build (and cache) an OpenAI-compatible client for a provider."""
        if provider in self._clients:
            return self._clients[provider]
        prov = self.providers().get(provider)
        if not prov:
            raise LLMRouterError(f"unknown provider: {provider}")
        base_url = prov["base_url"]
        api_key = os.environ.get(prov["api_key_env"], "") or prov.get("api_key_value", "")
        if self._client_factory:
            # Test/fake path: the factory owns auth, so an absent env key is OK.
            client = self._client_factory(base_url, api_key)
        else:
            if not api_key:
                raise LLMRouterError(
                    f"provider {provider} missing API key (env {prov['api_key_env']})"
                )
            from openai import OpenAI  # lazy: module loads without the SDK

            client = OpenAI(base_url=base_url, api_key=api_key)
        self._clients[provider] = client
        return client

    def _provider_for_model(self, model: str) -> str:
        spec = self.models().get(model)
        if not spec:
            raise LLMRouterError(f"unknown model: {model}")
        return spec["provider"]

    # -- PII + cost gates ---------------------------------------------------

    def _check_pii(self, model: str, pii: bool) -> None:
        if not pii:
            return
        spec = self.models().get(model, {})
        if not spec.get("pii_safe", False):
            raise PIIPolicyError(
                f"PII task routed to non-US model {model} (provider "
                f"{spec.get('provider')}); refusing per data-residency policy."
            )

    def _check_budget(self, agent: str) -> None:
        if not agent:
            return
        cap = float(self.budgets().get(agent, 0) or 0)
        if cap <= 0:
            return
        spent = self.ledger.spent(agent)
        if spent >= cap:
            raise BudgetExceeded(f"{agent} hit monthly cap ${cap:.2f} (spent ${spent:.2f})")
        if spent >= 0.8 * cap:
            log.warning(
                "%s at %.0f%% of monthly LLM cap ($%.2f / $%.2f)",
                agent,
                100 * spent / cap,
                spent,
                cap,
            )

    # -- cost estimate ------------------------------------------------------

    def _estimate_cost(self, model: str, in_tokens: int, out_tokens: int) -> float:
        spec = self.models().get(model, {})
        cost = (in_tokens / 1_000_000) * spec.get("cost_in", 0.0)
        cost += (out_tokens / 1_000_000) * spec.get("cost_out", 0.0)
        return round(cost, 6)

    # -- core call ----------------------------------------------------------

    def _call(self, model: str, messages: list[dict[str, str]], tc: dict[str, Any]) -> LLMResponse:
        provider = self._provider_for_model(model)
        client = self._make_client(provider)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": tc.get("temperature", 0.2),
            "max_tokens": tc.get("max_tokens", 1500),
        }
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        return LLMResponse(
            content=content,
            model=model,
            provider=provider,
            task_class=tc.get("_task_class", ""),
            cost_estimate=self._estimate_cost(model, in_tok, out_tok),
            raw={"prompt_tokens": in_tok, "completion_tokens": out_tok},
        )

    # -- public API ---------------------------------------------------------

    def complete(
        self,
        task_class: str,
        messages: list[dict[str, str]],
        *,
        agent: str = "",
        pii: bool | None = None,
    ) -> LLMResponse:
        """Route a chat completion by task class with fallback + gates."""
        tc = self.resolve_task_class(task_class)
        tc["_task_class"] = task_class
        is_pii = bool(tc.get("pii", False)) if pii is None else bool(pii)

        order = [tc["default"]]
        if tc.get("fallback") and tc["fallback"] != tc["default"]:
            order.append(tc["fallback"])

        self._check_budget(agent)
        last_err: Exception | None = None
        for idx, model in enumerate(order):
            try:
                self._check_pii(model, is_pii)
            except PIIPolicyError as exc:
                last_err = exc
                if idx == len(order) - 1:
                    # No safe provider remains for this PII task — fail loudly.
                    raise
                log.warning("%s — trying next provider", exc)
                continue
            try:
                resp = self._call(model, messages, tc)
                resp.fell_back = idx > 0
                if agent:
                    self.ledger.add(agent, resp.cost_estimate)
                    self._check_budget(agent)
                return resp
            except PIIPolicyError:
                raise
            except Exception as exc:
                last_err = exc
                log.warning("LLM primary %s failed: %s — falling back", model, exc)
                continue

        raise LLMRouterError(f"all providers failed for task_class {task_class}: {last_err}")

    def complete_with_confidence(
        self,
        task_class: str,
        messages: list[dict[str, str]],
        *,
        agent: str = "",
        pii: bool | None = None,
    ) -> LLMResponse:
        """Complete and parse a JSON ``{answer, confidence}`` envelope.

        If the primary model's confidence is below the task-class threshold,
        escalate the judgment step only to the escalation model (§3 ladder).
        """
        tc = self.resolve_task_class(task_class)
        is_pii = bool(tc.get("pii", False)) if pii is None else bool(pii)
        threshold = float(tc.get("confidence_threshold", 0.75))

        resp = self.complete(task_class, messages, agent=agent, pii=is_pii)
        parsed = _parse_confidence(resp.content)
        resp.confidence = parsed.get("confidence")

        escalation_model = tc.get("escalation")
        if escalation_model and (resp.confidence is None or resp.confidence < threshold):
            try:
                self._check_pii(escalation_model, is_pii)
                esc = self._call(escalation_model, messages, tc)
                esc.escalated = True
                esc.task_class = task_class
                if agent:
                    self.ledger.add(agent, esc.cost_estimate)
                parsed = _parse_confidence(esc.content)
                esc.confidence = parsed.get("confidence")
                esc.content = parsed.get("answer", esc.content)
                return esc
            except Exception as exc:
                log.warning("escalation to %s failed: %s", escalation_model, exc)
                # Fall through to return the primary response.

        if parsed.get("answer") is not None:
            resp.content = parsed["answer"]
        return resp

    def escalate_to_human(self, agent: str, context: str) -> str:
        """Terminal rung of the ladder — return a human-escalation marker."""
        log.warning("LLM escalation exhausted for %s: %s", agent, context)
        return f"[HUMAN_ESCALATION] {agent}: {context}"


def _parse_confidence(content: str) -> dict[str, Any]:
    """Best-effort parse of a JSON ``{"answer","confidence"}`` envelope."""
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a trailing explanation after the JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    if "answer" in obj:
        out["answer"] = str(obj["answer"])
    if "confidence" in obj:
        try:
            out["confidence"] = float(obj["confidence"])
        except (TypeError, ValueError):
            pass
    return out
