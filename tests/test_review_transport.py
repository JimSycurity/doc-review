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


def test_two_decimal_rounding_is_accepted_but_real_mass_errors_are_not() -> None:
    import pytest

    from docreview.documentation_decisions import read_answer

    question: dict[str, Json] = {"type": "score", "criteria": ["Poor", "Mixed", "Good"]}
    rounded: dict[str, Json] = {
        "type": "score",
        "score": 1.65,
        "confidence": 0.6,
        "probabilities": {"0": 0.01, "1": 0.31, "2": 0.67},
    }
    assert read_answer(rounded, question).value == 1.65
    wrong: dict[str, Json] = {
        **rounded,
        "probabilities": {"0": 0.0, "1": 0.3, "2": 0.6},
    }
    with pytest.raises(ValueError, match="sum to one"):
        read_answer(wrong, question)
