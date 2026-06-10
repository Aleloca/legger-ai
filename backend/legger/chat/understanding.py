"""Query understanding via Haiku forced tool use (Task E2).

Complements (does NOT replace) the deterministic extractor in
:mod:`legger.retrieval.fastpath`: a small, cheap Haiku call rewrites the
current user message into a standalone retrieval query (resolving anaphora
against the conversation: "e il comma successivo?" -> explicit article/comma),
spots normative reference *hints* and temporal references. The hints are
free-form model output — E5 crosschecks them with
:func:`~legger.retrieval.fastpath.extract_refs` before trusting them.

Robustness contract: the chat must NEVER break because of query
understanding. ANY failure — API down, timeout, missing tool_use block,
schema-invalid tool input — degrades to a :class:`QueryAnalysis` that carries
the last user message verbatim as the query (exactly what C5 retrieves on
today), with a warning in the log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from legger.chat.generate import last_user_message

if TYPE_CHECKING:
    from anthropic import Anthropic

logger = logging.getLogger(__name__)

#: Current Claude Haiku id, verified against the models overview on
#: platform.claude.com (2026-06-11): "claude-haiku-4-5" is the Claude API
#: alias (full id claude-haiku-4-5-20251001) — do NOT append a date.
MODEL_HAIKU = "claude-haiku-4-5"

MAX_TOKENS = 500
TEMPERATURE = 0.0
#: Wall-clock budget for the understanding call: it sits on the chat critical
#: path. With MAX_RETRIES = 0 the worst case is one attempt, ~10s; retrying
#: would double that, and the verbatim fallback is cheap and benign anyway.
TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 0
#: Conversation turns (before the current user message) shown to the model.
HISTORY_TURNS = 6
#: Per-message cap (chars) on history shown to the model: a deterministic
#: bound on prompt size, hence on cost and latency.
HISTORY_CHAR_LIMIT = 1500


class RefHint(BaseModel):
    """A normative reference the model spotted — a HINT, not a fact.

    ``act`` is a free-form descriptor in the user's own words ("codice
    civile", "statuto dei lavoratori", "d.lgs. 81/2008"): E5 crosschecks it
    against :func:`~legger.retrieval.fastpath.extract_refs` and the corpus
    before resolving; it is never trusted blindly.
    """

    act: str | None = None
    article: str | None = None
    comma: str | None = None


class QueryAnalysis(BaseModel):
    """Typed output of :func:`understand_query` (and of the fallback)."""

    rewritten_query: str
    explicit_refs: list[RefHint] = Field(default_factory=list)
    temporal_reference: str | None = None
    wants_historical: bool = False


#: Forced tool: the JSON schema mirrors :class:`QueryAnalysis` so the tool
#: input validates straight into the model. Optional fields stay optional in
#: the schema — Haiku omits them instead of inventing nulls.
ANALYZE_QUERY_TOOL = {
    "name": "analyze_query",
    "description": (
        "Registra l'analisi del messaggio corrente dell'utente ai fini del "
        "retrieval normativo. Va chiamato sempre, una sola volta."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rewritten_query": {
                "type": "string",
                "description": (
                    "Il messaggio corrente riscritto come query di ricerca "
                    "autonoma e autosufficiente, in italiano: i riferimenti "
                    "anaforici al contesto ('quell'articolo', 'e il comma "
                    "successivo?') sono risolti in riferimenti espliciti. Se "
                    "il messaggio è già autosufficiente, riportalo quasi "
                    "invariato. NON è una risposta alla domanda."
                ),
            },
            "explicit_refs": {
                "type": "array",
                "description": (
                    "Riferimenti normativi espliciti menzionati o implicati "
                    "dal messaggio corrente (dopo la risoluzione del "
                    "contesto). Vuoto se non ce ne sono."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "act": {
                            "type": "string",
                            "description": (
                                "L'atto, come lo chiama l'utente: 'codice "
                                "civile', 'd.lgs. 81/2008', 'statuto dei "
                                "lavoratori'."
                            ),
                        },
                        "article": {
                            "type": "string",
                            "description": "Numero dell'articolo, es. '2051', '18', '2-bis'.",
                        },
                        "comma": {
                            "type": "string",
                            "description": "Numero del comma, se citato.",
                        },
                    },
                },
            },
            "temporal_reference": {
                "type": "string",
                "description": (
                    "Data ISO (YYYY-MM-DD, o YYYY se è noto solo l'anno) a "
                    "cui l'utente ancora la domanda. Omettere se assente."
                ),
            },
            "wants_historical": {
                "type": "boolean",
                "description": (
                    "true se l'utente chiede una versione passata/storica "
                    "della norma (com'era prima di una riforma, in un certo "
                    "anno), false se chiede il testo vigente."
                ),
            },
        },
        "required": ["rewritten_query"],
    },
}

SYSTEM_PROMPT = (
    "Sei il modulo di query understanding di un assistente sulla normativa "
    "italiana. Ricevi lo storico recente della conversazione e il messaggio "
    "corrente dell'utente.\n"
    "Il tuo UNICO compito è chiamare lo strumento analyze_query per:\n"
    "1. riscrivere il messaggio corrente come query di ricerca autonoma, "
    "risolvendo i riferimenti anaforici al contesto ('quell'articolo', 'e il "
    "comma dopo?', 'lo stesso decreto');\n"
    "2. estrarre i riferimenti normativi espliciti (atto, articolo, comma);\n"
    "3. estrarre l'eventuale riferimento temporale e se la domanda riguarda "
    "una versione storica della norma.\n"
    "NON rispondere alla domanda dell'utente e NON aggiungere informazioni "
    "giuridiche: produci solo l'analisi tramite lo strumento."
)

_ROLE_LABELS = {"user": "utente", "assistant": "assistente"}


def _format_input(messages: list[dict]) -> str:
    """Compact transcript: last :data:`HISTORY_TURNS` turns + current message.

    The current message is the LAST user turn; anything after it (would be a
    caller bug) is ignored. History before it is truncated to the most recent
    ``HISTORY_TURNS`` turns — anaphora in legal chat point at the immediately
    preceding exchange, and a short prompt keeps the call cheap and fast.
    Each history message is additionally clamped to ``HISTORY_CHAR_LIMIT``
    chars, bounding prompt size deterministically.

    Message ``content`` is expected to be a plain string (the chat pipeline
    guarantees it); non-string content is coerced via ``str()``.

    History and current message are wrapped in distinct XML-ish tags so the
    model can tell the operator framing apart from (spoofable) message text.
    """
    current_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    history = messages[:current_idx][-HISTORY_TURNS:]
    lines = [
        f"{_ROLE_LABELS.get(m['role'], m['role'])}: {str(m['content'])[:HISTORY_CHAR_LIMIT]}"
        for m in history
    ]
    transcript = "\n".join(lines) if lines else "(nessuno)"
    return (
        "Storico della conversazione (turni più recenti):\n"
        f"<storico>\n{transcript}\n</storico>\n\n"
        "Messaggio corrente dell'utente:\n"
        f"<messaggio_corrente>\n{messages[current_idx]['content']!s}\n</messaggio_corrente>"
    )


def understand_query(messages: list[dict], *, anthropic_client: Anthropic) -> QueryAnalysis:
    """Analyze the current user message for retrieval; NEVER raises for QU.

    One Haiku call with tool use *forzato* on ``analyze_query``; the tool
    input is validated into :class:`QueryAnalysis`. Every failure mode (API
    error, timeout, no tool_use block, schema-invalid input) logs a warning
    and falls back to the last user message verbatim — the pipeline then
    behaves exactly like the pre-E2 chat.

    Sync/blocking call: it blocks for up to ~``TIMEOUT_SECONDS``. In async
    contexts (F2 SSE streaming) run it via a threadpool (e.g.
    ``anyio.to_thread.run_sync`` / ``loop.run_in_executor``) or call it from
    a sync route.

    Message ``content`` is expected to be a plain string; non-string content
    is coerced via ``str()`` (see :func:`_format_input`).
    """
    fallback = QueryAnalysis(rewritten_query=str(last_user_message(messages)))
    try:
        response = anthropic_client.with_options(
            timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        ).messages.create(
            model=MODEL_HAIKU,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=SYSTEM_PROMPT,
            tools=[ANALYZE_QUERY_TOOL],
            tool_choice={"type": "tool", "name": "analyze_query"},
            messages=[{"role": "user", "content": _format_input(messages)}],
        )
        block = next(
            (
                b
                for b in response.content
                if getattr(b, "type", None) == "tool_use" and b.name == "analyze_query"
            ),
            None,
        )
        if block is None:
            raise ValueError("no analyze_query tool_use block in the response")
        return QueryAnalysis.model_validate(block.input)
    except Exception:
        logger.warning(
            "query understanding failed; falling back to the verbatim user message",
            exc_info=True,
        )
        return fallback
