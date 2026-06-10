"""Canonical act_ref and vigenza derivation (Task B4, assunzioni A3/A7).

``act_ref`` is the canonical key joining Qdrant payloads, the Postgres
``acts`` table, the ``[[act_ref|art|comma]]`` citation markers and the
``/acts/{act_ref}`` API. It must be stable across corpus updates (it is
derived from the act *content*, not from the file path, whenever possible),
URL-safe (lowercase ASCII letters, digits and hyphens only) and
collision-aware (the same act stored in two collections yields the same
act_ref -- that is how cross-collection dedup works, A7).

Canonical act_ref format
========================
``<tipo-slug>-<numero>-<anno>`` -- e.g. ``dlgs-1-2018``, ``legge-197-2022``,
``rd-343-1912``. ``tipo-slug`` is the short ref prefix from
:data:`_REF_PREFIX` (``dlgs``, ``dl``, ``legge``, ``rd``, ``dpr``, ...);
``numero`` is the act number lowercased with spaces normalized to hyphens
(``7-B`` -> ``7-b``); ``anno`` is the 4-digit year.

Codici without a usable ``<tipo> <data> n. <numero>`` identity use a stable
well-known slug from the :data:`_KNOWN_CODICI` registry (``codice-civile``,
``codice-penale``, ``codice-protezione-civile``, ...), falling back to a
slugification of the title.

Derivation order (A3): the file content identifies the act, the path does not.

a. **header** -- parse ``Act.title`` (the first h1):
   ``<TIPO> <data italiana>[,] n. <numero>`` with trailing junk such as
   ``(Raccolta 2018)`` ignored;
b. **urn** -- first URN NIR found in the act text
   (``urn:nir:stato:<tipo>:<data>;<numero>``, the Normattiva link target);
c. **filename** -- low-quality fallback, documented per filename class:
   ``YYYY-MM-DD_<codice>_(VIGENZA_...|ORIGINALE)_V<n>`` and stems ending with
   a GU codice redazionale yield ``gu-<codice>`` (the codice redazionale is
   globally unique and collection-independent, so the two filename classes of
   the same act converge); any other stem yields
   ``<collection-prefix>-<slug(stem)>`` (slugs longer than 100 chars are
   truncated and suffixed with a stable hash of the full stem, so two long
   near-identical titles never collide), which IS collection-dependent --
   acceptable because ~95k filenames collide across collections while naming
   *different* acts (A7): a wrong merge is worse than a missed dedup.

Known limitations (relevant to B5/D2 dedup):

- the three sources are not guaranteed to agree with each other for the same
  act (e.g. the header of the Codice Penale carrier decree gives
  ``rd-1398-1930`` while a ``urn:nir:stato:codice.penale`` link gives
  ``codice-penale``). Since the derivation is deterministic on
  (content, path), identical files always get the same act_ref; mixed-source
  mismatches can only happen between *different* files of the same act, and
  ``source`` is exposed (with :data:`SOURCE_RANK` / :attr:`ActRef.rank`) so
  the ingestion can rank header-derived refs above fallback ones.
- the URN scan (A3.b) is deliberately bounded to the subtitle plus the FIRST
  comma: almost every comma after the opening one cites OTHER acts
  ("legge 7 agosto 1990, n. 241" links are everywhere), so scanning the whole
  body would frequently misattribute a *cited* act's URN to the act being
  derived -- a wrong-merge class. The flip side is a missed URN when the only
  self-referencing link appears later in the body; per the project principle
  (a wrong merge is worse than a missed dedup) that act simply falls through
  to the filename fallback. Note the bound does NOT remove the failure mode
  entirely: a foreign URN in the subtitle or first comma is still picked up.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

from legger.corpus._common import SUFFIX_ALT
from legger.corpus.models import Act, Vigenza

Source = Literal["header", "urn", "filename"]

# Dedup ranking for D2: when the same act_ref question is settled by multiple
# files, lower rank wins (header is authoritative, filename is a last resort).
SOURCE_RANK: dict[Source, int] = {"header": 0, "urn": 1, "filename": 2}


class ActRef(BaseModel):
    """Canonical identity of an act, with the provenance of the derivation."""

    act_ref: str
    act_type: str
    number: str | None = None
    year: int | None = None
    date: str | None = None  # ISO YYYY-MM-DD
    source: Source

    @property
    def rank(self) -> int:
        """Dedup rank of the derivation source (lower is better), see D2."""
        return SOURCE_RANK[self.source]


# ---------------------------------------------------------------------------
# Vigenza from the collection folder (A7)
# ---------------------------------------------------------------------------

# Exact folder names (A7): everything else is "vigente".
_VIGENZA_BY_COLLECTION: dict[str, Vigenza] = {
    "Atti normativi abrogati (in originale)": "abrogato",
    "DL decaduti": "decaduto",
}


def vigenza_from_path(rel_path: str) -> Vigenza:
    """Vigenza state from the FIRST path segment (the collection folder)."""
    return _VIGENZA_BY_COLLECTION.get(_collection(rel_path), "vigente")


def _collection(rel_path: str) -> str:
    parts = PurePosixPath(rel_path.lstrip("/")).parts
    return parts[0] if parts else ""


# ---------------------------------------------------------------------------
# Collection folder -> act_type HINT (used only by the filename fallback;
# the authoritative act_type comes from the h1 / URN)
# ---------------------------------------------------------------------------

# Complete map over the 23 real collections. Composite/ambiguous collections
# (hint quality only):
# - "Atti di attuazione Regolamenti UE": mixes DM/DPR/dlgs -> generic "decreto";
# - "Atti normativi abrogati (in originale)": every historical type -> the
#   composite "atto_normativo";
# - "DL e leggi di conversione": DL plus their conversion laws -> the DL side;
# - "Leggi delega e relativi provvedimenti delegati": leggi plus their dlgs ->
#   the legge side;
# - "Regolamenti di delegificazione"/"Regolamenti governativi": issued as DPR;
# - "Codici"/"Testi Unici" name the *role* of the act, not its formal type
#   (the h1 gives dlgs/rd/dpr): kept as role slugs for the hint.
_ACT_TYPE_BY_COLLECTION = {
    "Atti di attuazione Regolamenti UE": "decreto",
    "Atti di recepimento direttive UE": "decreto_legislativo",
    "Atti normativi abrogati (in originale)": "atto_normativo",
    "Codici": "codice",
    "DL decaduti": "decreto_legge",
    "DL e leggi di conversione": "decreto_legge",
    "DL proroghe": "decreto_legge",
    "DPCM": "dpcm",
    "DPR": "dpr",
    "Decreti Legislativi": "decreto_legislativo",
    "Decreti legislativi luogotenenziali": "decreto_legislativo_luogotenenziale",
    "Leggi contenenti deleghe": "legge",
    "Leggi costituzionali": "legge_costituzionale",
    "Leggi delega e relativi provvedimenti delegati": "legge",
    "Leggi di delegazione europea": "legge",
    "Leggi di ratifica": "legge",
    "Leggi finanziarie e di bilancio": "legge",
    "Regi decreti": "regio_decreto",
    "Regi decreti legislativi": "regio_decreto_legislativo",
    "Regolamenti di delegificazione": "dpr",
    "Regolamenti governativi": "dpr",
    "Regolamenti ministeriali": "decreto_ministeriale",
    "Testi Unici": "testo_unico",
}


def act_type_from_collection(collection: str) -> str:
    """Collection folder -> canonical act_type slug (hint/fallback only)."""
    known = _ACT_TYPE_BY_COLLECTION.get(collection)
    if known is not None:
        return known
    return _slugify(collection).replace("-", "_") or "atto_normativo"


# ---------------------------------------------------------------------------
# TIPO string -> (act_type slug, act_ref prefix)
# ---------------------------------------------------------------------------

_TIPO_TO_TYPE = {
    "legge": "legge",
    "legge costituzionale": "legge_costituzionale",
    "decreto legge": "decreto_legge",
    "decreto legislativo": "decreto_legislativo",
    "decreto legislativo luogotenenziale": "decreto_legislativo_luogotenenziale",
    "decreto legge luogotenenziale": "decreto_legge_luogotenenziale",
    "decreto luogotenenziale": "decreto_luogotenenziale",
    "decreto legislativo del capo provvisorio dello stato": "decreto_legislativo_cps",
    "regio decreto": "regio_decreto",
    "regio decreto legge": "regio_decreto_legge",
    "regio decreto legislativo": "regio_decreto_legislativo",
    "decreto del presidente della repubblica": "dpr",
    "dpr": "dpr",
    "decreto del presidente del consiglio dei ministri": "dpcm",
    "dpcm": "dpcm",
    "decreto ministeriale": "decreto_ministeriale",
    "dm": "decreto_ministeriale",
    "decreto": "decreto",
    "codice": "codice",
}

# act_type slug -> short act_ref prefix; unknown types fall back to the
# act_type with underscores turned into hyphens.
_REF_PREFIX = {
    "legge": "legge",
    "legge_costituzionale": "legge-cost",
    "decreto_legge": "dl",
    "decreto_legislativo": "dlgs",
    "decreto_legislativo_luogotenenziale": "dlgs-lgt",
    "decreto_legge_luogotenenziale": "dl-lgt",
    "decreto_luogotenenziale": "dlt",
    "decreto_legislativo_cps": "dlcps",
    "regio_decreto": "rd",
    "regio_decreto_legge": "rdl",
    "regio_decreto_legislativo": "rdlgs",
    "dpr": "dpr",
    "dpcm": "dpcm",
    "decreto_ministeriale": "dm",
    "decreto": "decreto",
    "codice": "codice",
    "testo_unico": "tu",
    "atto_normativo": "atto",
}


def act_slugs(tipo: str) -> tuple[str, str]:
    """TIPO string (h1 prefix or URN tipo) -> ``(act_type, ref_prefix)``.

    Unknown types are slugified (underscores for act_type, hyphens for the
    prefix) -- this never raises.
    """
    norm = _normalize_words(tipo)
    act_type = _TIPO_TO_TYPE.get(norm)
    if act_type is None:
        act_type = norm.replace(" ", "_") or "atto_normativo"
    return act_type, _ref_prefix(act_type)


def _ref_prefix(act_type: str) -> str:
    return _REF_PREFIX.get(act_type, act_type.replace("_", "-"))


def _normalize_words(text: str) -> str:
    """Lowercased, accent/dot-free, hyphen->space, collapsed whitespace."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace(".", "").replace("-", " ").replace("'", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def _slugify(text: str) -> str:
    """URL-safe slug: lowercase ASCII alphanumerics joined by hyphens."""
    return _normalize_words(text).replace(" ", "-")


