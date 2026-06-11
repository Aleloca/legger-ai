"""GET /acts/{act_ref}: the full text of one act, parsed (Task F1).

Backs the frontend split-view (G4): the chat cites ``[[act_ref|art.N]]``
markers, the split-view fetches this endpoint and scrolls to the article's
``anchor``. Postgres (``acts`` table) is the source of act *identity*
(title, act_type, vigenza, collection, file_path); the article text comes
from parsing the corpus file on demand.

Design notes
------------

- **DB lookup is a module-level function** (:func:`lookup_act`) so tests can
  stub it with ``monkeypatch.setattr`` instead of standing up Postgres — the
  query is a trivial primary-key SELECT, the interesting behavior is in the
  endpoint around it.
- **Parse cache**: :func:`_parse_cached` is an ``functools.lru_cache``
  (maxsize 16) keyed by ``(absolute file path, mtime_ns)``. Parsing is
  millisecond-scale for normal acts but the Codice civile (3282 articles,
  ~9 MB markdown) takes ~330 ms — and the Codici are exactly the acts the
  split-view opens most. The size is memory-driven: a parsed Codice-sized
  act is ~9 MB of Python objects, so 16 entries bound the cache at roughly
  150 MB worst case while still holding every hot Codice (there are nowhere
  near 64 of them). The mtime key self-invalidates when the corpus clone is
  updated (git checkout rewrites the file, bumping mtime); stale entries
  just age out of the LRU.
- **Row exists but file missing on disk** -> **503**, not 404: the act
  exists, the server is temporarily unable to render it (skew while the
  corpus clone is mid-update, or a misconfigured ``CORPUS_PATH``). A 404
  would tell the frontend the act_ref is wrong, which it is not. The same
  503 covers the file vanishing *between* the stat and the parse (the git
  pull race). The detail is generic — the on-disk path is logged, not
  leaked to the client.
- **Response size**: the Codice civile response is several MB of JSON.
  Accepted for now — the split-view loads an act once and scrolls within
  it. ``?article=N`` returns a window of the requested article ±1 neighbor
  (by document position) for clients that want a light payload instead.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from legger.corpus.parser import parse_act
from legger.db import acts as acts_table

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from legger.corpus.models import Act, Article

logger = logging.getLogger(__name__)

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def anchor_from_chunk_segment(seg: str) -> str:
    """URL-safe anchor for a B5 chunk-id article segment: ``art-1.2`` -> ``art-1-2``.

    THE slug rule for the article segment of the B5 chunk ids
    (``{act_ref}#art-{number}[.{occurrence}]#{i}``), in one place: lowercase,
    every non-alphanumeric run becomes a single hyphen. F3 maps a retrieved
    chunk id onto the split-view anchor through this function;
    :func:`article_anchor` builds the same anchors from the parse side.
    """
    return _SLUG_RE.sub("-", seg.lower()).strip("-")


def article_anchor(number: str, occurrence: int = 1) -> str:
    """URL-safe anchor id for an article, e.g. ``art-18``, ``art-613-bis``.

    Mirrors the article segment of the B5 chunk ids, including the
    ``.{occurrence}`` disambiguator for repeated numbers (approval decree +
    attached code both having an art. 1), so a chunk id maps onto an anchor
    mechanically: see :func:`anchor_from_chunk_segment`.
    """
    key = number if occurrence == 1 else f"{number}.{occurrence}"
    return anchor_from_chunk_segment(f"art-{key}")


class CommaOut(BaseModel):
    number: str | None
    text: str


class ArticleOut(BaseModel):
    number: str
    heading: str | None
    path: list[str]
    commi: list[CommaOut]
    anchor: str


class ActOut(BaseModel):
    act_ref: str
    title: str | None
    act_type: str
    vigenza: str
    collection: str
    articles: list[ArticleOut]


def lookup_act(engine: Engine, act_ref: str) -> dict | None:
    """The ``acts`` row for *act_ref* as a plain dict, or ``None``.

    Module-level on purpose: tests stub this function to avoid a Postgres
    dependency (see module docstring).
    """
    stmt = select(acts_table).where(acts_table.c.act_ref == act_ref)
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row is not None else None


# maxsize is memory-driven: a parsed Codice-sized act is ~9 MB, so 16 entries
# cap the cache at ~150 MB worst case — and there aren't 64 hot Codici anyway.
@lru_cache(maxsize=16)
def _parse_cached(file_path: str, mtime_ns: int) -> Act:
    """Parse *file_path*, memoized on (path, mtime) — see module docstring."""
    return parse_act(Path(file_path))


def _load_act(corpus_path: Path, file_path: str) -> Act:
    """Parse ``corpus_path / file_path`` through the LRU cache.

    Raises ``HTTPException(503)`` when the file is missing on disk (DB/corpus
    skew — documented choice, see module docstring). The OSError guard covers
    the parse too: the file can vanish *between* the stat and the open while
    a git pull rewrites the corpus clone.
    """
    full_path = corpus_path / file_path
    try:
        mtime_ns = full_path.stat().st_mtime_ns
        return _parse_cached(str(full_path), mtime_ns)
    except OSError:
        logger.warning("act file unavailable on disk: %s", full_path, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                "L'atto è censito ma il suo testo non è al momento "
                "disponibile (aggiornamento del corpus in corso?). Riprovare."
            ),
        ) from None


def _article_out(art: Article, occurrence: int) -> ArticleOut:
    """The response model for one parsed article."""
    return ArticleOut(
        number=art.number,
        heading=art.heading,
        path=art.path,
        commi=[CommaOut(number=c.number, text=c.text) for c in art.commi],
        anchor=article_anchor(art.number, occurrence),
    )


def _window_slice(numbers: list[str], number: str) -> slice:
    """Slice covering article *number* plus its ±1 positional neighbors.

    Position-based, not number-based: "the previous article" of art. 2052 is
    whatever precedes it in the document (2051), without arithmetic on
    suffixed numbers (613-bis). First occurrence wins for duplicated numbers.
    """
    try:
        i = numbers.index(number)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Articolo '{number}' non presente nell'atto."
        ) from None
    return slice(max(0, i - 1), i + 2)


@router.get("/acts/{act_ref}", response_model=ActOut)
def get_act(
    act_ref: str,
    request: Request,
    article: Annotated[
        str | None,
        Query(
            description=(
                "Article number (e.g. '2051', '613-bis'): return only that "
                "article plus its ±1 positional neighbors instead of the "
                "whole act."
            )
        ),
    ] = None,
) -> ActOut:
    """Full parsed text of one act; ``?article=N`` narrows to N ± 1 neighbors."""
    row = lookup_act(request.app.state.engine, act_ref)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Atto '{act_ref}' non trovato.")

    act = _load_act(request.app.state.settings.corpus_path, row["file_path"])

    articles: list[ArticleOut]
    if article is not None:
        # Locate the window on the parsed articles FIRST, then build response
        # models only for the <=3 windowed ones — never for a whole Codice.
        numbers = [a.number for a in act.articles]
        window = _window_slice(numbers, article)
        articles = [
            # Occurrence by plain string counting over the prefix: cheap,
            # because it runs for at most 3 articles.
            _article_out(art, numbers[:i].count(art.number) + 1)
            for i, art in enumerate(act.articles[window], start=window.start)
        ]
    else:
        occurrences: dict[str, int] = {}
        articles = []
        for art in act.articles:
            occ = occurrences.get(art.number, 0) + 1
            occurrences[art.number] = occ
            articles.append(_article_out(art, occ))

    return ActOut(
        act_ref=row["act_ref"],
        title=row["title"],
        act_type=row["act_type"],
        vigenza=row["vigenza"],
        collection=row["collection"],
        articles=articles,
    )
