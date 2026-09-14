# Установка Alarm Manager в закрытом контуре

## 1. Что согласовать перед поставкой

- ОС и архитектура сервера: Linux x86_64 (`linux/amd64`) или ARM64 (`linux/arm64`).
- Допускается ли Docker Engine с Compose v2. Их установочные пакеты и системные
  зависимости предоставляет администратор под свою ОС; в образ приложения они не входят.
- Внутренние адреса SAYMON и Oracle, DNS, маршрутизация и доверенные сертификаты HTTPS.
- Доступ от контейнера worker к Oracle `cist.garadj.com:1523`, от API и worker к SAYMON,
  от worker к API на 4800. Интернет для работы не нужен.
- Версия Oracle и совместимость учётной записи с python-oracledb Thin.
  Текущий коннектор не включает Thick-режим. Для Oracle 11g или требований к
  Oracle Client необходима отдельная адаптация, одного копирования библиотек недостаточно.
- С DBA: право выполнения `REPAIR.REP_MONIT_SYSTEM_CURS`, типы её аргументов,
  формат результата, коды ошибок и допустимость записи через `SELECT ... FROM dual`.

Драйвер в Thin-режиме не требует Oracle Instant Client:
[документация Oracle](https://python-oracledb.readthedocs.io/en/latest/user_guide/installation.html).
Не переносите `.venv` с macOS/Windows на Linux: бинарные зависимости зависят от платформы.

## 2. Подготовка Docker-комплекта вне закрытого контура

На машине с интернетом, Docker и исходниками проекта:

```bash
bash scripts/build-offline-bundle.sh linux/amd64 2026.09.14-1
```

Для ARM64 замените платформу на `linux/arm64`. При сборке для другой архитектуры
сборочный узел должен поддерживать эмуляцию Docker либо используйте узел нужной
архитектуры. Скрипт собирает образ, проверяет зависимости и импорт приложения
с `--network none`, сохраняет образ и формирует SHA-256. Это проверка комплекта,
а не интеграционный тест SAYMON/Oracle.

Результат: `dist/offline-2026.09.14-1-amd64/`:

- `image.tar` — приложение, Python, ОС образа и Python-библиотеки;
- `compose.yml` — запуск без сборки и скачивания образов;
- `.env.example` — шаблон без паролей;
- `python-packages.txt`, `image-inspect.json` — версии пакетов и сведения об образе;
- `docs/`, `SHA256SUMS` — инструкция и контрольные суммы.

Перенесите **весь каталог**, включая скрытый `.env.example`, разрешённым способом.
Сохраняйте исходный комплект каждой версии: повторная сборка из исходников может
разрешить более новые зависимости. Для повторной установки используйте тот же
проверенный архив, а для обновления — новый уникальный тег.

Механизм переноса образов описан в
[Docker image save](https://docs.docker.com/reference/cli/docker/image/save/).

## 3. Установка Docker-комплекта в контуре

Команды выполняются учётной записью с доступом к Docker. Поместите комплект,
например, в `/opt/alarm-manager-server`, затем:

```bash
cd /opt/alarm-manager-server
sha256sum -c SHA256SUMS
docker load --input image.tar
cp .env.example .env
chmod 600 .env
```

Заполните `.env` текстовым редактором:

```env
SAYMON_BASE_URL=https://saymon.internal.example
SAYMON_LOGIN=<логин API>
SAYMON_PASSWORD=<пароль API>
ORACLE_DSN=jdbc:oracle:thin:@//cist.garadj.com:1523/cist.garadj
ORACLE_USER=saymon
ORACLE_PASSWORD=<пароль БД>
ORACLE_ID_DEPT=215094
ORACLE_ID_BUILD=30
ORACLE_ID_DEF=11449
ORACLE_ID_MONIT=1
ORACLE_LOCATION=КИС
ORACLE_EVENT=created
ORACLE_TIMEZONE=Europe/Moscow
```

Сохраните строку `ALARM_MANAGER_IMAGE`, добавленную сборщиком. Не используйте
`localhost` для удалённых SAYMON/Oracle: внутри контейнера это сам контейнер.
Плагин включается, когда заданы подключение, учётные данные и все четыре ID.

```bash
docker compose -f compose.yml config --quiet
docker compose -f compose.yml up -d --no-build --pull never
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail=100 server worker
curl --fail http://127.0.0.1:4800/health
```

Используйте именно поставляемый `compose.yml`: он не содержит `build` и запрещает
pull. Обычный `docker-compose.yml` из исходников рассчитан на сборку с интернетом.
`/health` подтверждает доступность API; доступ к SAYMON и запись в Oracle этим не проверяются.
Worker автоматически начнёт обработку аварий и создание тикетов после запуска.

## 4. Аргументы Oracle и проверка интеграции

| Переменная | Назначение |
|---|---|
| `ORACLE_ID_DEPT` | `P_ID_DEPT`, подразделение |
| `ORACLE_ID_BUILD` | `P_ID_BUILD`, здание |
| `ORACLE_ID_DEF` | `P_ID_DEF`, дефект |
| `ORACLE_ID_MONIT` | `P_ID_MONIT`, идентификатор мониторинга |
| `ORACLE_NAME_EQUIP` | `P_NAME_EQUIP`; пусто — заголовок группы |
| `ORACLE_NAME_DEFECT` | `P_NAME_DEFECT`; пусто — описание группы |
| `ORACLE_EXECUTED_WORK` | `P_EXECUTED_WORK`; реальные работы задаются явно |
| `ORACLE_LOCATION` | `P_LOCATION` |
| `ORACLE_B_DATE`, `ORACLE_E_DATE` | Фиксированные даты `DD.MM.YYYY HH:MM`; обычно оставлять пустыми |
| `ORACLE_TIMEZONE` | Часовой пояс дат; по умолчанию `Europe/Moscow` |
| `ORACLE_EVENT` | `created` — при создании; `closed` — при закрытии |
| `ORACLE_RESULT_ID_COLUMN` | Колонка номера заявки в возвращённом REF CURSOR |
| `ORACLE_CONNECT_TIMEOUT_SEC` | Таймаут подключения, по умолчанию 10 секунд |
| `ORACLE_CALL_TIMEOUT_MS` | Таймаут обращения к БД, по умолчанию 30000 мс |

Без фиксированных дат передаются времена создания и обновления/закрытия
**локального тикета**. При CREATE начало и конец обычно совпадают. Для записи
после закрытия установите `ORACLE_EVENT=closed`. UPDATE ничего не записывает.

Скалярный результат считается номером тикета. Для REF CURSOR укажите колонку
номера заявки. Без неё непустой курсор подтверждает выполнение, но внешний номер
не сохраняется. Если функция возвращает статус/код ошибки вместо номера,
потребуется адаптация разбора результата по контракту DBA.

При приёмке создайте согласованную тестовую аварию в SAYMON, дождитесь прохода
worker, проверьте новую запись с DBA и `external_meta.oracle_recorded` в
`/var/lib/alarm-manager/tickets.json` контейнера worker. Для настроенного номера
также проверьте `external_meta.external_refs.oracle`. В режиме `closed` сначала
нужно закрыть тестовую аварию. Не удаляйте файл тикетов для повторного теста:
worker может создать дубликаты во внешних системах.

После успешного CREATE с внешним номером по умолчанию добавляется комментарий
в SAYMON. Это можно отключить: `TICKET_SAYMON_COMMENT_ENABLED=false`.

## 5. Обновление, резервное копирование, восстановление

Состояние хранится в именованных Docker-томах. Используйте постоянный проект
Compose `alarm-manager` из поставляемого файла. Не запускайте второй worker
с отдельным состоянием для тех же аварий.

Перед обновлением остановите worker, скопируйте состояние и сохраните `.env`:

```bash
mkdir -p backup
docker compose -f compose.yml stop worker
docker compose -f compose.yml cp worker:/var/lib/alarm-manager/tickets.json backup/tickets.json
cp .env backup/config.env
chmod 600 backup/config.env
```

Если тикетов ещё не было, файл может отсутствовать. Проверьте наличие и целостность
копии перед обновлением. Кеш API восстанавливается из SAYMON и не обязателен для
восстановления учёта тикетов.

Проверьте SHA256SUMS нового комплекта в отдельном каталоге, загрузите новый
`image.tar`, измените `ALARM_MANAGER_IMAGE` в рабочем `.env` на новый тег и выполните
`docker compose -f compose.yml up -d --no-build --pull never`. Не перезаписывайте
рабочий `.env` шаблоном новой версии; перенесите только новые необходимые параметры.

Для отката используйте сохранённый предыдущий образ и его тег. Перед возвратом
старого `tickets.json` сверяйте изменения с Oracle: восстановление старой копии
может потерять отметки о созданных заявках и вызвать дубликаты. Для переноса
состояния на новый узел сначала создайте контейнеры командой `docker compose -f
compose.yml create --no-build --pull never`, скопируйте файл в worker через
`docker compose -f compose.yml cp backup/tickets.json worker:/var/lib/alarm-manager/tickets.json`,
затем запускайте сервисы.

Не выполняйте `docker compose down -v`: флаг `-v` удалит тома с состоянием.

## 6. Без Docker: автономная установка из wheel-файлов

На целевом сервере должны быть установлены Python **3.11+**, модуль `venv`, pip,
системная база часовых поясов и необходимые системные библиотеки. Пакеты ОС
подготовьте средствами своего дистрибутива. Этот вариант не поставляет сам Python.

Готовьте комплект на Linux с той же архитектурой, версией Python и совместимой
libc (предпочтительно на копии целевой ОС). На машине с интернетом в исходниках:

```bash
python3.11 -m venv /tmp/alarm-build
/tmp/alarm-build/bin/python -m pip install --upgrade pip
mkdir -p dist/wheelhouse
/tmp/alarm-build/bin/python -m pip wheel --wheel-dir dist/wheelhouse .
python3.11 -m venv /tmp/alarm-offline-check
/tmp/alarm-offline-check/bin/python -m pip install --no-index --find-links=dist/wheelhouse alarm-manager-server
/tmp/alarm-offline-check/bin/python -m pip check
/tmp/alarm-offline-check/bin/python -c 'import oracledb; import alarm_manager_server.worker.run'
/tmp/alarm-offline-check/bin/python -m pip freeze > dist/python-packages.txt
```

Используйте новый пустой каталог wheelhouse для каждой поставки. `pip wheel`
собирает приложение и зависимости; в контуре установка идёт из локальных wheels,
без сборочных инструментов и индекса пакетов:
[документация pip](https://pip.pypa.io/en/stable/cli/pip_wheel/).

Перенесите wheelhouse, `.env.example`, `docs/`, `deploy/systemd/` и список версий.
Создайте контрольные суммы файлов комплекта и проверьте их после переноса.
В `/opt/alarm-manager-server` на целевом сервере:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links=wheelhouse alarm-manager-server
.venv/bin/python -m pip check
cp .env.example .env
chmod 600 .env
```

Заполните подключения как в разделе 3, без `ALARM_MANAGER_IMAGE`. Укажите:

```env
SERVER_URL=http://127.0.0.1:4800
CACHE_DIR=/var/cache/alarm-manager
TICKETS_FILE=/var/lib/alarm-manager/tickets.json
```

Создайте системного пользователя `alarm` и каталоги `/var/cache/alarm-manager`,
`/var/lib/alarm-manager` с правом записи для него. Рабочий каталог и `.env` должны
быть доступны пользователю `alarm` на чтение. От имени root установите unit-файлы:

```bash
cp deploy/systemd/alarm-manager-server.service /etc/systemd/system/
cp deploy/systemd/alarm-manager-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now alarm-manager-server alarm-manager-worker
journalctl -u alarm-manager-worker -n 100 --no-pager
```

Worker unit запускается с `--active --responsible --tickets`. Остановка перед
резервным копированием: `systemctl stop alarm-manager-worker`; копируйте
`TICKETS_FILE` и `.env`, затем `systemctl start alarm-manager-worker`.

## 7. Типовые неисправности

| Симптом | Проверить |
|---|---|
| Docker пытается скачать образ / образ не найден | Используется поставляемый Compose; `docker load` выполнен; тег в `.env` совпадает |
| `exec format error` | Архитектура образа соответствует серверу |
| `No matching distribution` при офлайн-установке | Полнота wheelhouse, версия Python, архитектура и libc |
| Oracle-плагин не включён | Заполнены DSN, логин, пароль и все четыре ID; в логах есть `ticket plugin enabled: oracle` |
| Ошибка подключения Oracle | DNS/маршруты/порт 1523 именно из контейнера, service name, учётная запись и режим Thin |
| Ошибка часового пояса | Системная база timezone установлена; `ORACLE_TIMEZONE` корректен |
| `ORA-14551` | Функция выполняет DML из SELECT; DBA должен подтвердить способ вызова, может понадобиться PL/SQL-вызов |
| Запись не появилась после исправления ошибки | Worker не повторяет неудачный CREATE автоматически без нового события; сверить состояние перед ручным повтором |
| В БД есть запись, локальной отметки нет | Возможен сбой между commit Oracle и сохранением файла; сверить с DBA до повторной записи |

Не отправляйте `.env` с паролями вместе с диагностикой. При обращении приложите
версию комплекта, обезличенные логи и тип/пример результата функции Oracle.
