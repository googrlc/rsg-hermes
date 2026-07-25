"""Tests for the NL agent module."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.core.nl_agent import (
    _exec_report,
    ask,
)
from hermes.core.dispatcher import DispatchResult


class AskTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": ""})
    def test_ask_no_api_key(self) -> None:
        result = ask("find Acme")
        self.assertFalse(result.ok)
        self.assertIn("API key not configured", result.message)


if __name__ == "__main__":
    unittest.main()
