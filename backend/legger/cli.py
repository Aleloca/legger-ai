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

    Exits on EOF (Ctrl-D), Ctrl-C, or `/quit`.
    """
    from anthropic import Anthropic
    from qdrant_client import QdrantClient

    from legger.chat.generate import MODEL_SONNET, retrieve_for_messages, stream_answer
    from legger.retrieval.embedders import get_embedder
    from legger.retrieval.search import SEARCH_CLIENT_TIMEOUT_S
    from legger.settings import Settings

    settings = Settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not configured: set it in the repo-root .env file.")
        raise SystemExit(1)

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    qdrant = QdrantClient(url=settings.qdrant_url, timeout=SEARCH_CLIENT_TIMEOUT_S)
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
        hits = retrieve_for_messages(
            messages,
            collection=args.collection,
            embedder=embedder,
            client=qdrant,
            k=args.k,
        )
        answer_parts: list[str] = []
        for delta in stream_answer(messages, hits, anthropic_client=anthropic_client):
            print(delta, end="", flush=True)
            answer_parts.append(delta)
        messages.append({"role": "assistant", "content": "".join(answer_parts)})

        print("\n\nFonti consultate:")
        for hit in hits:
            first_header_line = hit.header.splitlines()[0] if hit.header else ""
            print(f"  - {hit.act_ref} art. {hit.article} — {first_header_line}")
        print()


if __name__ == "__main__":
    main()
