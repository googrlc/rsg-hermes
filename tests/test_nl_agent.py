"""Tests for the NL agent module."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.agent.nl_agent import (
    _exec_report,
    ask,
)
from hermes_core.dispatch import DispatchResult


class AskTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": ""})
    def test_ask_no_api_key(self) -> None:
        result = ask("find Acme")
        self.assertFalse(result.ok)
        self.assertIn("API key not configured", result.message)


def _tool_call(name, args=None):
    tc = MagicMock()
    tc.id = "call_1"
    tc.function.name = name
    tc.function.arguments = json.dumps(args or {})
    return tc


def _client_that_calls(name, args=None):
    """An LLM client that asks for one tool, then answers in prose."""
    first = MagicMock()
    first.choices[0].message.tool_calls = [_tool_call(name, args)]
    first.choices[0].message.content = None
    second = MagicMock()
    second.choices[0].message.tool_calls = None
    second.choices[0].message.content = "done"

    client = MagicMock()
    client.chat.completions.create.side_effect = [first, second]
    return client


class DisabledToolTests(unittest.TestCase):
    """The tool-dispatch loop referenced `disabled` without defining it, so every
    tool-calling question raised NameError in production. Nothing here called
    ask() with a tool call, which is why it shipped. These cover both halves of
    the capability and the crash itself."""

    def _run(self, env, tool="list_renewals"):
        client = _client_that_calls(tool)
        executed = DispatchResult(True, "EXECUTED")
        with patch.dict("os.environ", env, clear=False), \
             patch("hermes_core.llm_client.get_client", return_value=client), \
             patch("hermes_core.llm_client.resolve_model", return_value="gpt-x"), \
             patch("hermes.agent.nl_agent._EXECUTORS", {tool: lambda *a, **kw: executed}):
            return ask("anything"), client

    def test_a_tool_call_does_not_raise_when_nothing_is_disabled(self) -> None:
        result, client = self._run({"HERMES_DISABLED_TOOLS": ""})
        self.assertTrue(result.ok, result.message)

    def test_a_disabled_tool_is_refused_rather_than_executed(self) -> None:
        result, client = self._run({"HERMES_DISABLED_TOOLS": "list_renewals"})
        self.assertTrue(result.ok, result.message)
        # The refusal goes back as the tool result on the second round trip.
        second_call = client.chat.completions.create.call_args_list[1]
        blob = json.dumps(second_call.kwargs.get("messages", []), default=str)
        self.assertIn("not available on this instance", blob)
        self.assertNotIn("EXECUTED", blob)

    def test_a_disabled_tool_is_not_advertised(self) -> None:
        _, client = self._run({"HERMES_DISABLED_TOOLS": "list_renewals"})
        first_call = client.chat.completions.create.call_args_list[0]
        names = [t["function"]["name"] for t in first_call.kwargs.get("tools", [])]
        self.assertNotIn("list_renewals", names)
        self.assertTrue(names, "the rest of the toolset must survive the filter")


if __name__ == "__main__":
    unittest.main()
