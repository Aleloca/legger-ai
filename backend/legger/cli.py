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

    subparsers.add_parser("ingest", help="Ingest the corpus (not implemented yet)")
    subparsers.add_parser("chat", help="Start an interactive chat (not implemented yet)")

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


def _run_eval(args: argparse.Namespace) -> None:
    from legger.eval_retrieval import format_report, run_eval

    report, json_path = run_eval(args.collection, args.embedder, k=args.k)
    print(format_report(report))
    print(f"\nJSON report: {json_path}")


if __name__ == "__main__":
    main()
