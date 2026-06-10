"""Parser for the base64-encoded Akoma Ntoso HTML acts (A1).

Files named ``YYYY-MM-DD_<codice>_(VIGENZA_<data>|ORIGINALE)_V<n>.md`` are not
markdown: they hold base64-encoded HTML produced by Normattiva's AKN pipeline
(the Codice Civile exists only in this format). Structure, verified on the
decoded fixture:

- ``<h1 class="tipo-numero-data-akn">`` -> act title (A3);
- ``<h2 class="titolo-atto-akn">``      -> subtitle (title + GU code);
- ``<h2 class="article-num-akn">Art. N</h2>`` + ``<span class="art-just-text-akn">``
  -> articles of the carrier decree;
- ``<div class="attachment-name"><Nome allegato>-art. N [bis]</div>`` +
  ``<span class="attachment-just-text">`` -> articles of the attached code
  (same plain-marker grammar as A4.3); the attachment name (e.g. ``CODICE
  CIVILE``) becomes the article ``path``;
- ``<div class="art_aggiornamento-akn">`` -> consolidation notes, appended to
  the current article body (consistent with the markdown path, A8); the
  ``ins-akn`` / ``art_abrogato-akn`` divs carry the ``((...))`` markers and are
  plain text content.

Parsing uses the stdlib ``html.parser`` because it is lenient: corpus files
can be byte-truncated prefixes, so the decoded HTML may simply stop
mid-document without closing tags.
"""

import base64
import re
from html.parser import HTMLParser

from legger.corpus.models import Act, Article, Comma
from legger.corpus.parser import (
    _PLAIN_ART,
    _SETEXT_ART,
    _finish_article,
    _normalize_number,
)

# Classes that start a capture; any of them appearing while a capture is open
# forcibly closes the previous one (they are always siblings in the document).
_CAPTURE_STARTERS = {
    ("h1", "tipo-numero-data-akn"): "title",
    ("h2", "titolo-atto-akn"): "subtitle",
    ("h2", "article-num-akn"): "artnum",
    ("div", "attachment-name"): "attname",
    ("span", "art-just-text-akn"): "body",
    ("span", "attachment-just-text"): "body",
    ("div", "art_aggiornamento-akn"): "body",
}


def parse_akn_text(text: str) -> Act:
    """Decode the base64 payload and parse the Akoma Ntoso HTML."""
    cleaned = re.sub(r"\s+", "", text)
    # Truncated corpus files may not end on a 4-char base64 boundary.
    cleaned = cleaned[: len(cleaned) - len(cleaned) % 4]
    html = base64.b64decode(cleaned).decode("utf-8", errors="replace")
    return parse_akn_html(html)


def parse_akn_html(html: str) -> Act:
    extractor = _AknExtractor()
    extractor.feed(html)
    extractor.close()
    extractor.flush_article()

    articles = [
        _finish_article(
            {
                "number": raw["number"],
                "style": "akn",
                "path": raw["path"],
                "lines": _body_lines(raw["parts"]),
            }
        )
        for raw in extractor.raw_articles
    ]
    if not articles:
        body = "\n".join(_body_lines(extractor.orphan_parts)).strip()
        articles = [Article(number="unico", commi=[Comma(number=None, text=body)])]

    return Act(
        title=extractor.title,
        subtitle=extractor.subtitle,
        source_format="akn-html",
        articles=articles,
    )


def _body_lines(parts: list[str]) -> list[str]:
    """``<br>``-separated capture buffer -> stripped body lines."""
    return [line.strip() for line in "".join(parts).split("\n")]


class _AknExtractor(HTMLParser):
    """Pulls title/subtitle and per-article text buffers out of the AKN HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.subtitle: str | None = None
        self.raw_articles: list[dict] = []
        self.orphan_parts: list[str] = []
        self._current: dict | None = None  # {"number", "path", "parts"}
        self._capture: tuple[str, str] | None = None  # (kind, tag)
        self._depth = 0  # nested same-tag elements inside the capture
        self._buffer: list[str] = []

    # -- HTMLParser hooks ---------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        kind = next(
            (k for (t, c), k in _CAPTURE_STARTERS.items() if t == tag and c in classes), None
        )
        if self._capture is not None:
            if kind is not None:
                # A new capture-starting sibling: the previous element was left
                # open (truncated/lenient HTML) -- close it defensively.
                self._end_capture()
            elif tag == self._capture[1]:
                self._depth += 1
                return
            else:
                if tag == "br":
                    self._buffer.append("\n")
                return
        if kind is not None:
            self._capture = (kind, tag)
            self._depth = 0
            self._buffer = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._capture is not None:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None or tag != self._capture[1]:
            return
        if self._depth > 0:
            self._depth -= 1
            return
        self._end_capture()

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    # -- capture handling ---------------------------------------------------

    def flush_article(self) -> None:
        if self._capture is not None:  # EOF inside a capture (truncated file)
            self._end_capture()
        if self._current is not None:
            self.raw_articles.append(self._current)
            self._current = None

    def _end_capture(self) -> None:
        assert self._capture is not None
        kind, _tag = self._capture
        text = "".join(self._buffer)
        self._capture = None
        self._buffer = []
        if kind == "title":
            if self.title is None:
                self.title = " ".join(text.split())
        elif kind == "subtitle":
            if self.subtitle is None:
                self.subtitle = " ".join(text.split())
        elif kind == "artnum":
            match = _SETEXT_ART.match(" ".join(text.split()))
            number = _normalize_number(match.group(1), match.group(2)) if match else text.strip()
            self._open_article(number, [])
        elif kind == "attname":
            name = " ".join(text.split())
            match = _PLAIN_ART.match(name)
            if match:
                number = _normalize_number(match.group(2), match.group(3))
                self._open_article(number, [match.group(1)])
            else:
                self._open_article(name, [])
        elif kind == "body":
            if self._current is not None:
                self._current["parts"].append(text)
            else:
                self.orphan_parts.append(text)

    def _open_article(self, number: str, path: list[str]) -> None:
        if self._current is not None:
            self.raw_articles.append(self._current)
        self._current = {"number": number, "path": path, "parts": []}
