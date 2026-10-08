# Измерения ДЗ1

## Готовые результаты — только просмотр

Серии сохранены 07.10.2026. На каждой операции 5 прогревов и 30 учитываемых
повторов, оператор demo (id=1). Исходные [JSON/CSV](../measurements) не перезаписываются.

| Серия | Файл | Начало, МСК |
|---|---|---|
| SMALL | small_baseline.json | 07.10.2026 20:38:41 |
| WORKING | working_baseline.json | 07.10.2026 20:39:38 |
| SMALL, диагностика | small_diagnostic.json | 07.10.2026 20:42:15 |
| WORKING, диагностика | working_diagnostic.json | 07.10.2026 20:43:18 |

Из корня проекта в WSL; требуется системный Python 3 со стандартной библиотекой:

```bash
python3 scripts/view_measurements.py --section small
python3 scripts/view_measurements.py --section working
python3 scripts/view_measurements.py --section comparison
python3 scripts/view_measurements.py --section diagnostic
python3 scripts/view_measurements.py --section inventory
```

Все таблицы: `python3 scripts/view_measurements.py`.
Просмотрщик читает файлы, не обращается к HTTP/БД и ничего не измеряет.
Он показывает имя файла, дату, размер, прогрев и число повторов.
Дата comparison — даты исходных серий; дата создания сравнения не сохранялась.

## Воспроизведение — будущий новый запуск

Команды ниже предназначены для отдельного стенда без других пользователей,
поднятого по [README](../README.md). **Seed заменяет все пять таблиц, включая ручные записи.**
Текущую WORKING и запись №100001 при редактуре отчёта не меняли.
Задавайте новые имена результатов: существующий `--output` перезаписывается.
Не пересобирайте и не перезапускайте app между сравниваемыми сериями;
браузерные проверки, pytest и посторонние запросы выполняйте отдельно.

```bash
docker compose exec -T app python -m app.cli seed --size small
docker compose exec -T app python -m app.cli data-counts
docker compose exec -T app python -m app.cli measure --size small --warmup 5 --repeats 30 --output measurements/small_recheck.json
docker compose exec -T app python -m app.cli seed --size working
docker compose exec -T app python -m app.cli data-counts
docker compose exec -T app python -m app.cli measure --size working --warmup 5 --repeats 30 --output measurements/working_recheck.json
docker compose exec -T app python -m app.cli compare --small measurements/small_recheck.json --working measurements/working_recheck.json --output measurements/comparison_recheck.json
```

После сравнения выберите кандидатов по его результату. В исходной работе это
free_slots, appointments_list, appointment_create, summary:

```bash
docker compose exec -T app python -m app.cli seed --size small
docker compose exec -T app python -m app.cli measure --size small --warmup 5 --repeats 30 --diagnostic --operations free_slots,appointments_list,appointment_create,summary --output measurements/small_diagnostic_recheck.json
docker compose exec -T app python -m app.cli seed --size working
docker compose exec -T app python -m app.cli measure --size working --warmup 5 --repeats 30 --diagnostic --operations free_slots,appointments_list,appointment_create,summary --output measurements/working_diagnostic_recheck.json
```

## Условия и расчёт

HTTPX 0.28.1 из app обращается к Uvicorn по http://127.0.0.1:8080:
HTTP/1.1, keep-alive, один последовательный клиент, trust_env=False.
Период — 2026-01-01–2026-12-31, свободные слоты service_id=1;
список и слоты page=1, size=20. Точные запросы, машина, версии, БД,
ограничения контейнера и хеши кода находятся в `conditions` и `scenario` JSON.
Версии Docker Engine и Compose на момент измерений не сохранены.

Таймер perf_counter_ns охватывает реальный HTTP-запрос и полное чтение ответа.
5 прогревов исключены. p50/p95 — линейная интерполяция позиции (n−1)×p;
max — наибольшее время. Создание выполняется для свободного слота, отмена —
для активной записи id=2. Подготовка cookie и данных, восстановление отмены,
удаление временной записи и восстановление последовательности находятся вне таймера.
Количество и SHA256 строк до/после совпадают; после восстановления состояния количество записей не изменилось (прирост 0).
Проверки: [verification.json](../measurements/verification.json),
[dz1_review.json](dz1_review.json).

Критерий compare: рост p50 ≥ 2× и прирост ≥ 5 мс; это правило отбора, не статистический тест.
SQLAlchemy events before_cursor_execute / after_cursor_execute / handle_error считают
cursor.execute; ContextVar ограничивает текущий запрос, X-Measurement-Id связывает
метрики с HTTP-повтором. SQL и бизнес-правила не изменяются.

В диагностике HTTP, SQL, остаток и число запросов — средние одних и тех же 30 повторов.
Для каждого повтора остаток = HTTP − SQL; SQL-среднее не вычитается из HTTP-медианы.
SQL включает работу драйвера и обмен во время execute, но не последующее извлечение,
ORM и отдельные commit/rollback. Остаток включает их и HTTP-обмен; это не чистое время Python.
Диагностика снята отдельно, её процентили не подменяют baseline.

Один клиент и фиксированные параметры не заменяют нагрузочное испытание;
браузерная отрисовка не измеряется. Диагностика имеет затраты наблюдения,
физическое состояние БД и независимые серии отдельно не исследованы.
Гипотезы и итоговые таблицы — в [отчёте](ДЗ1_Родионов_Сергей_отчёт.docx).