# ---------------------------------------------------------------------------
# Known codici registry (stable slugs for codici named without numero/anno)
# ---------------------------------------------------------------------------

# Matched at the START of the normalized title (or URN tipo with dots as
# spaces) on a whole-word boundary: the keyword must OPEN the text, so that
# "Disposizioni per l'attuazione del Codice di procedura civile" does NOT
# collide with the CPC itself.
#
# Prefix-extension hazards (a registry key opening the title of a DIFFERENT
# act) are the wrong-merge class of this matcher; the registry is therefore
# sorted longest-key-first at definition time, so an extended name always
# wins over its prefix. Audit notes:
# - "codice penale" used to swallow the military penal codes ("Codice penale
#   militare di pace"/"di guerra", also reachable as URN tipo
#   ``codice.penale.militare.di.pace``): both now have explicit entries;
# - "codice dei contratti pubblici" names three distinct carrier acts (dlgs
#   163/2006, 50/2016, 36/2023). All three corpus files carry a parseable
#   dlgs header, so the registry never fires for them; the bare name (title
#   or URN) inherently means "the codice vigente" and keeps the shared slug;
# - the Costituzione is NOT in the corpus (the "Leggi costituzionali"
#   collection holds only revision laws and special statutes), so no
#   "costituzione" well-known slug is registered -- revisit if a corpus
#   update ever ships the Costituzione itself.
_KNOWN_CODICI: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            ("codice di procedura civile", "codice-procedura-civile"),
            ("codice di procedura penale", "codice-procedura-penale"),
            ("codice procedura civile", "codice-procedura-civile"),
            ("codice procedura penale", "codice-procedura-penale"),
            ("codice civile", "codice-civile"),
            ("codice penale", "codice-penale"),
            ("codice penale militare di pace", "codice-penale-militare-pace"),
            ("codice penale militare di guerra", "codice-penale-militare-guerra"),
            ("codice della navigazione", "codice-navigazione"),
            ("codice della strada", "codice-strada"),
            ("codice del consumo", "codice-consumo"),
            ("codice dei contratti pubblici", "codice-contratti-pubblici"),
            ("codice della protezione civile", "codice-protezione-civile"),
            ("codice protezione civile", "codice-protezione-civile"),
            ("codice dell ordinamento militare", "codice-ordinamento-militare"),
            ("codice in materia di protezione dei dati personali", "codice-privacy"),
            ("codice delle comunicazioni elettroniche", "codice-comunicazioni-elettroniche"),
            ("codice della proprieta industriale", "codice-proprieta-industriale"),
            ("codice dell amministrazione digitale", "codice-amministrazione-digitale"),
            ("codice del terzo settore", "codice-terzo-settore"),
            ("codice della crisi d impresa", "codice-crisi-impresa"),
            ("codice dei beni culturali", "codice-beni-culturali"),
            ("codice delle assicurazioni private", "codice-assicurazioni-private"),
            ("codice del processo amministrativo", "codice-processo-amministrativo"),
            ("codice antimafia", "codice-antimafia"),
            ("codice della nautica da diporto", "codice-nautica-diporto"),
            ("codice postale e delle telecomunicazioni", "codice-postale-telecomunicazioni"),
        ],
        key=lambda entry: len(entry[0]),
        reverse=True,
    )
)


