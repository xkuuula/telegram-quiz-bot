# Telegram Quiz Poll Bot

Telegram-бот на Python, который берет вопросы из Google Sheets и отправляет quiz-опросы в Telegram-чат по расписанию.

## Возможности

- Отправка quiz-опросов в `13:00` и `20:00` по timezone из `.env`.
- Ручная отправка следующего вопроса командой `/send_now`.
- Ограничение `/send_now` одним пользователем через `SEND_NOW_USER_ID`.
- Управляемый список упоминаний для опросов: `/add @username`, `/remove @username`, `/mentions`.
- Автоматическое закрепление последнего отправленного опроса до следующего опроса.
- Получение `chat_id` и `user_id` командой `/chatid`.
- Источник истины по вопросам и статусам - Google Sheets.
- Защита от повторной отправки строк со статусом `SENT`.
- Автоматическое создание и заполнение служебных столбцов `D` и `E`.
- Повторные попытки при временных сбоях Google Sheets.
- Обработка Telegram FloodWait.

## Структура

```text
telegram_quiz_bot/
├── main.py
├── config.py
├── scheduler.py
├── google_sheets.py
├── poll_sender.py
├── requirements.txt
├── .env.example
├── README.md
└── credentials.json
```

`credentials.json` не хранится в репозитории. Его нужно скачать в Google Cloud Console и положить рядом с `main.py`.

## Google Sheets

Таблица должна иметь такую структуру:

| Столбец | Назначение |
| --- | --- |
| A | Текст вопроса |
| B | Варианты ответа через запятую |
| C | Объяснение |
| D | Статус: `NOT_SENT` или `SENT` |
| E | Дата и время отправки |

Первый вариант в столбце `B` всегда считается правильным.

Пример:

```text
Какой язык используется в Django? | Python,Java,C++,PHP | Django написан на Python.
```

## Настройка Google

1. Создайте проект в Google Cloud Console.
2. Включите Google Sheets API.
3. Создайте Service Account.
4. Скачайте JSON-ключ и переименуйте его в `credentials.json`.
5. Положите `credentials.json` рядом с `main.py`.
6. Откройте Google Sheets таблицу.
7. Нажмите `Share`.
8. Добавьте `client_email` из `credentials.json` с ролью `Editor`.

## Настройка Telegram

1. Создайте бота через `@BotFather`.
2. Скопируйте токен в `BOT_TOKEN`.
3. Добавьте бота в нужный чат.
4. Запустите бота и отправьте `/chatid`.
5. Скопируйте `chat_id` в `CHAT_ID`.
6. Скопируйте свой `user_id` в `SEND_NOW_USER_ID`, если `/send_now` должен быть доступен только вам.

## .env

Скопируйте пример:

```powershell
Copy-Item .env.example .env
```

Заполните:

```env
BOT_TOKEN=replace_with_your_telegram_bot_token
CHAT_ID=-1001234567890
SEND_NOW_USER_ID=123456789
MENTION_ADMIN_USER_ID=123456789
MENTIONS_FILE=mentions.json
PINNED_POLL_FILE=pinned_poll.json
GOOGLE_SHEET_ID=replace_with_your_google_sheet_id
GOOGLE_SHEET_NAME=Telegram Quiz Questions
TIMEZONE=Europe/Moscow
```

Рекомендуется использовать `GOOGLE_SHEET_ID`. Это часть URL между `/d/` и `/edit`.

## Запуск

```powershell
.\.venv\Scripts\python.exe main.py
```

## Команды

```text
/send_now
```

Отправляет следующий валидный вопрос со статусом `NOT_SENT`.

```text
/add @username
/remove @username
/mentions
```

Добавляет, удаляет и показывает людей, которых бот тегает перед каждым опросом. Доступ ограничивается `MENTION_ADMIN_USER_ID`; если он не задан, используется `SEND_NOW_USER_ID`.

```text
/chatid
```

Показывает `chat_id` текущего чата и `user_id` отправителя.

## Расписание

Бот отправляет вопросы каждый день:

- `13:00`
- `20:00`

Часовая зона задается через `TIMEZONE`.

## Установка зависимостей

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Важные ограничения

- Вопрос не должен быть пустым.
- Должно быть от 2 до 10 вариантов ответа.
- Объяснение может быть пустым.
- Если Telegram не принял опрос, строка не помечается как `SENT`.
- Если Google Sheets временно недоступен, бот продолжает работать и повторяет попытки.
