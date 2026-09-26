# Секретный шёпот

Telegram-бот для секретных сообщений: inline-конверт в любом чате, открытие только отправителем и адресатом, группы, таймер самоуничтожения, анонимная отправка через `/send`.

Данные живут в Neon PostgreSQL. Процесс на GitHub Actions перезапускается по расписанию — после рестарта шёпоты, группы и контакты на месте.

## Что нужно от вас

Секреты кладутся в **GitHub → Settings → Secrets and variables → Actions**. В чат и в git их писать не нужно.

| Секрет | Обязателен | Откуда |
|---|---|---|
| `BOT_TOKEN` | да | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `DATABASE_URL` | да | [Neon](https://neon.tech) → Connection string |
| `FERNET_KEY` | да | `python scripts/generate_fernet_key.py` |
| `BACKUP_REPO_URL` | нет | URL приватного репозитория с токеном в адресе |
| `HEARTBEAT_URL` | нет | URL пинга healthchecks.io, если хотите внешний сигнал «бот жив» |

Без `BACKUP_REPO_URL` резервное копирование просто пропускается. Бот от этого не падает.

### База

1. Зарегистрируйтесь на [neon.tech](https://neon.tech) и создайте проект (регион любой, бесплатного тарифа хватает).
2. Скопируйте **Connection string**. Нужен обычный Postgres URL, не SQLAlchemy-форма:

```text
postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require
```

Подойдёт и pooled-адрес Neon (`-pooler` в хосте): бот отключает кэш prepared statements и сам включает SSL для `neon.tech`. `postgres://` тоже принимается. Параметры `sslmode` и `channel_binding` из строки убираются автоматически — asyncpg их в query не понимает.

3. Эту строку целиком положите в секрет `DATABASE_URL`.

SQLite годится только для локальной проверки. В GitHub Actions бот **не стартует**, если `DATABASE_URL` не начинается с `postgres`. Иначе каждый рестарт обнулял бы данные.

Миграции применяются сами при старте (`alembic upgrade head`). Отдельную команду на Neon выполнять не нужно.

### Ключ шифрования

Анонимный `/send` хранит `sender_id` не открытым текстом, а Fernet-шифром. Ключ один на все такие шёпоты.

```bash
python scripts/generate_fernet_key.py
```

Вывод — одна строка. Это секрет `FERNET_KEY`. После первых анонимных шёпотов ключ менять нельзя: отправитель перестанет узнавать свои сообщения. В репозиторий ключ не коммитьте.

### Бот в BotFather

После `/newbot`:

1. `/setinline` → выберите бота → плейсхолдер: `текст @username`
2. `/setinlinefeedback` → **Enabled**. Без этого Telegram не присылает `chosen_inline_result`, черновик не становится шёпотом, и кнопка «Открыть» ничего не найдёт.

Имя бота без `@` понадобится в ссылках вида `t.me/ИмяБота?start=w_...`.

## Запуск на GitHub Actions (около 10 минут)

1. Запушьте этот репозиторий на GitHub (если ещё не там).
2. Создайте проект Neon и секрет `DATABASE_URL`.
3. Сгенерируйте `FERNET_KEY` и сохраните его в секреты.
4. Создайте бота, включите inline и inline feedback, положите токен в `BOT_TOKEN`.
5. Откройте вкладку **Actions**. Если GitHub спросит — включите workflows для репозитория.
6. Запустите workflow **Whisper bot** вручную: *Run workflow*.

Дальше он сам перезапускается по cron `0 */5 * * *` (UTC). Лимит job — 350 минут, новый запуск отменяет предыдущий (`concurrency`), поэтому два процесса с одним токеном не опрашивают Telegram одновременно. Зависший процесс перезапускается тем же ручным *Run workflow*.

Проверка: откройте лог job. Строка `whisper bot @YourBot is polling` значит, что бот на связи. Затем в Telegram: `/start`.

Локально то же самое:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# впишите BOT_TOKEN, DATABASE_URL, FERNET_KEY
.venv/bin/python -m bot
```

Тесты (SQLite, без Telegram и Neon):

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest --cov=bot --cov-fail-under=70
```

## Как бот остаётся включённым

GitHub-hosted runner **не имеет публичного адреса**. cron-job.org не может пинговать `localhost:8080` этой машины. Поэтому «не уснуть» здесь делает само расписание Actions, а не внешний HTTP-пинг.

Что есть в коде:

- `GET /health` на `0.0.0.0:8080` — `{"status":"ok","db":"ok"}` или 503, если база не отвечает.
- Внутри процесса каждые 4 минуты — проверка базы и, если задан `HEARTBEAT_URL`, исходящий GET (это как раз сигнал для healthchecks.io).
- Каждые 30 минут, пока процесс жив, — `pg_dump` в приватный репозиторий, если задан `BACKUP_REPO_URL` и установлен `pg_dump`. Иначе шаг пропускается.
- Отдельный workflow **Hourly backup** (`.github/workflows/backup.yml`, cron `15 * * * *`) делает тот же дамп раз в час. Без секрета тоже no-op.

Если позже перенесёте бота на VPS, cron-job.org сможет пинговать `http://ВАШ_IP:8080/health` каждые 4 минуты. На чистом GitHub Actions этот входящий пинг не заработает без туннеля с постоянным адресом.

## Резервная копия

Секрет `BACKUP_REPO_URL` — полный URL с правом push, например:

```text
https://x-access-token:github_pat_xxx@github.com/you/whisper-backups.git
```

Репозиторий должен быть **приватным**: в дампе тексты шёпотов. Токену достаточно `contents: write` на этот репозиторий. В исходный репозиторий бота дамп не пушится.

## Команды

`/start` · `/send` · `/settings` · `/cgn` · `/stats` · `/export` · `/nomedia` · `/cancel`

Меню: 🔒 Отправить шёпот · ⚙️ Настройки · 📇 Мои контакты · 👥 Мои группы · ℹ️ Инструкция.

Подробно, что делает каждая функция и какие у неё ограничения: [docs/FEATURES.md](docs/FEATURES.md).
