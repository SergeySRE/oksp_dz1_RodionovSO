# Запись на приём к специалисту

## Назначение

Учебный сервис ДЗ1 по дисциплине «Оптимизация клиент-серверных приложений».
Родионов Сергей Олегович, БИСТ-24-ПО-2; вариант 3.
Оператор ищет свободный слот, создаёт и отменяет запись, просматривает загрузку специалистов.
Префикс БД, схемы, Compose-проекта и интерфейса — `sergey_rodionov`.

## Требования

Git, WSL Ubuntu и Docker Engine / Docker Desktop с Docker Compose v2 или новее.
Команды ниже выполняются в WSL. Локальный Python для запуска приложения не нужен.

В контейнерах используются Python 3.12.12, FastAPI 0.115.12, Uvicorn 0.34.2,
SQLAlchemy 2.0.40, psycopg 3.2.6, PostgreSQL 16.10, Jinja2 3.1.6,
HTTPX 0.28.1 и pytest 8.3.5. Клиент — HTML/CSS с небольшим JavaScript.
Версии закреплены в [requirements.txt](requirements.txt), [constraints.txt](constraints.txt),
[Dockerfile](Dockerfile) и [compose.yaml](compose.yaml).

## Установка и запуск

```bash
git clone https://github.com/SergeySRE/oksp_dz1_RodionovSO.git
cd oksp_dz1_RodionovSO
cp .env.example .env
openssl rand -hex 32
nano .env
```

В `.env` задайте свой `POSTGRES_PASSWORD` и вставьте полученную случайную строку
в `SESSION_SECRET`. Для пароля БД используйте латинские буквы и цифры, чтобы он
не требовал кодирования в URI. При занятых портах измените `APP_PORT` и `DB_PORT`.
Затем последовательно выполните:

```bash
docker compose build
docker compose up -d --wait db
docker compose run --rm -T app python -m app.cli init-db
docker compose run --rm -T app python -m app.cli seed --size small
docker compose run --rm -T app python -m app.cli data-counts
docker compose up -d app
```

Интерфейс: http://localhost:8080. Вход: **demo / demo**.
Данные seed находятся в периоде 01.01.2026–31.12.2026.

Для WORKING вместо SMALL:

```bash
docker compose run --rm -T app python -m app.cli seed --size working
docker compose run --rm -T app python -m app.cli data-counts
```

**Seed заменяет все данные пяти таблиц, включая ручные записи.**
Не запускайте его одновременно с запросами пользователей или измерениями.
`docker compose down` останавливает стенд и сохраняет том БД;
`docker compose up -d` запускает его снова.

## Переменные окружения

Compose читает `.env`; этот файл не публикуется. Безопасные примеры — в [.env.example](.env.example).

| Переменная | Назначение | Пример |
|---|---|---|
| POSTGRES_PASSWORD | Пароль учебного пользователя PostgreSQL | changeme |
| DB_PORT | Локальный порт БД | 5433 |
| APP_PORT | Локальный порт сервиса | 8080 |
| SESSION_SECRET | Секрет подписи cookie, не менее 32 символов | changeme-replace-with-a-long-random-string |
| SESSION_HTTPS_ONLY | Secure-cookie; false для локального HTTP | false |
| DEMO_LOGIN / DEMO_PASSWORD | Учётная запись, создаваемая seed | demo / demo |
| DATABASE_URL | URI SQLAlchemy; Compose задаёт адрес db:5432 | postgresql+psycopg://sergey_rodionov:changeme@db:5432/sergey_rodionov |
| DB_SCHEMA | Схема PostgreSQL, задана в Compose | sergey_rodionov |

## Проверка работоспособности

Войдите на http://localhost:8080/login. Откройте список с фильтром и пагинацией,
найдите свободный слот, создайте запись, проверьте карточку, отмените запись
и откройте сводку. Отмена сохраняет историческую строку и освобождает слот.

```bash
docker compose ps
docker compose exec -T app python -m scripts.check_app --size small
docker compose -f compose.yaml run --rm -T app pytest -q
```

Для проверки WORKING замените `--size small` на `--size working`.
`check_app` создаёт и удаляет свои временные записи; последовательность id продвигается.
Перед будущими измерениями восстановите seed. Тесты используют отдельную временную
схему PostgreSQL и не меняют рабочие данные.

Сохранённый итог 08.10.2026: `60 passed, 1 warning in 15.11s`, код 0
([полный вывод](docs/dz1_pytest.txt)). Чистый запуск с новым томом в отдельном
Compose-проекте прошёл основные сценарии; WORKING и запись №100001 сохранены
([протокол](docs/dz1_clean_start.txt)).

## Программный интерфейс

[Полный контракт десяти операций](docs/api.md): методы, пути, параметры,
форматы ответов, коды ошибок и авторизация. Дополнительно доступны `/docs` и `/openapi.json`.

[Модель данных и правила](docs/data_model.md),
[сохранённые результаты и команды измерений](docs/measurements.md),
[отчёт](docs/ДЗ1_Родионов_Сергей_отчёт.docx),
[чек-лист требований](docs/dz1_checklist.md),
[материалы для Moodle](docs/dz1_moodle_submission.md).
