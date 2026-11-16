"""Display saved k6 and telemetry results for one or more stages."""

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


def read_jsonlines(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def show(directory):
    stage = json.loads((directory / "stage.json").read_text(encoding="utf-8"))
    metrics = json.loads((directory / "summary.json").read_text(encoding="utf-8"))["metrics"]
    timings = metrics["http_req_duration{phase:load}"]
    http_errors = metrics["http_req_failed{phase:load}"]["value"]
    contract_errors = metrics["operation_errors"]["value"]
    completed = metrics["iterations"]
    dropped = metrics.get("dropped_iterations", {}).get("count", 0)
    mode = stage["mode"]
    threshold = "smoke only" if mode == "smoke" else (
        "PASS" if timings["p(95)"] < 500 and max(http_errors, contract_errors) < 0.01 else "FAIL"
    )

    statuses = Counter()
    responses = []
    window_start = None
    for row in read_jsonlines(directory / "k6.jsonl"):
        if row["type"] != "Point":
            continue
        if row["metric"] == "load_window_start":
            window_start = datetime.fromisoformat(row["data"]["time"].replace("Z", "+00:00"))
        if row["metric"] != "http_req_duration":
            continue
        tags = row["data"]["tags"]
        if tags.get("phase") == "load":
            statuses[tags["operation"], tags["status"]] += 1
            if 200 <= int(tags["status"]) < 300:
                responses.append(datetime.fromisoformat(row["data"]["time"].replace("Z", "+00:00")))

    print(f"\n{directory.name} | {mode} | {stage['start_utc']} | {threshold} | k6={stage['k6_exit_code']}")
    print(f"offered={stage['offered_rps']} req/s | completed={completed['count']} "
          f"({completed['rate']:.2f} iterations/s in k6) | dropped={dropped}")
    print(f"p50={timings['med']:.2f} ms | p95={timings['p(95)']:.2f} ms | max={timings['max']:.2f} ms "
          f"| HTTP errors={http_errors:.2%} | contract errors={contract_errors:.2%}")
    seconds = stage["duration_seconds"]
    if window_start is not None and seconds:
        window_end = window_start + timedelta(seconds=seconds)
        in_window = sum(window_start <= time < window_end for time in responses)
        print(f"HTTP 2xx: {in_window} during {seconds}s ({in_window / seconds:.2f}/s), "
              f"{len(responses) - in_window} more during drain")
    for (operation, status), count in sorted(statuses.items()):
        print(f"  {operation:<24s} HTTP {status:>3s} {count:>4d}")
    for name in ("docker.jsonl", "postgres.jsonl"):
        print(f"  {name}: {sum(1 for _ in read_jsonlines(directory / name))} rows")

    raw = directory / "profile.txt"
    if raw.exists():
        show_profile(raw)


def show_profile(raw):
    total = 0
    leaf_functions = Counter()
    with raw.open(encoding="utf-8") as stream:
        for line in stream:
            stack, _, value = line.strip().rpartition(" ")
            count = int(value)
            total += count
            frames = stack.split(";")
            if any("app/services.py:" in frame for frame in frames):
                leaf_functions[frames[-1]] += count
    print(f"  py-spy: {total} stack samples; top leaf frames on service stacks")
    for frame, count in leaf_functions.most_common(5):
        print(f"    {count:>5d} {count / total:>7.2%}  {frame}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    for directory in parser.parse_args().directories:
        show(directory)


if __name__ == "__main__":
    main()
