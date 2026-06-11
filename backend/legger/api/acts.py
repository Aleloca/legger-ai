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
  (maxsize 64) keyed by ``(absolute file path, mtime_ns)``. Parsing is
  millisecond-scale for normal acts but the Codice civile (3282 articles,
  ~9 MB markdown) takes ~330 ms — and the Codici are exactly the acts the
  split-view opens most. The mtime key self-invalidates when the corpus
  clone is updated (git checkout rewrites the file, bumping mtime); stale
  entries just age out of the LRU.
- **Row exists but file missing on disk** -> **503**, not 404: the act
  exists, the server is temporarily unable to render it (skew while the
  corpus clone is mid-update, or a misconfigured ``CORPUS_PATH``). A 404
  would tell the frontend the act_ref is wrong, which it is not.
- **Response size**: the Codice civile response is several MB of JSON.
  Accepted for now — the split-view loads an act once and scrolls within
  it. ``?article=N`` returns a window of the requested article ±1 neighbor
  (by document position) for clients that want a light payload instead.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from legger.corpus.parser import parse_act
from legger.db import acts as acts_table

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from legger.corpus.models import Act

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def article_anchor(number: str, occurrence: int = 1) -> str:
    """URL-safe anchor id for an article, e.g. ``art-18``, ``art-613-bis``.

    Mirrors the article segment of the B5 chunk ids
    (``{act_ref}#art-{number}#{i}``), including the ``.{occurrence}``
    disambiguator for repeated numbers (approval decree + attached code both
    having an art. 1), so a chunk id maps onto an anchor mechanically:
    slug(``art-1.2``) == ``art-1-2``. Slug: lowercase, every non-alphanumeric
    run becomes a single hyphen.
    """
    key = number if occurrence == 1 else f"{number}.{occurrence}"
    return "art-" + _SLUG_RE.sub("-", key.lower()).strip("-")


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


@lru_cache(maxsize=64)
def _parse_cached(file_path: str, mtime_ns: int) -> Act:
    """Parse *file_path*, memoized on (path, mtime) — see module docstring."""
    return parse_act(Path(file_path))


def _load_act(corpus_path: Path, file_path: str) -> Act:
    """Parse ``corpus_path / file_path`` through the LRU cache.

    Raises ``HTTPException(503)`` when the file is missing on disk (DB/corpus
    skew — documented choice, see module docstring).
    """
    full_path = corpus_path / file_path
    try:
        mtime_ns = full_path.stat().st_mtime_ns
    except OSError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"L'atto è censito ma il file '{file_path}' non è al momento "
                "disponibile (aggiornamento del corpus in corso?). Riprovare."
            ),
        ) from None
    return _parse_cached(str(full_path), mtime_ns)


def _article_window(articles: list[ArticleOut], number: str) -> list[ArticleOut]:
    """The article *number* plus its ±1 positional neighbors.

    Position-based, not number-based: "the previous article" of art. 2052 is
    whatever precedes it in the document (2051), without arithmetic on
    suffixed numbers (613-bis). First occurrence wins for duplicated numbers.
    """
    for i, article in enumerate(articles):
        if article.number == number:
            return articles[max(0, i - 1) : i + 2]
    raise HTTPException(
        status_code=404, detail=f"Articolo '{number}' non presente nell'atto."
    )


@router.get("/acts/{act_ref}", response_model=ActOut)
def get_act(act_ref: str, request: Request, article: str | None = None) -> ActOut:
    """Full parsed text of one act; ``?article=N`` narrows to N ± 1 neighbors."""
    row = lookup_act(request.app.state.engine, act_ref)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Atto '{act_ref}' non trovato.")

    act = _load_act(request.app.state.settings.corpus_path, row["file_path"])

    occurrences: dict[str, int] = {}
    articles: list[ArticleOut] = []
    for art in act.articles:
        occ = occurrences.get(art.number, 0) + 1
        occurrences[art.number] = occ
        articles.append(
            ArticleOut(
                number=art.number,
                heading=art.heading,
                path=art.path,
                commi=[CommaOut(number=c.number, text=c.text) for c in art.commi],
                anchor=article_anchor(art.number, occ),
            )
        )
    if article is not None:
        articles = _article_window(articles, article)

    return ActOut(
        act_ref=row["act_ref"],
        title=row["title"],
        act_type=row["act_type"],
        vigenza=row["vigenza"],
        collection=row["collection"],
        articles=articles,
    )
