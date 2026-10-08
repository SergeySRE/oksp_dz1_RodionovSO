"""Read-only terminal viewer for saved HW1 JSON; standard library only."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MSK = timezone(timedelta(hours=3), "МСК")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def table(headers, rows):
    rendered = [[f"{v:.2f}" if isinstance(v, float) else str(v) for v in row] for row in rows]
    widths = [max(len(title), *(len(row[i]) for row in rendered)) for i, title in enumerate(headers)]
    print(" | ".join(title.ljust(width) for title, width in zip(headers, widths)))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(" | ".join(value.ljust(width) for value, width in zip(row, widths)))


def metadata(path, doc):
    started = datetime.fromisoformat(doc["started_at_utc"]).astimezone(MSK)
    print(f"Исходный файл: {path.resolve()}")
    print(f"Дата серии: {started:%Y-%m-%d %H:%M:%S} МСК; UTC: {doc['started_at_utc']}")
    print(f"Набор: {doc['size'].upper()}; режим: {doc['kind']}; "
          f"прогрев: {doc['warmup']}; повторов на операцию: {doc['repeats']}")
    print(f"Статус: {doc['status']}; данные сохранены: {doc.get('data_unchanged')}; "
          f"учётная запись: {doc['conditions']['account']}")
    counts = doc["data_counts_before"]
    print("Объёмы БД на момент серии: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def series(path):
    doc = load(path)
    print()
    metadata(path, doc)
    if doc["kind"] == "diagnostic":
        print("Диагностика: HTTP, SQL и остаток — СРЕДНИЕ одних и тех же повторов, мс.")
        print("Остаток = HTTP - cursor.execute; это не чистое время Python.")
        table(["operation", "n", "HTTP mean", "SQL mean", "SQL count", "other mean", "p50", "p95", "max"],
              [[r["operation"], r["repeats"], r["http_total_ms"], r["db_total_ms"],
                r["db_query_count"], r["app_or_other_ms"], r["p50_ms"], r["p95_ms"], r["max_ms"]]
               for r in doc["aggregated"]])
    else:
        print("Полное HTTP-время с чтением ответа, мс; прогрев исключён.")
        table(["operation", "n", "p50", "p95", "max"],
              [[r[k] for k in ("operation", "repeats", "p50_ms", "p95_ms", "max_ms")]
               for r in doc["aggregated"]])


def source_path(comparison_path, reference):
    # Bundled results can be viewed from any current directory.
    local = comparison_path.parent / Path(reference).name
    return local if local.exists() else ROOT / reference


def comparison(path):
    doc = load(path)
    print(f"\nИсходный файл сравнения: {path.resolve()}")
    print("Дата создания сравнения в JSON не сохранена; даты исходных серий:")
    for key in ("small_file", "working_file"):
        source = source_path(path, doc[key])
        metadata(source, load(source))
    criterion = doc["criterion"]
    print(f"Кандидат: рост p50 >= {criterion['p50_growth_at_least']}x И "
          f"прирост p50 >= {criterion['p50_delta_ms_at_least']} мс. "
          "Это правило отбора, не тест статистической значимости.")
    table(["operation", "p50 growth x", "p95 growth x", "p50 delta ms", "candidate"],
          [[r["operation"], r["p50_growth"], r["p95_growth"], r["p50_delta_ms"],
            "ДА" if r["candidate"] else "нет"] for r in doc["rows"]])


def inventory(directory):
    docs = [load(directory / f"{size}_baseline.json") for size in ("small", "working")]
    print("\nСОХРАНЁННЫЕ объёмы на момент исходных серий, не текущая БД:")
    for size, doc in zip(("small", "working"), docs):
        metadata(directory / f"{size}_baseline.json", doc)
    table(["table", "SMALL", "WORKING", "growth x"],
          [[name, value, docs[1]["data_counts_before"][name],
            docs[1]["data_counts_before"][name] / value]
           for name, value in docs[0]["data_counts_before"].items()])
    conditions = docs[0]["conditions"]
    print("Сохранённые условия SMALL (WORKING: "
          + ("идентичны" if conditions == docs[1]["conditions"] else "ОТЛИЧАЮТСЯ") + "):")
    print(f"CPU: {conditions['cpu_model']}; logical CPUs: {conditions['logical_cpus']}; "
          f"{conditions['memory']}")
    print(f"Python: {conditions['python']}; packages: {conditions['packages']}")
    print(f"PostgreSQL: {conditions['postgres']}")
    print(f"DB settings: {conditions['postgres_settings']}; cgroups: {conditions['cgroups']}")


def main():
    parser = argparse.ArgumentParser(description="Просмотр сохранённых результатов ДЗ1; без HTTP и БД")
    parser.add_argument("--section", choices=("all", "small", "working", "comparison", "diagnostic", "inventory"),
                        default="all")
    parser.add_argument("--directory", type=Path, default=ROOT / "measurements")
    args = parser.parse_args()
    directory = args.directory.resolve()
    print("sergey_rodionov | ПРОСМОТР СОХРАНЁННЫХ РЕЗУЛЬТАТОВ")
    print("Это не новый запуск измерений: HTTP-запросы, seed и обращения к БД не выполняются.")
    try:
        if args.section in ("all", "inventory"):
            inventory(directory)
        for name in ("small", "working"):
            if args.section in ("all", name):
                series(directory / f"{name}_baseline.json")
        if args.section in ("all", "comparison"):
            comparison(directory / "comparison.json")
        if args.section in ("all", "diagnostic"):
            for name in ("small", "working"):
                series(directory / f"{name}_diagnostic.json")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Не удалось прочитать сохранённые результаты: {error}\n")


if __name__ == "__main__":
    main()
