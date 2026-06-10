"""Validation of the retrieval eval query set (Task C1, backend/eval/queries.yaml).

Two layers:

- schema validity (always runs): unique ids, kinds in the enum, required
  fields, the agreed 10/12/5/3 kind mix over 30 entries;
- ground truth against the REAL corpus (``@pytest.mark.corpus``, skipped when
  the italia-corpus checkout is missing): every expected (act_ref, article)
  pair must exist in the parsed ``Codici`` collection -- the same files the
  Phase C index is built from. This keeps the eval set honest across corpus
  updates: an article renumbered or dropped upstream fails here, not silently
  in the C4 recall numbers.
"""

from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from legger.corpus.parser import parse_act
from legger.corpus.refs import derive_act_ref
from legger.settings import Settings

QUERIES_PATH = Path(__file__).parents[1] / "eval" / "queries.yaml"

KINDS = {"explicit", "natural", "lay", "trap"}
EXPECTED_KIND_COUNTS = {"explicit": 10, "natural": 12, "lay": 5, "trap": 3}


@lru_cache(maxsize=1)
def _queries() -> tuple[dict, ...]:
    with QUERIES_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, list)
    return tuple(data)


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------


def test_query_count() -> None:
    assert len(_queries()) == 30


def test_ids_unique_and_well_formed() -> None:
    ids = [entry["id"] for entry in _queries()]
    assert len(ids) == len(set(ids))
    assert all(isinstance(qid, str) and qid for qid in ids)


def test_required_fields() -> None:
    for entry in _queries():
        assert isinstance(entry["query"], str) and entry["query"].strip(), entry["id"]
        expected = entry["expected"]
        assert isinstance(expected["act_ref"], str) and expected["act_ref"], entry["id"]
        # Articles are strings ("2051", "62-bis"), never YAML integers.
        assert isinstance(expected["article"], str) and expected["article"], entry["id"]
        if "note" in entry:
            assert isinstance(entry["note"], str), entry["id"]


def test_kinds_in_enum() -> None:
    for entry in _queries():
        assert entry["kind"] in KINDS, entry["id"]


def test_kind_mix() -> None:
    counts: dict[str, int] = dict.fromkeys(KINDS, 0)
    for entry in _queries():
        counts[entry["kind"]] += 1
    assert counts == EXPECTED_KIND_COUNTS


def test_traps_document_expected_behavior() -> None:
    for entry in _queries():
        if entry["kind"] == "trap":
            assert entry.get("note"), f"{entry['id']}: traps must explain themselves in `note`"


# ---------------------------------------------------------------------------
# Ground truth against the real corpus (Codici collection)
# ---------------------------------------------------------------------------

_CODICI = Settings().corpus_path / "Codici"


@lru_cache(maxsize=1)
def _codici_inventory() -> dict[str, frozenset[str]]:
    """act_ref -> set of article numbers, parsed from the real Codici files."""
    inventory: dict[str, set[str]] = {}
    for path in sorted(_CODICI.glob("*.md")):
        act = parse_act(path)
        ref = derive_act_ref(act, f"Codici/{path.name}")
        numbers = inventory.setdefault(ref.act_ref, set())
        numbers.update(article.number for article in act.articles)
    return {act_ref: frozenset(numbers) for act_ref, numbers in inventory.items()}


@pytest.mark.corpus
@pytest.mark.skipif(not _CODICI.is_dir(), reason="italia-corpus checkout not available")
def test_expected_targets_exist_in_codici() -> None:
    inventory = _codici_inventory()
    missing: list[str] = []
    for entry in _queries():
        expected = entry["expected"]
        articles = inventory.get(expected["act_ref"])
        if articles is None:
            missing.append(f"{entry['id']}: act_ref {expected['act_ref']!r} not in Codici")
        elif expected["article"] not in articles:
            missing.append(
                f"{entry['id']}: art. {expected['article']} not in {expected['act_ref']}"
            )
    assert not missing, "\n".join(missing)
