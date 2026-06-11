"""Unit tests for legger.chat.models_catalog — the per-model API constraints.

Pins down the two constraints that would otherwise 400 at the Anthropic API:
no ``output_config.effort`` on haiku-4-5, no ``temperature`` on opus-4-8 —
plus the catalog/payload shape the rest of the stack derives from.
"""

import pytest

from legger.chat.models_catalog import (
    ALLOWED_ANSWER_MODELS,
    ALLOWED_QU_MODELS,
    DEFAULT_ANSWER_MODEL,
    DEFAULT_QU_MODEL,
    EFFORT_LEVELS,
    build_model_kwargs,
    catalog_payload,
    effective_effort,
)

# --- catalog shape ---------------------------------------------------------------


def test_defaults_are_in_their_allowlists() -> None:
    assert DEFAULT_ANSWER_MODEL == "claude-sonnet-4-6"
    assert DEFAULT_QU_MODEL == "claude-haiku-4-5"
    assert DEFAULT_ANSWER_MODEL in ALLOWED_ANSWER_MODELS
    assert DEFAULT_QU_MODEL in ALLOWED_QU_MODELS


def test_allowlists_content() -> None:
    assert set(ALLOWED_ANSWER_MODELS) == {
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-8",
    }
    assert set(ALLOWED_QU_MODELS) == {"claude-haiku-4-5", "claude-sonnet-4-6"}


def test_effort_levels() -> None:
    assert EFFORT_LEVELS == ("low", "medium", "high", "max")


def test_every_entry_has_the_full_info_shape() -> None:
    for info in {**ALLOWED_ANSWER_MODELS, **ALLOWED_QU_MODELS}.values():
        assert set(info) == {
            "label",
            "input_usd_mtok",
            "output_usd_mtok",
            "supports_effort",
            "supports_temperature",
        }


# --- build_model_kwargs: the API-constraint rules ---------------------------------


def test_haiku_never_gets_output_config_even_with_effort() -> None:
    # output_config.effort on haiku-4-5 is a 400 at the API: requested effort
    # is silently dropped, temperature stays.
    kwargs = build_model_kwargs("claude-haiku-4-5", "max")
    assert kwargs == {"model": "claude-haiku-4-5", "temperature": 0.2}


def test_opus_48_never_gets_temperature() -> None:
    # temperature on opus-4-8 is a 400 at the API: it must be omitted.
    kwargs = build_model_kwargs("claude-opus-4-8", "high")
    assert kwargs == {"model": "claude-opus-4-8", "output_config": {"effort": "high"}}
    assert "temperature" not in build_model_kwargs("claude-opus-4-8", None)


def test_sonnet_gets_both_when_effort_given() -> None:
    kwargs = build_model_kwargs("claude-sonnet-4-6", "low")
    assert kwargs == {
        "model": "claude-sonnet-4-6",
        "temperature": 0.2,
        "output_config": {"effort": "low"},
    }


def test_opus_46_supports_both() -> None:
    kwargs = build_model_kwargs("claude-opus-4-6", "medium")
    assert kwargs == {
        "model": "claude-opus-4-6",
        "temperature": 0.2,
        "output_config": {"effort": "medium"},
    }


def test_no_effort_means_no_output_config() -> None:
    # Omitting effort = API default (high); never send an explicit field.
    for model in ("claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"):
        assert "output_config" not in build_model_kwargs(model, None)


def test_temperature_is_parametrizable() -> None:
    # Query understanding runs at temperature 0.0 — the helper passes it
    # through (where supported) instead of hardcoding 0.2.
    kwargs = build_model_kwargs("claude-haiku-4-5", None, temperature=0.0)
    assert kwargs == {"model": "claude-haiku-4-5", "temperature": 0.0}


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="not in the catalog"):
        build_model_kwargs("claude-fantasia-9", None)


def test_unknown_effort_raises() -> None:
    with pytest.raises(ValueError, match="effort"):
        build_model_kwargs("claude-sonnet-4-6", "xhigh")


# --- effective_effort --------------------------------------------------------------


def test_effective_effort_none_for_unsupporting_model() -> None:
    assert effective_effort("claude-haiku-4-5", "max") is None
    assert effective_effort("claude-sonnet-4-6", "max") == "max"
    assert effective_effort("claude-sonnet-4-6", None) is None


# --- catalog_payload (GET /chat/models body) ---------------------------------------


def test_catalog_payload_shape() -> None:
    payload = catalog_payload()
    assert set(payload) == {"answer", "qu", "effort_levels"}
    assert payload["effort_levels"] == ["low", "medium", "high", "max"]
    assert payload["answer"]["default"] == DEFAULT_ANSWER_MODEL
    assert payload["qu"]["default"] == DEFAULT_QU_MODEL
    answer_ids = [m["id"] for m in payload["answer"]["models"]]
    assert answer_ids == list(ALLOWED_ANSWER_MODELS)
    for entry in payload["answer"]["models"] + payload["qu"]["models"]:
        assert set(entry) == {
            "id",
            "label",
            "input_usd_mtok",
            "output_usd_mtok",
            "supports_effort",
        }
    haiku = next(m for m in payload["answer"]["models"] if m["id"] == "claude-haiku-4-5")
    assert haiku["supports_effort"] is False
    assert haiku["input_usd_mtok"] == 1.0
    assert haiku["output_usd_mtok"] == 5.0
