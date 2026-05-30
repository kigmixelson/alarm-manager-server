# Alarm Manager Server

Серверное Python-приложение, реализующее логику группировки аварий и определения ответственных из клиентского приложения «Менеджер Аварий» (alarm-manager).

## Возможности

- **Группировка по owner** — сворачивает аварии одного объекта мониторинга (активные + исторические)
- **Группировка по классу предка** — BFS вверх по иерархии объектов, поиск контейнеров классов Host/Router/Local Address
- **Синтетические группы** — обёртки для предков без собственной аварии (≥2 детей)
- **Объединение группировок** — owner имеет приоритет над class-группировкой
- **Определение ответственных** — резолв макросов `{{parent[class.id=...].properties[...]}}` по цепочке предков

## Установка

```bash
# все команды — из корня репозитория alarm-manager-server
# на macOS часто доступен только python3, не python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
cp .env.example .env
# укажите SAYMON_BASE_URL, SAYMON_LOGIN, SAYMON_PASSWORD
```

Если вы в соседнем каталоге `alarm-manager`, сначала выполните `cd ../alarm-manager-server`.

Перед запросами к SAYMON клиент выполняет `POST /node/api/users/session`, сохраняет cookies `sid` и `csrf`, затем (опционально) открывает `SAYMON_AUTH_REDIRECT_URL`. Все API-запросы идут с `Cookie` и заголовком `x-csrf-token`, как в curl `-b` / `-H 'x-csrf-token: ...'`.

## Запуск

```bash
alarm-manager-server
# или
python3 -m alarm_manager_server
# или
python3 -m uvicorn alarm_manager_server.api.app:app --reload --port 8000
```

### Фоновый worker (группировка в консоль)

Отдельный процесс опрашивает `POST /process` и печатает группы:

1. **Заголовок** — имя владельца (с родителем в скобках, если есть).
2. **Статистика** — время первой/последней аварии в группе и их количество.
3. **Строки аварий** (через tab): состояние | **объект** | когда открыта | когда закрыта | текст (без id аварии). Одинаковые аварии (3 и более с тем же состоянием, объектом и текстом) сворачиваются: **последняя**, строка `...`, **первая**; в статистике группы по-прежнему полное число аварий.

Между группами — пустая строка. Для незакрытых аварий колонка «когда закрыта» заполняется пробелами по ширине колонки.

```bash
# один проход
alarm-manager-worker --once

# только группы с не-Cleared авариями
alarm-manager-worker --once --active

# с ответственными (строка в группе; для одиночной аварии — ещё и колонка в строке)
alarm-manager-worker --once --responsible

Убедитесь, что `MACROS` в `.env` совпадает с настройками в web-интерфейсе (localStorage `ps-macros`).

# цикл каждые 60 с (SERVER_URL / WORKER_INTERVAL_SEC в .env)
alarm-manager-worker

# или
python3 -m alarm_manager_server.worker --server-url http://127.0.0.1:8000 --interval 30
```

Шаблон ссылки: `INCIDENT_LINK_TEMPLATE` (плейсхолдеры `{id}`, `{saymon_base_url}`).

Если в web-интерфейсе аварий больше, чем в worker, увеличьте в `.env` лимиты `FETCH_LIMIT` / `HISTORY_LIMIT` (загрузка идёт постранично). Строка заголовка worker показывает число групп и строк аварий — сверяйте с UI.

## API

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Проверка работоспособности |
| GET | `/config` | Текущие настройки |
| POST | `/process` | Загрузить аварии, сгруппировать, определить ответственных |
| POST | `/grouping` | Только группировка без макросов |

### Пример

```bash
curl -X POST http://localhost:8000/process
```

При ошибке SAYMON или авторизации API вернёт JSON `{"detail": "..."}` (401/502), а не пустой 500. Worker выведет этот текст в лог.

**Проверка:** в `.env` должны быть `SAYMON_BASE_URL`, `SAYMON_LOGIN`, `SAYMON_PASSWORD`. `SAYMON_AUTH_REDIRECT_URL` опционален; при ошибке редиректа сервер продолжит работу, если cookies сессии уже получены.

## Архитектура

```
alarm_manager_server/
├── models/incident.py       # Модели данных
├── services/
│   ├── grouping/            # owner, class, merge, synthetic
│   ├── macros/              # parser, resolver
│   └── processor.py         # Оркестратор
├── saymon/                  # HTTP-клиент и object store
└── api/app.py               # FastAPI
```

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

## Тесты

```bash
python3 -m pytest
```
