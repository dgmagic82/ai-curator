# Выкладка Telegram-бота на сервер

Бот работает на VPS вне России. С российского сервера OpenRouter отвечает `403 Access denied by security policy`. Первая выкладка на сервер в Петербурге упёрлась именно в это, после чего бот перенесли.

## Что нужно

- Ubuntu с Python 3.12 или новее, `git`, пакет `python3-venv`. Боту хватает около 350 МБ диска и 150 МБ памяти.
- Токен бота от @BotFather.
- Ключ OpenRouter **с лимитом расходов**: бот публичный.
- Id чата администратора для `/stats` и `/export`.

## Шаги

```bash
git clone https://github.com/dgmagic82/ai-curator /opt/ai-curator
```

```bash
cd /opt/ai-curator && mkdir -p logs && python3 -m venv venv && venv/bin/pip install -r deploy/requirements-bot.txt
```

Зависимости для бота урезаны: без Streamlit и pandas.

Создайте `/opt/ai-curator/.env` по образцу `deploy/env.example`. Права на файл `600`. Соль для хеширования сгенерируйте на сервере:

```bash
echo "CURATOR_LOG_SALT=$(openssl rand -hex 16)" >> /opt/ai-curator/.env
```

```bash
cp deploy/ai-curator-bot.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now ai-curator-bot
```

Служба:
- перезапускается при сбое;
- поднимается после перезагрузки сервера;
- может писать только в `logs/`;
- ограничена 400 МБ памяти.

## Проверка

```bash
systemctl is-active ai-curator-bot && journalctl -u ai-curator-bot -n 20 --no-pager
```

В журнале должны быть строки `bot started` и `Application started`. Затем в Telegram отправьте `/start`, задайте вопрос и вызовите `/stats` из чата администратора.

## Обновление

```bash
cd /opt/ai-curator && git pull && systemctl restart ai-curator-bot
```

База логов `logs/curator.db` при обновлении сохраняется: она не в git. Индекс FAISS приходит из репозитория. Если вы поменяли `kb/`, сначала переиндексируйте локально и закоммитьте `index/`.
