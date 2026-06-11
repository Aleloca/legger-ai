"""Command-line interface for legger."""

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="legger", description="legger.ai CLI")
    subparsers = parser.add_subparsers(dest="command")

    index = subparsers.add_parser("index", help="Index a corpus collection into Qdrant")
    index.add_argument(
        "--collection",
        required=True,
        help='Corpus collection folder to index (e.g. "Codici")',
    )
    index.add_argument(
        "--embedder",
        required=True,
        help='Dense embedder: "bge-m3" or a "voyage-*" model id',
    )
    index.add_argument(
        "--qdrant-collection-suffix",
        default=None,
        help="Optional suffix appended to the Qdrant collection name (experiments)",
    )
    index.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the Qdrant collection before indexing",
    )
    index.add_argument(
        "--no-resume",
        action="store_true",
        help="Force full re-embedding (default: skip lots already in Qdrant)",
    )

    evalp = subparsers.add_parser("eval", help="Run the retrieval eval (queries.yaml)")
    evalp.add_argument(
        "--collection",
        required=True,
        help='Qdrant collection to query (e.g. "norme_voyagelaw2")',
    )
    evalp.add_argument(
        "--embedder",
        required=True,
        help='Dense embedder matching the collection: "bge-m3" or a "voyage-*" model id',
    )
    evalp.add_argument("--k", type=int, default=10, help="Hits retrieved per query (default 10)")
    evalp.add_argument(
        "--rerank",
        action="store_true",
        help="Comparison mode: run baseline AND cross-encoder rerank (50 -> k), print the delta",
    )

    ingest = subparsers.add_parser("ingest", help="Corpus ingestion (bootstrap, delta)")
    ingest_sub = ingest.add_subparsers(dest="ingest_command")
    boot = ingest_sub.add_parser(
        "bootstrap",
        help="Full-corpus bootstrap with checkpoint/resume (re-run to resume)",
    )
    boot.add_argument(
        "--collections",
        default=None,
        help='Comma-separated subset of corpus folders (e.g. "Codici,DPR"); default: all',
    )
    boot.add_argument(
        "--embedder",
        default="voyage-4-large",
        help='Dense embedder: "bge-m3" or a "voyage-*" model id (default voyage-4-large)',
    )
    boot.add_argument(
        "--qdrant-collection",
        default="norme",
        help='Target Qdrant collection (default "norme")',
    )
    boot.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse+chunk statistics only: no embedding, no DB writes (C6 cost estimate)",
    )
    delta_p = ingest_sub.add_parser(
        "delta",
        help="Delta ingestion: git pull + diff from the last successful run's commit",
    )
    delta_p.add_argument(
        "--embedder",
        default="voyage-4-large",
        help='Dense embedder: "bge-m3" or a "voyage-*" model id (default voyage-4-large)',
    )
    delta_p.add_argument(
        "--qdrant-collection",
        default="norme",
        help='Target Qdrant collection (default "norme")',
    )
    delta_p.add_argument(
        "--no-pull",
        action="store_true",
        help="Skip `git pull --ff-only` and diff against the local corpus HEAD",
    )
    check_p = ingest_sub.add_parser(
        "check-upstream",
        help="Alert (Telegram) when the corpus upstream has no commit for over N days",
    )
    check_p.add_argument(
        "--max-days",
        type=int,
        default=7,
        help="Staleness threshold in days (default 7, per design §8 risk #1)",
    )

    chat = subparsers.add_parser("chat", help="Interactive grounded chat over the indexed corpus")
    chat.add_argument(
        "--collection",
        default="norme_voyage4large",
        help='Qdrant collection to query (default "norme_voyage4large")',
    )
    chat.add_argument(
        "--embedder",
        default="voyage-4-large",
        help='Dense embedder matching the collection (default "voyage-4-large")',
    )
    chat.add_argument("--k", type=int, default=10, help="Chunks retrieved per turn (default 10)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "index":
        _run_index(args)
        return

    if args.command == "eval":
        _run_eval(args)
        return

    if args.command == "chat":
        _run_chat(args)
        return

    if args.command == "ingest":
        if getattr(args, "ingest_command", None) == "bootstrap":
            _run_ingest_bootstrap(args)
            return
        if getattr(args, "ingest_command", None) == "delta":
            _run_ingest_delta(args)
            return
        if getattr(args, "ingest_command", None) == "check-upstream":
            _run_ingest_check_upstream(args)
            return
        ingest.print_help()
        raise SystemExit(1)

    print(f"'{args.command}' is not implemented yet.")
    raise SystemExit(1)


def _run_index(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    from legger.retrieval.index import index_collection

    report = index_collection(
        args.collection,
        args.embedder,
        suffix=args.qdrant_collection_suffix,
        recreate=args.recreate,
        resume=not args.no_resume,
    )
    print(
        f"\nIndexed {report.chunks_indexed} chunks "
        f"({report.chunks_skipped} skipped, already indexed) from "
        f"{report.files_indexed}/{report.files_total} files into "
        f"'{report.qdrant_collection}' in {report.elapsed_s:.0f}s."
    )
    if report.file_errors:
        print(f"{len(report.file_errors)} file(s) failed:")
        for rel_path, error in report.file_errors:
            print(f"  - {rel_path}: {error}")
        raise SystemExit(2)


def _run_ingest_bootstrap(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    from legger.ingestion.bootstrap import bootstrap

    collections = None
    if args.collections:
        collections = [name.strip() for name in args.collections.split(",") if name.strip()]

    report = bootstrap(
        collections=collections,
        embedder_name=args.embedder,
        qdrant_collection=args.qdrant_collection,
        dry_run=args.dry_run,
    )

    if report.status == "dry-run":
        print(f"\nDRY-RUN over {report.files_total} files ({report.elapsed_s:.0f}s):")
        for name, stats in sorted(report.per_collection.items()):
            print(
                f"  {name}: {stats['files']} files "
                f"(+{stats['files_dedup_skipped']} dedup-skipped), "
                f"{stats['chunks']} chunks, {stats['chars']:,} chars"
            )
        print(
            f"\nTOTAL: {report.files_processed} files would be indexed "
            f"({report.files_dedup_skipped} dedup-skipped, {len(report.errors)} errors), "
            f"{report.est_chunks} chunks, {report.total_chars:,} chars, "
            f"~{report.est_tokens:,} tokens (chars/2.26)."
        )
    else:
        print(
            f"\nRun #{report.run_id} {report.status}: "
            f"{report.files_processed} files processed, "
            f"{report.files_skipped} skipped "
            f"({report.files_resume_skipped} resume + {report.files_dedup_skipped} dedup), "
            f"{report.chunks_indexed} chunks indexed "
            f"({report.chunks_skipped} already present) into "
            f"'{report.qdrant_collection}' in {report.elapsed_s:.0f}s."
        )
        if report.note:
            print(f"Note: {report.note}")

    if report.errors:
        print(f"{len(report.errors)} file(s) failed (recorded in the run row):")
        for entry in report.errors[:20]:
            print(f"  - {entry['file_path']}: {entry['error']}")
        if len(report.errors) > 20:
            print(f"  ... and {len(report.errors) - 20} more")

    if report.status == "failed":
        raise SystemExit(1)


def _run_ingest_delta(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    from legger import alerts
    from legger.ingestion.delta import DeltaRefusedError, delta
    from legger.settings import Settings

    settings = Settings()
    try:
        report = delta(
            embedder_name=args.embedder,
            qdrant_collection=args.qdrant_collection,
            pull=not args.no_pull,
            settings=settings,
        )
    except DeltaRefusedError as exc:
        print(f"Delta rifiutato: {exc}")
        alerts.send_alert(f"delta rifiutata: {exc}", settings=settings)
        raise SystemExit(1) from exc

    span = f"{(report.commit_from or '?')[:12]}..{(report.commit_to or '?')[:12]}"
    if report.files_changed == 0:
        print(f"\nRun #{report.run_id} ({span}): corpus aggiornato, nessuna modifica.")
    else:
        print(
            f"\nRun #{report.run_id} {report.status} ({span}): "
            f"{report.files_changed} file cambiati — "
            f"{report.files_indexed} indicizzati, {report.files_moved} spostati, "
            f"{report.files_deleted} eliminati, {report.files_skipped} skip "
            f"({report.files_resume_skipped} resume + {report.files_dedup_skipped} dedup), "
            f"{report.chunks_indexed} chunk indicizzati, "
            f"{report.stale_points_deleted} punti stantii eliminati, "
            f"{report.vigenza_flips} flip di vigenza, in {report.elapsed_s:.0f}s."
        )
    if report.note:
        print(f"Nota: {report.note}")
    if report.errors:
        print(f"{len(report.errors)} file falliti (registrati nella riga della run):")
        for entry in report.errors[:20]:
            print(f"  - {entry['file_path']}: {entry['error']}")
        if len(report.errors) > 20:
            print(f"  ... e altri {len(report.errors) - 20}")

    # Alerting (Task D4): a failed run alerts with its note, a completed run
    # alerts only when files errored (those files will NOT be retried by the
    # next delta — see legger/ingestion/delta.py); a clean run stays silent.
    # The upstream staleness check runs inline after every delta (and from
    # its own `legger ingest check-upstream` cron); send_alert never raises.
    if report.status == "failed":
        alerts.send_alert(
            f"delta fallita: {report.note or 'errore sconosciuto'}", settings=settings
        )
    elif report.errors:
        alerts.send_alert(
            f"delta completata con {len(report.errors)} errori su {report.files_changed} file",
            settings=settings,
        )
    alerts.check_upstream_freshness(settings.corpus_path, settings=settings)

    if report.status == "failed":
        raise SystemExit(1)


def _run_ingest_check_upstream(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    from legger import alerts
    from legger.settings import Settings

    settings = Settings()
    result = alerts.check_upstream_freshness(
        settings.corpus_path, max_days=args.max_days, settings=settings
    )
    if result.stale:
        alert_note = (
            "alert inviato, dedup 24h"
            if result.alert_sent
            else "alert non inviato (config assente o dedup)"
        )
        print(
            f"Upstream stantio: nessun commit da oltre {args.max_days} giorni "
            f"in {settings.corpus_path} ({alert_note})."
        )
        raise SystemExit(1)
    print(f"Upstream OK: ultimo commit entro {args.max_days} giorni in {settings.corpus_path}.")


def _run_eval(args: argparse.Namespace) -> None:
    from legger.eval_retrieval import format_comparison, format_report, run_eval

    report, json_path = run_eval(args.collection, args.embedder, k=args.k)
    print(format_report(report))
    print(f"\nJSON report: {json_path}")

    if not args.rerank:
        return

    print("\nRunning rerank pass (first run downloads BAAI/bge-reranker-v2-m3, ~2.3GB) ...\n")
    rerank_report, rerank_json_path = run_eval(
        args.collection, args.embedder, k=args.k, rerank=True
    )
    print(format_report(rerank_report))
    print(f"\nJSON report: {rerank_json_path}")
    print()
    print(format_comparison(report, rerank_report))


def _run_chat(args: argparse.Namespace) -> None:
    """REPL: prompt `> `, stream the grounded answer, list the sources consulted.

    Retrieval goes through the unified pipeline (Task E5): query
    understanding, explicit-reference fast path, hybrid search (+ optional
    rerank), 1-hop citation following. Exits on EOF (Ctrl-D), Ctrl-C at the
    prompt, or `/quit`.

    Per-turn failures never kill the REPL: any exception in retrieval or
    generation (Anthropic API errors, Qdrant down, Ctrl-C mid-stream) prints
    a short notice, pops the dangling user turn from the history (so the
    next turn does not carry a question that was never answered), and goes
    back to the prompt.
    """
    from anthropic import Anthropic
    from qdrant_client import QdrantClient
    from sqlalchemy import create_engine

    from legger.chat.generate import MODEL_SONNET, stream_answer
    from legger.retrieval.embedders import get_embedder
    from legger.retrieval.pipeline import retrieve
    from legger.retrieval.search import SEARCH_CLIENT_TIMEOUT_S
    from legger.settings import Settings

    settings = Settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not configured: set it in the repo-root .env file.")
        raise SystemExit(1)

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    qdrant = QdrantClient(url=settings.qdrant_url, timeout=SEARCH_CLIENT_TIMEOUT_S)
    # The engine backs the fast path's advisory acts-table probe only:
    # creation is lazy and probe failures degrade inside the pipeline, so a
    # stopped Postgres never blocks the chat.
    engine = create_engine(settings.database_url)
    embedder = get_embedder(args.embedder)
    messages: list[dict] = []

    print(
        f"legger chat — {args.collection} / {args.embedder} / {MODEL_SONNET} "
        f"(top-{args.k}). /quit per uscire.\n"
    )
    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user_input:
            continue
        if user_input in {"/quit", "/exit"}:
            return

        messages.append({"role": "user", "content": user_input})
        try:
            result = retrieve(
                messages,
                qdrant_client=qdrant,
                engine=engine,
                anthropic_client=anthropic_client,
                collection=args.collection,
                embedder=embedder,
                k=args.k,
            )
            answer_parts: list[str] = []
            stop_reason: str | None = None
            gen = stream_answer(messages, result.hits, anthropic_client=anthropic_client)
            # next()-driven so the generator's RETURN value (the stop_reason,
            # see stream_answer) is captured from StopIteration.
            while True:
                try:
                    delta = next(gen)
                except StopIteration as stop:
                    stop_reason = stop.value
                    break
                print(delta, end="", flush=True)
                answer_parts.append(delta)
        except KeyboardInterrupt:
            print("\n[turno interrotto]")
            messages.pop()  # drop the unanswered user turn
            continue
        except Exception as exc:  # anthropic.APIError, qdrant/network, ...
            print(f"\n[errore: {type(exc).__name__}: {exc}]\nRiprova.")
            messages.pop()  # drop the unanswered user turn
            continue
        messages.append({"role": "assistant", "content": "".join(answer_parts)})
        if stop_reason == "max_tokens":
            print("\n[risposta troncata: raggiunto il limite di token]")

        print("\n\nFonti consultate:")
        for source in result.sources:
            vigenza = "" if source.vigenza == "vigente" else f" [{source.vigenza}]"
            print(f"  - {source.act_ref} art. {source.article} — {source.title}{vigenza}")
        if result.used_fastpath:
            print("  (riferimento esplicito risolto via fast path)")
        print()


if __name__ == "__main__":
    main()