def _known_codice_slug(text: str) -> str | None:
    norm = _normalize_words(text)
    norm = norm.removeprefix("il ")
    for name, slug in _KNOWN_CODICI:
        # Whole-word prefix: "codice civile" must not match "codice civilistico".
        if norm == name or norm.startswith(name + " "):
            return slug
    return None


# ---------------------------------------------------------------------------
# Header (h1) parsing -- A3.a
# ---------------------------------------------------------------------------

_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

# "<TIPO> <giorno> <mese> <anno>[, ] n. <numero>" with optional fascist-era
# roman suffix after the year ("1936-XIV") and trailing junk such as
# "(Raccolta 2018)" ignored. The numero accepts a latin ordinal suffix
# ("7 bis"), a single uppercase letter ("7-B") or an uppercase roman numeral
# (19th-century regi decreti: "n. MMMDCCCLXXV" -- normalized to arabic, as
# confirmed by the matching GU codice redazionale, e.g. 9003875R -> 3875).
# The single-letter suffix is DELIBERATELY uppercase-only: a lowercase letter
# would swallow the conjunction/preposition of a title continuation
# ("n. 241 e successive modificazioni" -> number "241-e", a wrong-merge
# class), and a scan of all 287,913 corpus first lines found zero lowercase
# single-letter suffixes (headers print them uppercase, "7-B").
_HEADER = re.compile(
    r"^\s*(?P<tipo>\D+?)\s+"
    r"(?P<day>\d{1,2})°?\s+"
    r"(?P<month>" + "|".join(_MONTHS) + r")\s+"
    r"(?P<year>\d{4})(?:-[ivxlc]+)?"
    r"\s*,?\s*n\.?\s*"
    rf"(?:(?P<num>\d+(?:[ -](?:{SUFFIX_ALT}|(?-i:[A-Z])))?)|(?P<roman>(?-i:[IVXLCDM]+))\b)",
    re.IGNORECASE,
)

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(roman: str) -> int:
    """Lenient roman parser: handles the additive GU forms (CCCC, DCCCC)."""
    values = [_ROMAN_VALUES[c] for c in roman]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def _from_header(title: str) -> ActRef | None:
    match = _HEADER.match(title)
    if match is None:
        return None
    act_type, prefix = act_slugs(match.group("tipo"))
    if match.group("roman") is not None:
        number = str(_roman_to_int(match.group("roman")))
    else:
        number = match.group("num").lower().replace(" ", "-")
    year = int(match.group("year"))
    month = _MONTHS[match.group("month").lower()]
    day = int(match.group("day"))
    return ActRef(
        act_ref=f"{prefix}-{number}-{year}",
        act_type=act_type,
        number=number,
        year=year,
        date=f"{year:04d}-{month:02d}-{day:02d}",
        source="header",
    )


