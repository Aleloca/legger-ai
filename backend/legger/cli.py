"""Command-line interface for legger."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="legger", description="legger.ai CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ingest", help="Ingest the corpus (not implemented yet)")
    subparsers.add_parser("eval", help="Run retrieval evaluation (not implemented yet)")
    subparsers.add_parser("chat", help="Start an interactive chat (not implemented yet)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    print(f"'{args.command}' is not implemented yet.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
