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
- **Handlers внешней SD** (`TICKET_HANDLERS` / `--ticket-handler`) — регистрация заявок из Python
- **Плагины** Jira, Redmine, Freshdesk, ServiceNow, SimpleOne, Naumen, ELMA365, Битрикс24, HP Service Manager — автоматически из `.env` (см. [ниже](#плагины-внешних-service-desk))

---

## Установка в закрытом контуре

[Инструкция для администраторов](docs/offline-install.md): подготовка готового Docker-образа с зависимостями, перенос без интернета, установка из wheel-файлов, настройка Oracle, резервное копирование и обновление. Сборка комплекта: `bash scripts/build-offline-bundle.sh linux/amd64 <тег-версии>`.

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

Однократный проход worker (CLI **внутри образа**, на хосте `alarm-manager-worker` не будет, если не делали `pip install .`):

```bash
docker compose run --rm worker alarm-manager-worker --once --active --responsible --tickets
```

`SERVER_URL` для этого запуска берётся из `docker-compose.yml` (`http://server:4800`); контейнер `server` должен уже работать.

Локально на хосте (рядом с Docker-сервером на порту 4800):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install .
alarm-manager-worker --once --active --responsible --tickets --server-url http://127.0.0.1:4800
# без установки в PATH:
python3 -m alarm_manager_server.worker --once --active --responsible --tickets --server-url http://127.0.0.1:4800
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
| `TICKETS_FILE` | Локально по умолчанию `~/.local/share/alarm-manager/tickets.json`; в Docker — `/var/lib/alarm-manager/tickets.json` (см. `.env`) |

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

**Локальный venv:** если в `.env` скопирован Docker-путь `TICKETS_FILE=/var/lib/...`, будет `Permission denied` — удалите строку или укажите `TICKETS_FILE=~/.local/share/alarm-manager/tickets.json`, либо `--tickets-file ./data/tickets.json`.

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

### Плагины внешних Service Desk

Каталог [`alarm_manager_server/plugins/`](alarm_manager_server/plugins/): интеграции включаются автоматически при заполнении обязательных переменных в `.env`. Работают с `--tickets`.

| Плагин | Обязательные переменные | Поведение |
|--------|-------------------------|-----------|
| **Jira** | `JIRA_BASE_URL`, `JIRA_USER`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` | CREATE → issue; UPDATE/CLOSE → комментарий; опционально transition |
| **Redmine** | `REDMINE_BASE_URL`, `REDMINE_API_KEY`, `REDMINE_PROJECT_ID` | CREATE → issue; UPDATE/CLOSE → journal |
| **Freshdesk** | `FRESHDESK_BASE_URL`, `FRESHDESK_API_KEY`, `FRESHDESK_REQUESTER_EMAIL` | CREATE → ticket; UPDATE/CLOSE → note |
| **ServiceNow** | `SERVICENOW_INSTANCE_URL` + (`SERVICENOW_USER`/`PASSWORD` или `OAUTH_TOKEN`) | CREATE → incident; UPDATE/CLOSE → `work_notes`; CLOSE → state |
| **SimpleOne** | `SIMPLEONE_BASE_URL`, `SIMPLEONE_API_TOKEN`, `SIMPLEONE_CALLER` | CREATE → `itsm_incident`; UPDATE/CLOSE → `work_notes` (PATCH) |
| **Naumen** | `NAUMEN_BASE_URL`, `NAUMEN_ACCESS_KEY`, `NAUMEN_CLIENT`, `NAUMEN_CLIENT_EMPLOYEE`, `NAUMEN_AGREEMENT`, `NAUMEN_SERVICE` | CREATE → заявка (`create-m2m`); UPDATE → комментарий; CLOSE → `edit` (state + resultDescr) |
| **ELMA365** | `ELMA_BASE_URL`, `ELMA_API_TOKEN`, `ELMA_NAMESPACE`, `ELMA_APP_CODE` | CREATE → элемент приложения; UPDATE/CLOSE → сообщение в ленте; CLOSE → `set-status` |
| **Битрикс24** | `BITRIX24_WEBHOOK_URL`, `BITRIX24_RESPONSIBLE_ID` | CREATE → задача (`tasks.task.add`); UPDATE/CLOSE → комментарий; CLOSE → `tasks.task.complete` |
| **HP Service Manager** | `HPSM_BASE_URL`, `HPSM_USER`, `HPSM_PASSWORD` | CREATE → incident (`POST /incidents`); UPDATE/CLOSE → `JournalUpdates` (PUT); CLOSE → `Status` |

Пример `.env` для Jira Cloud:

```env
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_USER=bot@example.com
JIRA_API_TOKEN=your-api-token
JIRA_PROJECT_KEY=OPS
JIRA_ISSUE_TYPE=Task
JIRA_CLOSE_TRANSITION=Done
```

Пример для Redmine:

```env
REDMINE_BASE_URL=https://redmine.example.com
REDMINE_API_KEY=your-api-key
REDMINE_PROJECT_ID=42
REDMINE_TRACKER_ID=1
REDMINE_STATUS_CLOSED_ID=5
```

Пример для Freshdesk:

```env
FRESHDESK_BASE_URL=https://your-domain.freshdesk.com
FRESHDESK_API_KEY=your-api-key
FRESHDESK_REQUESTER_EMAIL=monitoring@example.com
FRESHDESK_PRIORITY=2
FRESHDESK_STATUS_OPEN=2
FRESHDESK_STATUS_CLOSED=5
```

Пример для ServiceNow:

```env
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_USER=integration_user
SERVICENOW_PASSWORD=...
SERVICENOW_CALLER_ID=sys_id_пользователя
SERVICENOW_CLOSE_STATE=7
```

Пример для SimpleOne:

```env
SIMPLEONE_BASE_URL=https://sandbox.dev.simpleone.ru
SIMPLEONE_API_TOKEN=...
SIMPLEONE_CALLER=155931135900000001
SIMPLEONE_CONTACT_TYPE=email
```

Пример для Naumen (ITSM 365):

```env
NAUMEN_BASE_URL=https://your-tenant.itsm365.com/sd
NAUMEN_ACCESS_KEY=8a5d0671-ae00-4870-8751-229ed963932b
NAUMEN_META_CLASS=serviceCall$serviceCall
NAUMEN_CLIENT=ou$1546201
NAUMEN_CLIENT_EMPLOYEE=employee$1545902
NAUMEN_AGREEMENT=agreement$1492401
NAUMEN_SERVICE=slmService$5602
NAUMEN_OFFICE=ou$2283501
NAUMEN_CLOSE_STATE=resolved
NAUMEN_CLOSE_CODE=resolved
```

UUID контрагента, сотрудника, контракта и услуги можно получить через REST `find/employee/{login}` и `get/agreement$…` (см. [документацию Naumen](https://nsdlab.ru/blog/api)). Ключ: `api.auth.getAccessKey('login')` в консоли SMP.

Пример для ELMA365:

```env
ELMA_BASE_URL=https://company.elma365.ru
ELMA_API_TOKEN=...
ELMA_NAMESPACE=service_desk
ELMA_APP_CODE=incident
ELMA_TITLE_FIELD=__name
ELMA_DESCRIPTION_FIELD=description
ELMA_CLOSE_STATUS=closed
ELMA_CONTEXT_EXTRA={"service":"network"}
```

Коды полей (`ELMA_TITLE_FIELD`, `ELMA_DESCRIPTION_FIELD`) смотрите в API-справке приложения в ELMA365. Токен: Администрирование → API. Документация: [api.elma365.com](https://api.elma365.com/ru/public-api/guides/IntroWebAPI/).

Пример для Битрикс24:

```env
BITRIX24_WEBHOOK_URL=https://portal.bitrix24.ru/rest/1/xxxxxxxxxxxxxxxx
BITRIX24_RESPONSIBLE_ID=1
BITRIX24_CREATED_BY=1
BITRIX24_COMPLETE_ON_CLOSE=true
```

Webhook создаётся в Битрикс24: **Разработчикам → Другое → Входящий webhook** (права: `task`, минимум `tasks` + `task`). `BITRIX24_RESPONSIBLE_ID` — ID пользователя-исполнителя.

Пример для HP Service Manager / Service Desk:

```env
HPSM_BASE_URL=https://sm.example.com:13080/SM/9/rest
HPSM_USER=integration.user
HPSM_PASSWORD=...
HPSM_IMPACT=3
HPSM_URGENCY=3
HPSM_CATEGORY=incident
HPSM_ASSIGNMENT_GROUP=Network
HPSM_CLOSE_STATUS=Closed
HPSM_CLOSURE_CODE=Solved Remotely
```

Оператору нужна capability **RESTful API**. Базовый URL — REST root (`/SM/9/rest`); список ресурсов: `GET {HPSM_BASE_URL}`. Документация: [Micro Focus REST API](https://docs.microfocus.com/SM/9.61/Hybrid/Content/webservicesguide/rest_syntax.htm).

`REDMINE_STATUS_CLOSED_ID=0` — при CLOSE только комментарий. `SERVICENOW_CLOSE_STATE` / `SIMPLEONE_CLOSE_STATE` пустые — при CLOSE только `work_notes`. `NAUMEN_CLOSE_STATE` / `NAUMEN_CLOSE_CODE` пустые — при CLOSE только `resultDescr`. `HPSM_CLOSE_STATUS` пустой — при CLOSE только `JournalUpdates`.

Id сохраняются в `TICKETS_FILE`: `external_ref` (номер заявки, напр. `INC0001234`) и `external_meta.sys_id` для последующих UPDATE/CLOSE.

### Комментарий в SAYMON после регистрации

После успешного **CREATE** во внешней системе worker добавляет комментарий в **активные** аварии группы (не history) через `POST /node/api/incidents/:id/comment`:

- включено по умолчанию: `TICKET_SAYMON_COMMENT_ENABLED=true`
- шаблон: `TICKET_SAYMON_COMMENT_TEMPLATE` (плейсхолдеры `{system}`, `{external_ref}`, `{local_ticket_id}`)
- на worker нужны те же **`SAYMON_LOGIN` / `SAYMON_PASSWORD` / `SAYMON_BASE_URL`**, что и у server (прямой вызов API ЦП)
- повторный комментарий на тот же инцидент не отправляется (`external_meta.saymon_sd_comments`)

Модули: `plugins/jira.py`, `redmine.py`, `freshdesk.py`, `servicenow.py`, `simpleone.py`, `naumen.py`, `elma.py`, `bitrix24.py`, `hpsm.py`.

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
| `TICKET_HANDLERS` | Доп. handlers: `pkg.mod:Handler` (поверх плагинов) |
| `JIRA_*` | Jira: URL, user, token, project, issue type, transition (см. `.env.example`) |
| `REDMINE_*` | Redmine: URL, API key, project, tracker, closed status id |
| `FRESHDESK_*` | Freshdesk |
| `SERVICENOW_*` | ServiceNow Table API |
| `SIMPLEONE_*` | SimpleOne Table API |
| `NAUMEN_*` | Naumen / ITSM 365 REST API |
| `ELMA_*` | ELMA365 Public API |
| `BITRIX24_*` | Битрикс24 incoming webhook (задачи) |
| `HPSM_*` | HP Service Manager REST API (incidents) |
| `TICKET_SAYMON_COMMENT_*` | Комментарий в SAYMON после CREATE во внешней SD |

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
├── plugins/                # Jira, Redmine, Freshdesk, ServiceNow, SimpleOne, Naumen, ELMA, Bitrix24, HPSM
│   ├── jira.py
│   ├── redmine.py
│   ├── freshdesk.py
│   ├── servicenow.py
│   ├── simpleone.py
│   ├── naumen.py
│   ├── elma.py
│   ├── bitrix24.py
│   ├── hpsm.py
│   └── registry.py
└── worker/
    ├── run.py              # CLI
    ├── formatter.py        # группы для консоли
    ├── tickets.py          # CREATE / UPDATE / CLOSE, TICKETS_FILE
    ├── ticket_handlers.py  # TICKET_HANDLERS, dispatch
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

## Oracle DB: запись тикета через функцию

Плагин `plugins/oracle.py` автоматически включается при заполнении `ORACLE_DSN`,
`ORACLE_USER`, `ORACLE_PASSWORD` и всех четырёх `ORACLE_ID_*`. Требуется worker
с `--tickets`. Драйвер `python-oracledb` входит в зависимости; после обновления
выполните `pip install .` или пересоберите Docker-образ.

```env
ORACLE_DSN=jdbc:oracle:thin:@//cist.garadj.com:1523/cist.garadj
ORACLE_USER=saymon
ORACLE_PASSWORD=<пароль>
ORACLE_ID_DEPT=215094
ORACLE_ID_BUILD=30
ORACLE_ID_DEF=11449
ORACLE_ID_MONIT=1
ORACLE_LOCATION=КИС
ORACLE_EVENT=created
ORACLE_TIMEZONE=Europe/Moscow
```

Пароль задаётся только в локальном `.env`. Поддерживается также DSN вида
`//host:port/service`. Драйвер работает в Thin-режиме.

Выполняется `SELECT REPAIR.REP_MONIT_SYSTEM_CURS(...) FROM dual`, все десять
аргументов передаются через bind-параметры. Успешная запись подтверждается
`commit`; ошибка вызывает `rollback` и обрабатывается стандартным механизмом
ошибок handlers.

| Аргумент функции | Источник |
|---|---|
| `P_B_DATE` | `ORACLE_B_DATE`, иначе время создания локального тикета |
| `P_E_DATE` | `ORACLE_E_DATE`, иначе время обновления (CREATE) или закрытия (CLOSE) локального тикета |
| `P_ID_DEPT`, `P_ID_BUILD`, `P_ID_DEF`, `P_ID_MONIT` | Соответствующие `ORACLE_ID_*` |
| `P_NAME_EQUIP` | `ORACLE_NAME_EQUIP`, иначе заголовок группы |
| `P_NAME_DEFECT` | `ORACLE_NAME_DEFECT`, иначе текст группы / тексты аварий из снимка |
| `P_EXECUTED_WORK` | `ORACLE_EXECUTED_WORK` (по умолчанию пусто) |
| `P_LOCATION` | `ORACLE_LOCATION` |

Даты передаются строками `DD.MM.YYYY HH:MM` в `ORACLE_TIMEZONE` (по умолчанию
`Europe/Moscow`). Это даты жизненного цикла локального тикета, а не время начала
исходной аварии. На CREATE начало и конец обычно совпадают. Для записи после
закрытия укажите `ORACLE_EVENT=closed`. UPDATE не вызывает повторную запись.
Выполненные работы задаются явно; автоматически сведения о ремонте не формируются.
Фиксированные даты `ORACLE_B_DATE` / `ORACLE_E_DATE` предназначены, в частности,
для воспроизведения тестового запроса.

Ожидаемый результат функции: скалярный номер тикета (строка/целое число) либо
REF CURSOR. Для REF CURSOR задайте `ORACLE_RESULT_ID_COLUMN` — имя колонки с номером
тикета. Без этой настройки непустой курсор считается подтверждением выполнения,
но внешний номер не выдумывается и комментарий с ним в SAYMON не создаётся.
Пустой результат считается ошибкой. Схему курсора и семантику возможных кодов
ошибок необходимо сверить с контрактом функции: плагин не может отличить
скалярный код ошибки от номера тикета без этого контракта.

После успеха в `external_meta.oracle_recorded` сохраняется отметка, предотвращающая
повторную запись при повторной обработке сохранённого тикета. Это не обеспечивает
exactly-once при сбое между commit Oracle и сохранением локального файла.
Автоматического повтора после ошибки без нового события в текущем worker нет.

Таймаут соединения: `ORACLE_CONNECT_TIMEOUT_SEC=30`, ожидания результата и commit после подключения:
`ORACLE_CALL_TIMEOUT_MS=30000`.