# ---------------------------------------------------------------------------
# URN NIR parsing -- A3.b
# ---------------------------------------------------------------------------

# urn:nir:stato:decreto.legislativo:2018-01-02;1 -- the date may be the year
# alone (urn:nir:stato:legge:1990;241); the numero stops at "!vig" and other
# URN suffixes.
_URN = re.compile(
    r"urn:nir:stato:(?P<tipo>[a-z][a-z.]*):"
    r"(?P<year>\d{4})(?:-(?P<month>\d{2})-(?P<day>\d{2}))?;"
    r"(?P<num>\d+(?:-[a-z0-9]+)?)",
    re.IGNORECASE,
)


def _from_urn(act: Act) -> ActRef | None:
    match = None
    for part in _urn_scan_parts(act):
        match = _URN.search(part)
        if match is not None:
            break
    if match is None:
        return None
    tipo = match.group("tipo")
    number = match.group("num").lower()
    year = int(match.group("year"))
    date = None
    if match.group("month") is not None:
        date = f"{year:04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    if tipo.lower().startswith("codice"):
        act_type = "codice"
        codice_slug = _known_codice_slug(tipo.replace(".", " "))
        act_ref = codice_slug or _slugify(tipo.replace(".", " "))
    else:
        act_type, prefix = act_slugs(tipo.replace(".", " "))
        act_ref = f"{prefix}-{number}-{year}"
    return ActRef(
        act_ref=act_ref,
        act_type=act_type,
        number=number,
        year=year,
        date=date,
        source="urn",
    )


