import argparse
import json

from app.db import engine, init_db
from app.seed import SIZES, data_counts, seed_database


def main():
    parser = argparse.ArgumentParser(description="Учебный стенд sergey_rodionov")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db")
    seed = subcommands.add_parser("seed", help="Replace all data in the project schema")
    seed.add_argument("--size", choices=SIZES, required=True)
    subcommands.add_parser("data-counts")
    measure_parser = subcommands.add_parser("measure", help="Sequential real HTTP baseline measurements")
    measure_parser.add_argument("--size", choices=SIZES, required=True)
    measure_parser.add_argument("--warmup", type=int, default=5)
    measure_parser.add_argument("--repeats", type=int, default=30)
    measure_parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    measure_parser.add_argument("--operations", help="Comma-separated operation names, default: all ten")
    measure_parser.add_argument("--diagnostic", action="store_true")
    measure_parser.add_argument("--output", help="JSON path; CSV files are saved alongside it")
    compare_parser = subcommands.add_parser("compare", help="Compare completed baseline measurements")
    compare_parser.add_argument("--small", required=True)
    compare_parser.add_argument("--working", required=True)
    compare_parser.add_argument("--output", default="measurements/comparison.json")
    compare_parser.add_argument("--growth-threshold", type=float, default=2.0)
    compare_parser.add_argument("--min-delta-ms", type=float, default=5.0)
    args = parser.parse_args()
    try:
        if args.command == "init-db":
            init_db()
            print("sergey_rodionov: schema and tables initialized")
        elif args.command == "seed":
            seed_database(engine, args.size)
            print(f"sergey_rodionov: seed={args.size}, random.seed(42)")
            print(json.dumps(data_counts(engine), indent=2))
        elif args.command == "measure":
            from app.measurement import measure
            operations = [name.strip() for name in args.operations.split(",")] if args.operations else None
            measure(engine, args.size, args.warmup, args.repeats, args.base_url,
                    operations, args.diagnostic, args.output)
        elif args.command == "compare":
            from app.measurement import compare_files
            compare_files(args.small, args.working, args.output,
                          args.growth_threshold, args.min_delta_ms)
        else:
            print("sergey_rodionov: actual database counts")
            print(json.dumps(data_counts(engine), indent=2))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
