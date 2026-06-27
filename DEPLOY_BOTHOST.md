# Деплой на Bothost

Проект подготовлен для запуска через Docker.

## Что загрузить

Загрузите архив `telegram_quiz_bot_bothost.zip` или папку проекта без локальных файлов:

- `.venv`
- `__pycache__`
- `.env`
- `credentials.json`
- `bot.out.log`
- `bot.err.log`

Эти файлы исключены в `.dockerignore`.

## Переменные окружения

В панели Bothost задайте ENV:

```env
BOT_TOKEN=ваш_telegram_bot_token
CHAT_ID=-1001234567890
SEND_NOW_USER_ID=123456789
GOOGLE_SHEET_ID=ваш_google_sheet_id
GOOGLE_SHEET_NAME=oprosi
TIMEZONE=Europe/Moscow
GOOGLE_CREDENTIALS_BASE64=base64_строка_из_credentials_json
```

`GOOGLE_CREDENTIALS_BASE64` - лучший вариант для хостинга, потому что это одна строка без переносов.

Получить строку на Windows:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
```

Альтернативно можно использовать `GOOGLE_CREDENTIALS_JSON`, но тогда JSON должен быть строго одной строкой.

## Команда запуска

Если Bothost просит команду:

```bash
python main.py
```

Если Bothost собирает `Dockerfile`, команда уже указана:

```dockerfile
CMD ["python", "main.py"]
```

## Проверка

1. Остановите локального бота.
2. Запустите бота на Bothost.
3. В Telegram отправьте `/chatid`.
4. Отправьте `/send_now` с пользователя из `SEND_NOW_USER_ID`.

Одновременно запускать локальную копию и Bothost с одним токеном нельзя: Telegram вернет `Conflict: terminated by other getUpdates request`.
