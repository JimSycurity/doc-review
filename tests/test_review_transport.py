"""Reject oversize disclosures before opening a network connection."""

from unittest.mock import patch

import pytest

from docreview.documentation_decisions import Json, decide, encoded_request


def test_utf8_budget_uses_serialized_bytes_not_character_count() -> None:
    payload: dict[str, Json] = {
        "model": "typesafe/jev-1.14",
        "state": {"document": "é" * 16000},
        "questions": {},
    }
    assert len(encoded_request(payload)) > 32000
    with patch(
        "docreview.documentation_decisions.http.client.HTTPSConnection"
    ) as connection:
        with pytest.raises(ValueError, match="32000-byte"):
            decide(payload, "synthetic-key")
        connection.assert_not_called()


def test_actual_credential_cannot_enter_request_state() -> None:
    with patch(
        "docreview.documentation_decisions.http.client.HTTPSConnection"
    ) as connection:
        with pytest.raises(ValueError, match="authentication material"):
            decide(
                {"state": {"text": "synthetic-secret-value"}}, "synthetic-secret-value"
            )
        connection.assert_not_called()
