"""Smoke tests for the legger CLI."""

import pytest

from legger import cli


def test_main_exists() -> None:
    assert callable(cli.main)


def test_help_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 0


@pytest.mark.parametrize("command", ["ingest"])
def test_stub_commands_exit_nonzero(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setattr("sys.argv", ["legger", command])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1


def test_chat_requires_api_key(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """`legger chat` fails fast with a clear message when the key is missing."""
    import legger.settings as settings_mod

    class NoKeySettings:
        anthropic_api_key = ""
        qdrant_url = "http://localhost:6333"

    monkeypatch.setattr(settings_mod, "Settings", lambda: NoKeySettings())
    monkeypatch.setattr("sys.argv", ["legger", "chat"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_eval_requires_collection_and_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["legger", "eval"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2  # argparse: missing required arguments


def test_eval_invokes_run_eval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import legger.eval_retrieval as eval_mod

    calls: list[tuple] = []

    def fake_run_eval(collection: str, embedder: str, *, k: int):
        calls.append((collection, embedder, k))
        report = eval_mod.evaluate(
            [], lambda q: [], collection=collection, embedder_name=embedder, k=k, vigenza="vigente"
        )
        return report, "eval/results/fake.json"

    monkeypatch.setattr(eval_mod, "run_eval", fake_run_eval)
    monkeypatch.setattr(
        "sys.argv",
        ["legger", "eval", "--collection", "norme_test", "--embedder", "voyage-law-2", "--k", "5"],
    )
    cli.main()
    assert calls == [("norme_test", "voyage-law-2", 5)]
    out = capsys.readouterr().out
    assert "collection=norme_test" in out
    assert "JSON report: eval/results/fake.json" in out


def test_eval_rerank_runs_both_and_prints_comparison(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--rerank` runs baseline + rerank pipelines and ends with the delta table."""
    import legger.eval_retrieval as eval_mod

    calls: list[dict] = []

    def fake_run_eval(collection: str, embedder: str, *, k: int, rerank: bool = False):
        calls.append({"collection": collection, "k": k, "rerank": rerank})
        report = eval_mod.evaluate(
            [],
            lambda q: [],
            collection=collection,
            embedder_name=embedder,
            k=k,
            vigenza="vigente",
            rerank=rerank,
            rerank_candidates=50 if rerank else None,
        )
        return report, f"eval/results/fake{'-rerank' if rerank else ''}.json"

    monkeypatch.setattr(eval_mod, "run_eval", fake_run_eval)
    monkeypatch.setattr(
        "sys.argv",
        ["legger", "eval", "--collection", "norme_test", "--embedder", "voyage-law-2", "--rerank"],
    )
    cli.main()
    assert [c["rerank"] for c in calls] == [False, True]
    out = capsys.readouterr().out
    assert "JSON report: eval/results/fake.json" in out
    assert "JSON report: eval/results/fake-rerank.json" in out
    assert "Rerank comparison" in out
    assert "DECISION RULE" in out
