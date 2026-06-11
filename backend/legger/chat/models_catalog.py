"""Model/effort catalog for the beta-testing chat configuration.

THE single source of truth for which Claude models a beta tester may pick
(per role: answer generation vs query understanding), what each costs, and
which request knobs each supports. Everything else derives from here:

- the ``ChatConfig`` allowlist validation in :mod:`legger.api.chat` (422 on
  anything outside these dicts — client strings NEVER reach the Anthropic
  API unvalidated);
- ``GET /chat/models`` (the frontend renders its selects from that payload,
  no duplicated list in TypeScript);
- :func:`build_model_kwargs`, the one place that knows the per-model API
  constraints below.

API constraints encoded here (verified against platform.claude.com,
2026-06-10 — see the per-model fields):

- ``output_config: {"effort": ...}`` is supported ONLY on sonnet-4-6 /
  opus-4-6 / opus-4-8; sending it to haiku-4-5 is a 400. Omitting it means
  the model default (high).
- ``temperature`` is REMOVED on opus-4-8 (400 if sent); still accepted on
  haiku-4-5, sonnet-4-6 and opus-4-6.

Prices are USD per million tokens (input/output), shown in the settings UI
so testers can weigh cost while comparing models.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ModelInfo(TypedDict):
    """Catalog entry for one selectable model."""

    label: str
    input_usd_mtok: float
    output_usd_mtok: float
    supports_effort: bool
    supports_temperature: bool


#: The four effort levels of ``output_config.effort``. Omitting the field
#: (effort=None everywhere in this codebase) means the API default (high).
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")

_HAIKU_4_5: ModelInfo = {
    "label": "Haiku 4.5",
    "input_usd_mtok": 1.0,
    "output_usd_mtok": 5.0,
    "supports_effort": False,  # output_config.effort -> 400 on haiku-4-5
    "supports_temperature": True,
}
_SONNET_4_6: ModelInfo = {
    "label": "Sonnet 4.6",
    "input_usd_mtok": 3.0,
    "output_usd_mtok": 15.0,
    "supports_effort": True,
    "supports_temperature": True,
}
_OPUS_4_6: ModelInfo = {
    "label": "Opus 4.6",
    "input_usd_mtok": 5.0,
    "output_usd_mtok": 25.0,
    "supports_effort": True,
    "supports_temperature": True,
}
_OPUS_4_8: ModelInfo = {
    "label": "Opus 4.8",
    "input_usd_mtok": 5.0,
    "output_usd_mtok": 25.0,
    "supports_effort": True,
    "supports_temperature": False,  # temperature REMOVED on opus-4-8 (400)
}

#: Models a tester may pick for ANSWER generation, in menu order.
ALLOWED_ANSWER_MODELS: dict[str, ModelInfo] = {
    "claude-haiku-4-5": _HAIKU_4_5,
    "claude-sonnet-4-6": _SONNET_4_6,
    "claude-opus-4-6": _OPUS_4_6,
    "claude-opus-4-8": _OPUS_4_8,
}

#: Models a tester may pick for QUERY UNDERSTANDING (cheap, on the hot path).
ALLOWED_QU_MODELS: dict[str, ModelInfo] = {
    "claude-haiku-4-5": _HAIKU_4_5,
    "claude-sonnet-4-6": _SONNET_4_6,
}

#: Today's production defaults (what runs when no config is sent).
DEFAULT_ANSWER_MODEL = "claude-sonnet-4-6"
DEFAULT_QU_MODEL = "claude-haiku-4-5"

#: Union of both allowlists, for kwargs assembly lookups.
_ALL_MODELS: dict[str, ModelInfo] = {**ALLOWED_ANSWER_MODELS, **ALLOWED_QU_MODELS}


def build_model_kwargs(
    model_id: str,
    effort: str | None,
    *,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """The per-model Anthropic kwargs fragment: model + sampling knobs.

    Returns ``{"model": ...}`` plus:

    - ``temperature`` (the given value) only when the model still accepts it
      (NOT opus-4-8 — sending it there is a 400);
    - ``output_config={"effort": ...}`` only when *effort* is given AND the
      model supports it (NOT haiku-4-5 — sending it there is a 400). An
      unsupported effort is silently dropped rather than rejected: the
      request still degrades to the model's default behavior.

    Raises :class:`ValueError` for a model outside the catalog (defensive:
    request validation must have rejected it long before this point) and for
    an effort outside :data:`EFFORT_LEVELS`.
    """
    info = _ALL_MODELS.get(model_id)
    if info is None:
        raise ValueError(f"model {model_id!r} is not in the catalog")
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ValueError(f"effort {effort!r} is not one of {EFFORT_LEVELS}")
    kwargs: dict[str, Any] = {"model": model_id}
    if info["supports_temperature"]:
        kwargs["temperature"] = temperature
    if effort is not None and info["supports_effort"]:
        kwargs["output_config"] = {"effort": effort}
    return kwargs


def effective_effort(model_id: str, effort: str | None) -> str | None:
    """The effort that actually reaches the API for *model_id* (or None).

    ``None`` both when no effort was requested and when the model does not
    support the parameter (it is silently dropped — see
    :func:`build_model_kwargs`). Used for the transparency block on the
    ``done`` SSE event.
    """
    info = _ALL_MODELS.get(model_id)
    if info is None or not info["supports_effort"]:
        return None
    return effort


def catalog_payload() -> dict[str, Any]:
    """The ``GET /chat/models`` response body (frontend renders FROM this)."""

    def entries(models: dict[str, ModelInfo]) -> list[dict[str, Any]]:
        return [
            {
                "id": model_id,
                "label": info["label"],
                "input_usd_mtok": info["input_usd_mtok"],
                "output_usd_mtok": info["output_usd_mtok"],
                "supports_effort": info["supports_effort"],
            }
            for model_id, info in models.items()
        ]

    return {
        "answer": {"default": DEFAULT_ANSWER_MODEL, "models": entries(ALLOWED_ANSWER_MODELS)},
        "qu": {"default": DEFAULT_QU_MODEL, "models": entries(ALLOWED_QU_MODELS)},
        "effort_levels": list(EFFORT_LEVELS),
    }