def _urn_scan_parts(act: Act) -> Iterator[str]:
    """URN scan input: the subtitle, then the FIRST comma -- nothing else.

    Self-referencing URNs (the Normattiva permalink of the act itself) appear
    in the opening material; later commi cite OTHER acts, so widening the
    scan would misattribute a cited act's URN (see the module docstring
    limitations). The scan is incremental: the caller stops at the first hit.
    """
    if act.subtitle:
        yield act.subtitle
    for article in act.articles:
        if article.commi:
            yield article.commi[0].text
            return


# ---------------------------------------------------------------------------
# Filename fallback -- A3.c
# ---------------------------------------------------------------------------

# YYYY-MM-DD_<codice redazionale>_(VIGENZA_<data>|ORIGINALE)_V<n>
_DATA_CODICE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_"
    r"(?P<code>[0-9A-Za-z]+)_"
    r"(?:VIGENZA_\d{4}-\d{2}-\d{2}|ORIGINALE)_V\d+$"
)

# GU codice redazionale at the end of the stem: "18G00011", "030U1398",
# "012U0343" (whole stem) or "0600226R" (historical digits+letter form).
_REDAZIONALE = re.compile(r"(?:^|[ _.])(\d{2,4}[A-Z]\d{4,5}[A-Z]?|\d{6,7}[A-Z])$")


def _from_filename(rel_path: str, collection: str) -> ActRef:
    stem = PurePosixPath(rel_path).name.removesuffix(".md")
    act_type = act_type_from_collection(collection)

    match = _DATA_CODICE.match(stem)
    if match is not None:
        year = int(match.group("year"))
        return ActRef(
            act_ref=f"gu-{match.group('code').lower()}",
            act_type=act_type,
            number=None,
            year=year,
            date=f"{year:04d}-{match.group('month')}-{match.group('day')}",
            source="filename",
        )

    match = _REDAZIONALE.search(stem)
    if match is not None:
        return ActRef(
            act_ref=f"gu-{match.group(1).lower()}",
            act_type=act_type,
            source="filename",
        )

    slug = _slugify(stem)
    if len(slug) > 100:
        # Two long near-identical stems ("Attuazione della direttiva ..."
        # titles) can share their first 100 slug chars while naming different
        # acts: disambiguate the truncation with a stable hash of the FULL
        # original stem, so the ref stays deterministic across corpus updates.
        digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:100].rstrip('-')}-{digest}"
    slug = slug or "atto"
    return ActRef(
        act_ref=f"{_ref_prefix(act_type)}-{slug}",
        act_type=act_type,
        source="filename",
    )


# ---------------------------------------------------------------------------
# Public derivation entry point
# ---------------------------------------------------------------------------


def derive_act_ref(act: Act, rel_path: str) -> ActRef:
    """Derive the canonical :class:`ActRef` for a parsed act.

    Order per A3: h1 header (authoritative), then the first URN NIR in the
    text, then the filename (low-quality fallback). Never raises on weird
    input; the derivation is deterministic, so the same (content, path) pair
    always produces the same act_ref.
    """
    if act.title:
        ref = _from_header(act.title)
        if ref is not None:
            return ref
        codice_slug = _known_codice_slug(act.title)
        if codice_slug is not None:
            return ActRef(act_ref=codice_slug, act_type="codice", source="header")
    ref = _from_urn(act)
    if ref is not None:
        return ref
    return _from_filename(rel_path, _collection(rel_path))
