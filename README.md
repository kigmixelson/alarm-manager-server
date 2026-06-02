# Alarm Manager Server

Серверная часть для группировки аварий SAYMON и определения ответственных. Логика соответствует web-приложению [alarm-manager](https://github.com/) (TypeScript): owner/class/synthetic grouping, макросы по цепочке предков.

**Сервис может работать на отдельном узле** — не на сервере, где развёрнут Программный комплекс «Центральный Пульт» и SAYMON. Достаточно сетевого доступа к API ЦП (`SAYMON_BASE_URL`, учётная запись). Удобно вынести группировку и worker на выделенную ВМ, в контур service desk или в DMZ, не нагружая центральный узел мониторинга.

Состоит из двух процессов:

| Компонент | Назначение |
|-----------|------------|
| **API** (`alarm-manager-server`) | Загрузка аварий из SAYMON, группировка, резолв макросов; HTTP на порту **4800** |
| **Worker** (`alarm-manager-worker`) | Опрос `POST /process`, отчёт в stdout; с `--tickets` — учёт групп между запусками |

---

## Возможности

- **Группировка по owner** — аварии одного объекта мониторинга (активные + история)
- **Группировка по классу предка** — обход вверх по иерархии, контейнеры Host / Router / Local Address
- **Синтетические группы** — общий заголовок, если у предка нет своей аварии (≥2 дочерних)
- **Приоритет owner** над class-группировкой при слиянии
- **Ответственные** — макросы вида `{{parent[class.id=...].properties[...]}}`
- **Тикеты групп** (`--tickets`) — между запусками worker: создать / обновить / закрыть «тикет» на каждую группу; состояние в `TICKETS_FILE` (см. [ниже](#тикеты-между-запусками-worker---tickets))
- **Handlers внешней SD** (`TICKET_HANDLERS` / `--ticket-handler`) — регистрация заявок в service desk из Python

---

## Требования

- Python **3.11+** (локальный запуск) или Docker / Docker Compose
- **Сетевой доступ** к API SAYMON на узле ЦП (`SAYMON_BASE_URL`, `SAYMON_LOGIN`, `SAYMON_PASSWORD`) — установка на том же хосте, что и ЦП, **не обязательна**

Перед запросами к SAYMON выполняется `POST /node/api/users/session` (cookies `sid`, `csrf`), опционально — `SAYMON_AUTH_REDIRECT_URL`. Дальнейшие запросы идут с `Cookie` и `x-csrf-token`.

### Развёртывание вне узла «Центральный Пульт»

Типовая схема:

```
  ┌─────────────────────────────┐         HTTPS/API          ┌──────────────────────────────┐
  │  Узел ЦП (Центральный       │  ◄────────────────────────  │  Отдельная ВМ / контейнер    │
  │  Пульт, SAYMON, веб UI)     │      только чтение аварий   │  Alarm Manager Server        │
  └─────────────────────────────┘                             │  + worker → логи, SD, боты   │
                                                              └──────────────────────────────┘
```

- В `.env` на внешнем узле укажите `SAYMON_BASE_URL=https://<хост-цп>/...` (доступный с этой ВМ).
- В Docker на внешней ВМ — `SAYMON_BASE_URL=http://host.docker.internal:...` только если API ЦП на **хосте** рядом с Docker; для удалённого ЦП — обычный URL по сети.
- Веб [«Менеджер Аварий»](https://alm.cpult.ru/) остаётся на стороне ЦП; этот репозиторий — **внешний** потребитель API и поставщик свёрнутых отчётов.

Плюсы выноса: разгрузка центрального узла, изоляция фоновой обработки, размещение рядом с интеграциями (почта, тикеты, мониторинг логов worker).

---

## Быстрый старт (Docker)

Рекомендуемый способ: API и worker в одном `docker compose`.

```bash
cd alarm-manager-server
cp .env.example .env
# Заполните SAYMON_BASE_URL, SAYMON_LOGIN, SAYMON_PASSWORD (URL узла ЦП, доступный с этой ВМ)
# SAYMON на другой машине — полный https://... ; только если API на хосте рядом с Docker:
#   SAYMON_BASE_URL=http://host.docker.internal:8080

docker compose up -d --build
docker compose logs -f worker
```

| Сервис | Роль | Снаружи |
|--------|------|---------|
| `server` | FastAPI | `http://localhost:4800` (переменная `SERVER_PORT` меняет порт на хосте) |
| `worker` | Периодический опрос API, вывод в лог; по умолчанию `--active --responsible --tickets` | том `alarm-manager-data` → `/var/lib/alarm-manager` (тикеты + каталог данных) |

Worker в compose пишет тикеты в `/var/lib/alarm-manager/tickets.json` (переменная `TICKETS_FILE`). Без персистентного тома при пересоздании контейнера история тикетов обнуляется.

Проверка API:

```bash
curl -s http://localhost:4800/health
curl -s -X POST "http://localhost:4800/process?resolve_macros=false" | head
```

Только API, без worker:

```bash
docker compose up -d server
```

Пересборка после изменений кода:

```bash
docker compose up -d --build
```

---

## Автозапуск после загрузки ВМ

Чтобы API и worker поднимались сами после перезагрузки виртуальной машины, используйте **systemd**. Ниже — два варианта; для production на ВМ обычно удобнее **Docker**.

Перед включением автозапуска один раз вручную проверьте, что стек работает (`docker compose up -d` или локальный запуск) и заполнен `.env`.

### Вариант A: Docker Compose (рекомендуется)

1. Разместите репозиторий, например, в `/opt/alarm-manager-server` (рядом должны лежать `docker-compose.yml` и `.env`).

2. Скопируйте unit-файл:

```bash
sudo cp deploy/systemd/alarm-manager-docker.service /etc/systemd/system/
```

При другом пути к проекту отредактируйте `WorkingDirectory=` в unit-файле. Если `docker compose` не в `/usr/bin/docker`, укажите полный путь (`which docker`).

3. Включите автозапуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable alarm-manager-docker.service
sudo systemctl start alarm-manager-docker.service
```

Проверка:

```bash
sudo systemctl status alarm-manager-docker.service
docker compose -f /opt/alarm-manager-server/docker-compose.yml ps
curl -s http://localhost:4800/health
```

Логи worker: `docker compose -f /opt/alarm-manager-server/docker-compose.yml logs -f worker`.

Остановка / перезапуск:

```bash
sudo systemctl stop alarm-manager-docker.service
sudo systemctl restart alarm-manager-docker.service
```

После обновления кода: `cd /opt/alarm-manager-server && docker compose up -d --build` (или `systemctl restart alarm-manager-docker`).

### Вариант B: без Docker (venv на ВМ)

1. Установите проект в `/opt/alarm-manager-server`, создайте venv и пользователя (пример):

```bash
sudo useradd --system --home /opt/alarm-manager-server --shell /usr/sbin/nologin alarm || true
cd /opt/alarm-manager-server
sudo -u alarm python3 -m venv .venv
sudo -u alarm .venv/bin/pip install .
cp .env.example .env   # и настройте права: chown alarm:alarm .env
```

2. Скопируйте unit-файлы API и worker:

```bash
sudo cp deploy/systemd/alarm-manager-server.service /etc/systemd/system/
sudo cp deploy/systemd/alarm-manager-worker.service /etc/systemd/system/
```

При необходимости поправьте `User=`, `WorkingDirectory=` и пути к `ExecStart=`.

3. Включите автозапуск (worker стартует после API):

```bash
sudo systemctl daemon-reload
sudo systemctl enable alarm-manager-server.service alarm-manager-worker.service
sudo systemctl start alarm-manager-server.service alarm-manager-worker.service
```

Проверка:

```bash
sudo systemctl status alarm-manager-server.service
sudo systemctl status alarm-manager-worker.service
journalctl -u alarm-manager-worker.service -f
```

### Зависимости при загрузке

- Unit для Docker ждёт `docker.service` и сеть (`network-online.target`).
- Worker без Docker ждёт `alarm-manager-server.service`.
- Если SAYMON на **другой** машине, достаточно сети на ВМ; если SAYMON на **хосте**, а сервис в Docker — в `.env` укажите `SAYMON_BASE_URL=http://host.docker.internal:8080` (как в разделе Docker выше).

---

## Локальный запуск

Из корня репозитория (на macOS — `python3`, не `python`):

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
cp .env.example .env
# отредактируйте .env
```

### API-сервер

```bash
alarm-manager-server
# то же:
python3 -m alarm_manager_server
# с hot-reload:
python3 -m uvicorn alarm_manager_server.api.app:app --reload --host 0.0.0.0 --port 4800
```

Слушает **0.0.0.0:4800** (см. `SERVER_URL` в `.env` для worker).

### Worker

Отдельный терминал; сервер должен уже отвечать на `SERVER_URL` (по умолчанию `http://127.0.0.1:4800`).

```bash
# один проход
alarm-manager-worker --once

# только группы с хотя бы одной не-Cleared аварией
alarm-manager-worker --once --active

# с ответственными (включает резолв макросов на сервере)
alarm-manager-worker --once --responsible

# тикеты: первый проход создаёт, дальше обновляет/закрывает при изменениях
alarm-manager-worker --once --active --responsible --tickets

# цикл (интервал WORKER_INTERVAL_SEC в .env, по умолчанию 60 с)
alarm-manager-worker --responsible

# явный URL и интервал
python3 -m alarm_manager_server.worker --server-url http://127.0.0.1:4800 --interval 30 --responsible
```

Флаги worker:

| Флаг | Описание |
|------|----------|
| `--once` | Один цикл и выход (`--interval 0`) |
| `--active` | Не показывать группы, где все аварии Cleared |
| `--responsible` | Строка «ответственный» в группе; для одиночной аварии — ещё колонка в строке |
| `--no-macros` | Не резолвить макросы на сервере (несовместимо с `--responsible`) |
| `--tickets` | Учёт «тикетов» по группам между запусками (см. ниже) |
| `--tickets-file` | Путь к JSON с тикетами (по умолчанию `TICKETS_FILE` из `.env`) |
| `--ticket-handler` | Handler внешней системы `module:Class` (нужен `--tickets`; можно повторять) |
| `--server-url` | Базовый URL API (по умолчанию из `.env`) |
| `-v` | Подробные логи |

`MACROS` в `.env` должны совпадать с web (localStorage `ps-macros`).

---

## Формат вывода worker

### Обычный режим (без `--tickets`)

Между **группами** — пустая строка. В начале цикла — строка вида `--- <время> — N group(s), M incident row(s) ---`.

В каждой группе:

1. **Заголовок** — имя владельца (при необходимости с родителем в скобках).
2. **Статистика** — `первая: … | последняя: … | аварий: N` (полное число, даже если строки свёрнуты).
3. **Ответственный** (с `--responsible`) — одна строка на группу; в строках дочерних аварий колонка ответственного пустая.
4. **Строки аварий** (tab): `состояние | объект | когда открыта | когда закрыта | текст` — без id аварии.

Одинаковые аварии в группе (то же состояние, объект и текст), **3 и более** — в списке только **последняя**, строка `...`, **первая** (по времени открытия). Две одинаковые — обе строки.

Для незакрытых аварий колонка «когда закрыта» выровнена пробелами по ширине колонки.

### Режим тикетов (`--tickets`)

Вместо полного дампа всех групп печатаются только **события** по сравнению с прошлым запуском. Заголовок цикла:

`--- <время> — tickets: N open; +C ~U −L; M visible group(s), R row(s) ---`

где `+C` / `~U` / `−L` — число созданных, обновлённых и закрытых тикетов за этот проход.

Подробности — в разделе [Тикеты между запусками worker](#тикеты-между-запусками-worker---tickets).

---

## Тикеты между запусками worker (`--tickets`)

### Зачем

При каждом `alarm-manager-worker --once` (или в цикле) состав **групп** и **состояния** аварий могут меняться: новая авария на том же хосте, смена Warning → Critical, перегруппировка по owner/class, исчезновение из выборки API, все члены группы стали Cleared. Без учёта между запусками в лог снова попадает **полный снимок**, и интеграция с service desk не понимает, что изменилось.

Флаг **`--tickets`** вводит **локальные тикеты** (не путать с заявками SD): одна открытая запись `T-000001` на одну логическую **группу** отчёта worker. Между запусками worker сравнивает текущие группы с файлом `TICKETS_FILE` и выводит только **CREATE**, **UPDATE** или **CLOSE**.

Маркетинговое описание для заказчика и SD: [DESCRIPTION.md — раздел про тикеты](DESCRIPTION.md#тикеты-жизненный-цикл-группы-между-запусками).

### Жизненный цикл

```
  первый проход, новая группа          последующие проходы
         │                                    │
         ▼                                    ▼
    ┌─────────┐   изменение снимка      ┌─────────┐
    │ CREATE  │ ──────────────────────► │ UPDATE  │
    │ T-000042│   (состав, состояние,    │ T-000042│
    └─────────┘    ответственный…)      └────┬────┘
         │                                    │
         │         все Cleared / нет в API   │
         │         или смена группировки     │
         │                                    ▼
         │                              ┌─────────┐
         └────────────────────────────► │ CLOSE   │
                                        │ T-000042│
                                        └─────────┘
```

| Событие в логе | Когда срабатывает |
|----------------|-------------------|
| **`[CREATE T-…]`** | Группа впервые попала в учёт: новый `group_key` и нет открытого тикета с пересечением ≥50% по id аварий |
| **`[UPDATE T-…]`** | Найден тот же тикет, но изменился снимок: добавились/исчезли аварии, состояние, текст, объект, ответственный, заголовок или статистика |
| **`[CLOSE T-…]`** | Открытый тикет больше не соответствует активной группе (см. причины ниже) |

**Причины CLOSE** (строка `причина:` в выводе):

| Код в JSON | Текст в логе | Смысл |
|------------|--------------|--------|
| `all_cleared` | все аварии Cleared | Все аварии группы в Cleared |
| `removed` | аварии отсутствуют в выборке | Id аварий больше нет в ответе API (лимиты, история) |
| `group_changed` | группа расформирована или изменила состав | Перегруппировка: другой `group_key`, нет пересечения с открытым тикетом |

Новые группы после CLOSE получают **новый** номер тикета (повторное открытие старого `T-…` не делается).

### Идентичность группы (`group_key`)

Один и тот же тикет привязан к стабильному ключу (не к заголовку на экране):

| Ситуация | `group_key` |
|----------|-------------|
| Синтетическая группа (контейнер без своей аварии) | `synth:__synth__<id>` |
| Несколько аварий на одном объекте-владельце | `owner:<entityId>` |
| Одиночная авария | `inc:<id аварии>` |

Если SAYMON перестроил дерево, но ≥50% id аварий совпадают с открытым тикетом, тикет **обновляется** (и при необходимости перепривязывается к новому `group_key`), а не дублируется.

### Режим `--active` и вывод

С **`--active`** в отчёт попадают только группы, где есть хотя бы одна не-Cleared авария. Учёт тикетов при этом ведётся по **полному** списку групп (включая all-Cleared): иначе нельзя корректно закрыть тикет, когда группа «погасла».

- **CREATE** / **UPDATE** печатаются только для групп, видимых с `--active`.
- **CLOSE** печатается всегда (в т.ч. когда группа стала полностью Cleared и пропала из активного списка).

Проход без изменений: `(no ticket changes)`.

### Конфигурация и хранение

| Переменная / флаг | Назначение |
|-------------------|------------|
| `--tickets` | Включить учёт между запусками |
| `--tickets-file` | Путь к JSON (перекрывает `TICKETS_FILE`) |
| `TICKETS_FILE` | По умолчанию `/var/lib/alarm-manager/tickets.json` |

Файл создаётся автоматически; запись атомарная (через временный файл). Структура верхнего уровня:

```json
{
  "next_seq": 43,
  "open_by_group_key": { "owner:abc123": "T-000012" },
  "tickets": {
    "T-000012": {
      "ticket_id": "T-000012",
      "group_key": "owner:abc123",
      "status": "open",
      "created_at": "2025-05-27T10:00:00+00:00",
      "updated_at": "2025-05-27T11:00:00+00:00",
      "closed_at": null,
      "close_reason": null,
      "snapshot": { "title": "…", "member_ids": ["…"], "members": { } }
    }
  }
}
```

В `snapshot` — заголовок, список id аварий и по каждой: состояние, текст, объект, ответственный, время (для diff).

**Docker:** том `alarm-manager-data` → `/var/lib/alarm-manager`; в `command` worker: `--active --responsible --tickets`.

### Пример вывода

```
--- 2025-05-27 12:00:00 UTC — tickets: 5 open; +1 ~2 −1; 4 visible group(s), 12 row(s) ---

[CREATE T-000003]
Router-A (Host)
первая: … | последняя: … | аварий: 2
ответственный: Иванов И.И.
…

[UPDATE T-000001] изменения: inc-77: состояние: warning → critical
…

[CLOSE T-000002] Old Group
  причина: все аварии Cleared
```

### Внешний handler (`TICKET_HANDLERS`)

Чтобы **регистрировать тикеты во внешней системе** (service desk, Jira, webhook), укажите Python-класс или функцию, возвращающую экземпляр с методом `on_ticket_event`:

| Способ | Пример |
|--------|--------|
| `.env` | `TICKET_HANDLERS=my_company.sd:ServiceDeskHandler` |
| CLI | `--ticket-handler my_company.sd:ServiceDeskHandler` (можно несколько раз) |
| Несколько | `TICKET_HANDLERS=handler1:H1,handler2:H2` |

Требуется **`--tickets`**. После `sync_tickets` worker вызывает handler для каждого события CREATE/UPDATE/CLOSE. Успешный ответ может записать во внутренний тикет:

- `external_ref` — id заявки во внешней системе (строка);
- `external_meta` — произвольный JSON (слияние по ключам).

**Контекст** (`TicketHandlerContext`): `event` (действие, изменения, текст группы), `ticket` (запись из `TICKETS_FILE`), `body_text` (форматированный блок группы для CREATE/UPDATE).

Удобная база — `BaseTicketHandler` с отдельными `on_created` / `on_updated` / `on_closed`. Встроенный отладочный handler:

```bash
alarm-manager-worker --once --tickets --ticket-handler \
  alarm_manager_server.worker.ticket_handlers:LoggingTicketHandler -v
```

Шаблон для своей SD: [`examples/ticket_handler_example.py`](examples/ticket_handler_example.py) (модуль должен быть в `PYTHONPATH` или установлен как пакет).

```python
from alarm_manager_server.worker.ticket_handlers import BaseTicketHandler, HandlerResult, TicketHandlerContext

class MyHandler(BaseTicketHandler):
    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        sd_id = create_issue(title=ctx.event.title, body=ctx.body_text)
        return HandlerResult(external_ref=sd_id)

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        update_issue(ctx.ticket["external_ref"], comment="; ".join(ctx.event.changes))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        close_issue(ctx.ticket["external_ref"], reason=ctx.event.close_reason)
        return None
```

Ошибка в handler логируется; остальные handlers и stdout не прерываются.

### Интеграция с service desk (без кода)

1. Парсить stdout: префиксы `[CREATE T-…]`, `[UPDATE T-…]`, `[CLOSE T-…]` и блок текста группы под CREATE/UPDATE.
2. Либо читать `TICKETS_FILE` после прохода worker (`ticket_id`, `external_ref`, `snapshot`, `close_reason`).
3. Либо реализовать **`TICKET_HANDLERS`** (см. выше) — предпочтительно для автоматического создания заявок.

Рекомендуемая связка для продакшена:

```bash
alarm-manager-worker --responsible --active --tickets \
  --ticket-handler my_company.sd:ServiceDeskHandler
```

---

## Конфигурация (`.env`)

Скопируйте `.env.example` → `.env`.

| Переменная | Назначение |
|------------|------------|
| `SAYMON_BASE_URL` | Базовый URL SAYMON |
| `SAYMON_LOGIN`, `SAYMON_PASSWORD` | Учётная запись API |
| `SAYMON_AUTH_REDIRECT_URL` | Опциональный GET после логина |
| `GROUP_BY_CLASS_NAMES`, `GROUP_BY_DEPTH` | Class-группировка |
| `MACROS`, `MACRO_DEPTH` | Макросы ответственных |
| `FETCH_LIMIT`, `HISTORY_LIMIT`, `FETCH_PAGE_SIZE` | Лимиты загрузки (с пагинацией). Если в UI аварий больше — увеличьте |
| `SERVER_URL` | URL API для worker (`http://127.0.0.1:4800` локально; в compose worker получает `http://server:4800`) |
| `WORKER_INTERVAL_SEC` | Период опроса worker |
| `SERVER_PORT` | Только Docker: порт на хосте для проброса (внутри контейнера — 4800) |
| `INCIDENT_LINK_TEMPLATE` | Шаблон ссылки; плейсхолдеры `{id}`, `{saymon_base_url}` |
| `CACHE_ENABLED` | Запись и чтение файлового кеша (`true` / `false`) |
| `CACHE_DIR` | Каталог JSON-файлов кеша (в Docker по умолчанию `/var/cache/alarm-manager`, смонтирован томом) |
| `CACHE_TTL_*_SEC` | Время жизни записи по типам данных (см. ниже); `0` — не использовать кеш для этого типа |
| `TICKETS_FILE` | JSON с тикетами worker (`--tickets`) |
| `TICKET_HANDLERS` | Handlers внешней SD через запятую: `pkg.mod:Handler` |

---

## Файловый кеш между запусками

Процесс API держит в памяти граф объектов SAYMON между запросами. При перезапуске контейнера или сервера эта память обнуляется. Чтобы **не обращаться к API ЦП за теми же данными** сразу после старта, включён файловый кеш в `CACHE_DIR`.

| Тип | Файл | Переменная TTL | По умолчанию | Содержимое |
|-----|------|----------------|--------------|------------|
| Аварии | `incidents.json` | `CACHE_TTL_INCIDENTS_SEC` | 120 с | Список аварий (active + history, дедуп по id) |
| Объекты | `objects.json` | `CACHE_TTL_OBJECTS_SEC` | 3600 с | Имена, классы, свойства, родители из инцидентов и API |
| Пути объектов | `object_paths.json` | `CACHE_TTL_OBJECT_PATHS_SEC` | 3600 с | Ответы `get_object_paths` по `entity_id` |
| Подписи состояний | `state_labels.json` | `CACHE_TTL_STATE_LABELS_SEC` | 86400 с | Словарь id уровня → имя |
| Class id | `class_ids.json` | `CACHE_TTL_CLASS_IDS_SEC` | 86400 с | Id классов для `GROUP_BY_CLASS_NAMES` |

Поведение:

- При **чтении** проверяется поле `saved_at` в JSON; если возраст записи больше TTL, кеш игнорируется и данные запрашиваются из SAYMON.
- После успешного `POST /process` и `POST /grouping` обновлённый снимок объектов записывается на диск; при остановке контейнера — ещё раз в shutdown.
- `CACHE_ENABLED=false` отключает запись и чтение файлов (остаётся только in-memory внутри одного процесса).
- Worker кеш не использует — только HTTP API сервера.

В `docker compose` для сервиса `server` смонтирован именованный том `alarm-manager-cache` → `/var/cache/alarm-manager`. Для bind-mount на хосте добавьте в `docker-compose.yml`, например: `./data/cache:/var/cache/alarm-manager`.

Очистка: удалите файлы в `CACHE_DIR` или `docker volume rm <project>_alarm-manager-cache` (имя тома зависит от имени каталога проекта).

---

## HTTP API

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Проверка работоспособности |
| GET | `/config` | Текущие настройки (без пароля) |
| POST | `/process` | Загрузка, группировка, опционально макросы (`?resolve_macros=true\|false`) |
| POST | `/grouping` | Только группировка |

Примеры:

```bash
curl -s http://localhost:4800/health
curl -s -X POST http://localhost:4800/process
curl -s -X POST "http://localhost:4800/process?resolve_macros=false"
```

При ошибке SAYMON или авторизации — JSON `{"detail": "..."}` (401/502), не пустой 500. Worker пишет `detail` в лог.

---

## Архитектура

```
alarm_manager_server/
├── api/app.py              # FastAPI
├── models/incident.py
├── services/
│   ├── processor.py        # fetch → group → macros
│   ├── grouping/           # owner, class, merge, synthetic
│   ├── macros/             # parser, resolver
│   └── owner_display.py
├── cache/                  # file_cache.py — JSON на диске с TTL
├── saymon/                 # client, object_store, auth
└── worker/
    ├── run.py              # CLI
    ├── formatter.py        # группы для консоли
    ├── tickets.py          # CREATE / UPDATE / CLOSE, TICKETS_FILE
    ├── ticket_handlers.py  # загрузка TICKET_HANDLERS, dispatch
    └── client.py           # HTTP к /process
```

---

## Соответствие frontend

| Frontend (TypeScript) | Server (Python) |
|----------------------|-----------------|
| `useOwnerGrouping.ts` | `services/grouping/owner.py` |
| `useIncidentGrouping.ts` | `services/grouping/class_ancestor.py` |
| `Index.tsx` merge | `services/grouping/merge.py` |
| `Index.tsx` synthetic | `services/grouping/synthetic.py` |
| `macroParser.ts` | `services/macros/parser.py` |
| `useMacroResolver.ts` | `services/macros/resolver.py` |
| `objectCache.ts` | `saymon/object_store.py` |

---

## Тесты

```bash
source .venv/bin/activate
python3 -m pytest
```
