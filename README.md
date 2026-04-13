# web4mihomo

Лёгкий веб-интерфейс (**FastAPI** + **HTMX** + **Tailwind**) для добавления одиночных узлов **VLESS** в работающий **[mihomo](https://github.com/MetaCubeX/mihomo)** (Clash Meta) через **External Controller API** и **file proxy-provider**. Подписки не используются: только `vless://` по одной или нескольким строкам.

## Возможности

- Вставка одной или **нескольких** ссылок `vless://` (по одной на строку; строки с `#` в начале — комментарии).
- Парсинг Reality (`pbk`, `sid`, `fp`, `sni`, …), транспорты tcp/ws/grpc/http/h2/xhttp, `flow`, `packet-encoding`.
- Запись YAML для `proxy-providers` типа `file` и перезагрузка провайдера через `PUT /providers/proxies/{name}`.
- Хранение ссылок и метаданных в **JSON** (`data/my_vless_proxies.json` по умолчанию).
- Если JSON пуст, а файл провайдера уже содержит узлы — **импорт из YAML** в список (без восстановления исходных `vless://`; пометка **YAML** в таблице).
- Проверка задержки: по одному узлу и **Delay: все** (только для строк таблицы, узлы не добавляет).
- Опциональная защита веб-интерфейса паролем (`UI_PASSWORD`).

## Требования

- Python **3.11+** (рекомендуется)
- Запущенный mihomo с **external-controller** и **secret**
- В конфиге mihomo один раз настроены `proxy-providers` (file) и `proxy-groups` с `use: [имя провайдера]` — см. комментарии в [`app/settings.py`](app/settings.py) или блок «Помощь» в UI.

## Установка

```bash
git clone <url> web4mihomo
cd web4mihomo
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip wheel
pip install -r requirements.txt
```

На Debian/Ubuntu при отсутствии venv: `apt install python3-venv python3-full`.

Быстрый скрипт: `bash scripts/setup-venv.sh`

## Настройка

Переменные окружения или файл **`.env`** в корне проекта (см. [`app/settings.py`](app/settings.py)):

| Переменная | Описание |
|------------|----------|
| `MIHOMO_BASE_URL` | URL API, например `http://127.0.0.1:9090` |
| `MIHOMO_SECRET` | Bearer secret из конфига mihomo |
| `PROVIDER_NAME` | Имя file-провайдера в YAML mihomo (по умолчанию `web4mihomo_nodes`) |
| `PROVIDER_YAML_PATH` | Путь к YAML-файлу провайдера (абсолютный или относительно **корня проекта**, каталог с `main.py`) |
| `JSON_STORE_PATH` | Путь к JSON-хранилищу (по умолчанию `data/my_vless_proxies.json`) |
| `UI_PASSWORD` | Если задан — вход по паролю (сессия) |
| `DELAY_TEST_URL` | URL для delay-теста |
| `DELAY_TIMEOUT_MS` | Таймаут delay (мс) |
| `DELAY_TEST_EXPECTED` | Опционально query `expected` для API mihomo |
| `TEST_ALL_CONCURRENCY` | Параллелизм «Delay: все» |
| `WEB4_VERBOSE_LOG` | `true` / `false` — подробные логи в консоль |

Каталоги `data/` и файл `.env` в **`.gitignore`** — при обновлении с `git pull` локальные прокси и секреты не перезаписываются.

## Запуск

```bash
export MIHOMO_SECRET="ваш-secret"
# опционально: export MIHOMO_BASE_URL=... PROVIDER_YAML_PATH=...
uvicorn main:app --host 0.0.0.0 --port 8800
```

Откройте в браузере `http://<хост>:8800`.

## Ограничения API mihomo

Добавить произвольный узел **только** через `PATCH /proxies` нельзя: ядро ожидает обновление **file provider** после записи YAML на диск. Поэтому путь в `proxy-providers.path` в mihomo и `PROVIDER_YAML_PATH` в приложении должны совпадать.

## Особенность YAML: REALITY `short-id`

Hex-значения вроде `486e44` без кавычек в YAML 1.1 могут интерпретироваться как число в экспоненциальной записи. При генерации файла провайдера такие `short-id` принудительно **экранируются** в кавычки.

## Структура проекта

```
main.py                 # uvicorn: app = create_app()
app/                    # настройки, парсер vless, клиент mihomo, sync, роуты
templates/              # Jinja2 + HTMX
static/
scripts/setup-venv.sh
requirements.txt
```

## Лицензия

Используйте и изменяйте по своему усмотрению; зависимости подчиняются их лицензиям.
