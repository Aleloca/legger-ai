"""Pydantic models for parsed acts.

The parser (B3) returns the *structure of the text* only: act identity
(act_ref, act_type, vigenza, collection) is derived from the file path and
the raw header by Task B4 (refs.py). ``Act.title`` / ``Act.subtitle`` carry
the raw h1/h2 header text exactly for that purpose (A3).
"""

from typing import Literal

from pydantic import BaseModel

Vigenza = Literal["vigente", "abrogato", "decaduto"]

SourceFormat = Literal["markdown", "akn-html"]


class Comma(BaseModel):
    """A comma (paragraph) of an article.

    ``number`` is best-effort (A5): ``"1"``, ``"2-bis"``, or ``None`` when the
    article has no line-start numbered commi (historical acts) -- in that case
    the article holds a single ``Comma(number=None, text=<whole body>)``.
    """

    number: str | None = None
    text: str


class Article(BaseModel):
    """An article of an act.

    - ``number``: ``"18"``, ``"613-bis"`` (suffixes normalized to hyphen form).
    - ``heading``: the rubrica, when the marker style provides one (ATX rubrica
      line, or the parenthesized rubrica of plain/AKN markers); ``None`` otherwise.
    - ``path``: containing partitions. For modern acts these are the setext-h2
      partition headings (e.g. ``["CAPO I - ..."]``, A6); for plain-marker and
      AKN attachment articles it is the marker prefix (e.g. ``["Codice Penale"]``,
      ``["CODICE CIVILE"]``), which disambiguates the approval-decree ``art. 1``
      from the attached code's ``art. 1``. Empty when no container exists.
    """

    number: str
    heading: str | None = None
    path: list[str] = []
    commi: list[Comma] = []


class Act(BaseModel):
    """A parsed act: raw header identity plus the article list (always >= 1).

    ``title`` is the first h1 (``<TIPO ATTO> <data> n. <numero>``, A3) and
    ``subtitle`` the first non-article h2 after it (act title + GU code):
    together they are the raw material B4 needs to derive the canonical
    act_ref. Acts with no detectable article markers yield a single
    pseudo-article with ``number="unico"`` (A10).
    """

    title: str | None = None
    subtitle: str | None = None
    source_format: SourceFormat = "markdown"
    articles: list[Article] = []
