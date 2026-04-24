# web4mihomo

Лёгкий веб-интерфейс (**FastAPI** + **HTMX** + **Tailwind**) для управления узлами и подписками в работающем **[mihomo](https://github.com/MetaCubeX/mihomo)** (Clash Meta) через **External Controller API** и **file proxy-provider**.

## Возможности

- Вставка одной или **нескольких** ссылок `vless://` и `trojan://` (по одной на строку; строки с `#` в начале — комментарии).
- Подписки по URL (JSON `links`, plain text URI list, base64 URI list).
- Парсинг Reality (`pbk`, `sid`, `fp`, `sni`, …), транспорты tcp/ws/grpc/http/h2/xhttp, `flow`, `packet-encoding`.
- Запись двух YAML для `proxy-providers` типа `file`:
  - full provider (без переименования текущего файла),
  - LB provider (отфильтрованный пул).
- Перезагрузка обоих провайдеров через `PUT /providers/proxies/{name}`.
- Хранение ссылок и метаданных в **JSON** (`data/my_vless_proxies.json` по умолчанию).
- Если JSON пуст, а файл провайдера уже содержит узлы — **импорт из YAML** в список (без восстановления исходных `vless://`; пометка **YAML** в таблице).
- Ручные и автоматические исключения subscription-узлов (`excluded_uris` и `auto_excluded_uris`).
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

Установка systemd unit:

```bash
sudo bash scripts/install-systemd-unit.sh
```

При необходимости можно переопределить параметры:

```bash
sudo UNIT_NAME=web4mihomo APP_DIR=/etc/mihomo/web4mihomo APP_USER=root APP_GROUP=root PORT=8800 \
  bash scripts/install-systemd-unit.sh
```

## Настройка

Переменные окружения или файл **`.env`** в корне проекта (см. [`app/settings.py`](app/settings.py)):

| Переменная | Описание |
|------------|----------|
| `MIHOMO_BASE_URL` | URL API, например `http://127.0.0.1:9090` |
| `MIHOMO_SECRET` | Bearer secret из конфига mihomo |
| `PROVIDER_NAME` | Имя file-провайдера в YAML mihomo (по умолчанию `web4mihomo_nodes`) |
| `PROVIDER_YAML_PATH` | Путь к YAML-файлу провайдера (абсолютный или относительно **корня проекта**, каталог с `main.py`) |
| `PROVIDER_LB_NAME` | Имя второго file-провайдера для LB (по умолчанию `web4mihomo_nodes_lb`) |
| `PROVIDER_LB_YAML_PATH` | Путь к YAML-файлу отфильтрованного LB-провайдера |
| `JSON_STORE_PATH` | Путь к JSON-хранилищу (по умолчанию `data/my_vless_proxies.json`) |
| `UI_PASSWORD` | Если задан — вход по паролю (сессия) |
| `DELAY_TEST_URL` | URL для delay-теста |
| `DELAY_TIMEOUT_MS` | Таймаут delay (мс) |
| `DELAY_TEST_EXPECTED` | Опционально query `expected` для API mihomo |
| `TEST_ALL_CONCURRENCY` | Параллелизм «Delay: все» |
| `AUTO_FILTER_ENABLED` | Включить авто-исключение медленных/падающих subscription-узлов |
| `AUTO_FILTER_MAX_DELAY_MS` | Порог задержки для авто-исключения |
| `AUTO_FILTER_FAIL_STREAK` | Сколько подряд провалов нужно для авто-исключения |
| `AUTO_FILTER_RECHECK_INTERVAL_SEC` | Рекомендуемый интервал автопроверки |
| `AUTO_FILTER_PROBE_URL` | Рекомендуемый URL для delay-отбора |
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

Добавить произвольный узел **только** через `PATCH /proxies` нельзя: ядро ожидает обновление **file provider** после записи YAML на диск. Поэтому пути в `proxy-providers.path` в mihomo и `PROVIDER_YAML_PATH` / `PROVIDER_LB_YAML_PATH` в приложении должны совпадать.

## Dynamic LB через web4mihomo

Если в `mihomo` у вас `WEB4_LB` + `WEB4_FAILOVER` + `WEB4_SMART`, используйте два провайдера:

- `web4mihomo_nodes` (full) -> полный пул для `WEB4_FAILOVER`;
- `web4mihomo_nodes_lb` (filtered) -> отфильтрованный пул для `WEB4_LB`.

Важно: текущий файл `/etc/mihomo/web4mihomo/data/web4mihomo_provider.yaml` остается как есть, без переименования.

Пример фрагмента `config.yaml`:

```yaml
proxy-providers:
  web4mihomo_nodes:
    type: file
    path: /etc/mihomo/web4mihomo/data/web4mihomo_provider.yaml
    interval: 0

  web4mihomo_nodes_lb:
    type: file
    path: /etc/mihomo/web4mihomo/data/web4mihomo_provider_lb.yaml
    interval: 0

proxy-groups:
  - name: WEB4_FAILOVER
    type: url-test
    use: [web4mihomo_nodes]
    url: http://cp.cloudflare.com/generate_204
    interval: 100

  - name: WEB4_LB
    type: load-balance
    strategy: consistent-hashing
    use: [web4mihomo_nodes_lb]
    url: http://cp.cloudflare.com/generate_204
    interval: 150

  - name: WEB4_SMART
    type: fallback
    proxies: [WEB4_LB, WEB4_FAILOVER]
    url: http://cp.cloudflare.com/generate_204
    interval: 180
```

Динамический отбор на стороне web4mihomo:

- Запускать `Delay: все` по рабочему probe URL (`http://cp.cloudflare.com/generate_204`).
- Узлы подписок с `503/504/timeout` или `delay > AUTO_FILTER_MAX_DELAY_MS` автоматически попадают в `auto_excluded_uris`.
- При следующем sync такие узлы не попадают в **LB provider**, и группа `WEB4_LB` работает по очищенному набору.
- `Delay: все` выполняет recheck для уже auto-excluded subscription-узлов, чтобы они могли автоматически вернуться в LB после стабилизации.

Рекомендованные стартовые значения:

- `DELAY_TEST_URL=http://cp.cloudflare.com/generate_204`
- `DELAY_TIMEOUT_MS=8000..12000`
- `TEST_ALL_CONCURRENCY=3..5`
- `AUTO_FILTER_ENABLED=true`
- `AUTO_FILTER_MAX_DELAY_MS=1500`
- `AUTO_FILTER_FAIL_STREAK=2`

Если видите массовые `503/504`:

- снизьте `TEST_ALL_CONCURRENCY`,
- чуть увеличьте `DELAY_TIMEOUT_MS`,
- попробуйте другой `DELAY_TEST_URL` (`https://1.1.1.1` или `http://cp.cloudflare.com/generate_204`),
- проверьте доступность нод в клиенте/логах mihomo.

Откат за минуту:

- В `mihomo/config.yaml` временно переключите `WEB4_LB` обратно на `use: [web4mihomo_nodes]`.
- Оставьте второй provider в конфиге, но не используйте его в группах до стабилизации фильтра.

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
