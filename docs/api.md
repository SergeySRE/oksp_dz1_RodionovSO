# Контракт JSON API

Тело POST — JSON с Content-Type: application/json.
После входа клиент передаёт полученную cookie; браузер делает это автоматически.
Все операции, кроме входа, требуют авторизации.

| Метод и путь | Параметры / тело | Успешный ответ | Ошибки |
|---|---|---|---|
| POST /api/auth/login | login, password | 200: id, login, full_name; Set-Cookie | 401, 422 |
| POST /api/auth/logout | нет | 204 без тела; удаление cookie | 401 |
| GET /api/services | нет | 200: массив id, name, duration_minutes | 401 |
| GET /api/specialists | service_id > 0, необязательный | 200: массив id, full_name, specialization; услуга без специалистов даёт [] | 401, 422 |
| GET /api/slots | service_id, date_from, date_to; specialist_id; page=1, size=20 | 200: items свободных слотов, total, page, size | 401, 404, 422 |
| GET /api/appointments | page=1, size=20; status, specialist_id, service_id, date_from, date_to | 200: items записей, total, page, size | 401, 422 |
| GET /api/appointments/{id} | положительный id | 200: полная запись | 401, 404, 422 |
| POST /api/appointments | slot_id, client_name, client_contact | 201: полная созданная запись | 401, 404, 409, 422 |
| POST /api/appointments/{id}/cancel | положительный id | 200: полная отменённая запись; повторный вызов возвращает то же время отмены | 401, 404, 422 |
| GET /api/summary | date_from, date_to | 200: показатели сводки | 401, 422 |

Параметры дат — YYYY-MM-DD; date_to не раньше date_from.
Идентификаторы положительные, page ≥ 1, 1 ≤ size ≤ 100.
status — booked или cancelled. Длина ФИО и контакта 1–200 символов после удаления пробелов по краям.
Неизвестные поля POST отклоняются с 422.

Полная запись:
`id, status, client_name, client_contact, created_at, cancelled_at,`
`slot: {id, starts_at, ends_at}, specialist: {id, full_name, specialization},`
`service: {id, name, duration_minutes}, created_by: {id, full_name}`.
Список возвращает тот же формат каждой записи.

Свободный слот:
`id, starts_at, ends_at, specialist: {id, full_name}, service: {id, name, duration_minutes}`.

Сводка:
`date_from, date_to, total_appointments, cancelled_appointments, cancellation_percent,`
`specialists: [{specialist_id, full_name, scheduled_minutes, booked_minutes, load_percent}]`.

Даты и время JSON — ISO 8601 с часовым поясом; cancelled_at может быть null.
Список записей сортируется по starts_at DESC, id DESC;
свободные слоты — starts_at ASC, id ASC; справочники и специалисты сводки — id ASC.
total относится ко всему отфильтрованному набору; страница за его концом содержит пустой items.
Отсутствующие объекты при чтении карточки, бронировании и отмене дают 404.
Несуществующие идентификаторы в фильтрах списка дают пустой результат.
В поиске слотов отсутствующая услуга или специалист дают 404.
409 означает занятый слот, пересечение с записью или несоответствие длительности.

Ошибка предметной операции имеет вид `{"detail": "сообщение"}`.
Ошибка валидации FastAPI — `{"detail": [{loc, msg, type, ...}]}`.
При некорректных параметрах и отсутствии входа порядок проверки не считается гарантией:
корректный неавторизованный запрос получает 401, некорректный может получить 422.
Автоматическая схема: /openapi.json; интерактивная документация: /docs.

HTML-страницы — клиентский интерфейс, отдельный дублирующий контракт для них не ведётся.
Jinja2 вызывает общую бизнес-логику, минимальный JavaScript отправляет изменения в JSON API.
