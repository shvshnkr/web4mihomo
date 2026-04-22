"""Application configuration.

Bootstrap mihomo (one-time): add a file-based ``proxy-providers`` entry whose
``path`` matches ``provider_yaml_path`` below, and ``proxy-groups`` that
``use`` that provider. Example (adjust paths and names):

```yaml
proxy-providers:
  web4mihomo_nodes:
    type: file
    path: C:/path/to/data/web4mihomo_provider.yaml
    interval: 0
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 600

proxy-groups:
  - name: WEB4_FAILOVER
    type: url-test
    use:
      - web4mihomo_nodes
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
    lazy: true

  - name: WEB4_LB
    type: load-balance
    strategy: consistent-hashing
    use:
      - web4mihomo_nodes
    url: https://www.gstatic.com/generate_204
```

If the provider file lives outside mihomo's home directory, set the
``SAFE_PATHS`` environment variable for mihomo (see upstream docs).
"""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mihomo_base_url: str = Field(
        default="http://127.0.0.1:9090",
        description="External controller base URL (no trailing slash).",
    )
    mihomo_secret: str = Field(
        default="",
        description="Bearer secret for mihomo external-controller.",
    )

    provider_name: str = Field(
        default="web4mihomo_nodes",
        description="Name of the file proxy-provider in mihomo config.",
    )
    provider_yaml_path: Path = Field(
        default=Path("data/web4mihomo_provider.yaml"),
        description="Абсолютный путь или относительно **корня проекта** (папка с main.py), не от cwd uvicorn.",
    )
    json_store_path: Path = Field(
        default=Path("data/my_vless_proxies.json"),
        description="JSON: абсолютный путь или относительно **корня проекта**.",
    )

    @model_validator(mode="after")
    def resolve_paths_from_project_root(self) -> "Settings":
        """Относительные пути привязываем к каталогу проекта, чтобы cwd не ломал импорт/запись."""
        if not self.provider_yaml_path.is_absolute():
            self.provider_yaml_path = (_PROJECT_ROOT / self.provider_yaml_path).resolve()
        if not self.json_store_path.is_absolute():
            self.json_store_path = (_PROJECT_ROOT / self.json_store_path).resolve()
        return self

    delay_test_url: str = Field(
        default="https://www.gstatic.com/generate_204",
        description=(
            "URL for GET /proxies/{name}/delay. Если 503 — попробуйте, например, "
            "http://cp.cloudflare.com/generate_204 или https://1.1.1.1 (через env DELAY_TEST_URL)."
        ),
    )
    delay_timeout_ms: int = Field(default=10000, ge=500, le=120000)
    delay_test_expected: str | None = Field(
        default=None,
        description=(
            "Необязательный query `expected` для API delay (формат mihomo: 204, 200/302, …). "
            "Оставьте пустым — по умолчанию mihomo принимает любой успешный ответ."
        ),
    )

    ui_password: str | None = Field(
        default=None,
        description="If set, browser session login is required for the UI.",
    )

    test_all_concurrency: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Parallel delay checks for «Test all».",
    )

    verbose_app_log: bool = Field(
        default=True,
        description="Подробные логи (маршруты, шаги синхронизации). Выключить: WEB4_VERBOSE_LOG=false.",
    )
    subscriptions_refresh_on_startup: bool = Field(
        default=True,
        description="If true, refresh enabled subscriptions when app starts.",
    )
    subscriptions_auto_refresh_interval_sec: int = Field(
        default=0,
        ge=0,
        le=86400,
        description="Periodic enabled subscriptions refresh interval in seconds (0 disables scheduler).",
    )
    subscriptions_fetch_timeout_sec: float = Field(
        default=20.0,
        ge=3.0,
        le=120.0,
        description="HTTP timeout for subscription URL fetch.",
    )

    @property
    def session_secret(self) -> str:
        """Stable secret for signed cookies (SessionMiddleware)."""
        base = (self.mihomo_secret or "change-me") + "|web4mihomo-session"
        if len(base) < 32:
            base = (base * 4)[:32]
        return base[:64]

    def bootstrap_yaml_hint(self) -> str:
        """Human-readable snippet for mihomo config (paths are examples)."""
        path = str(self.provider_yaml_path).replace("\\", "/")
        return f"""proxy-providers:
  {self.provider_name}:
    type: file
    path: {path}
    interval: 0
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 600

proxy-groups:
  - name: WEB4_FAILOVER
    type: url-test
    use:
      - {self.provider_name}
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
    lazy: true

  - name: WEB4_LB
    type: load-balance
    strategy: consistent-hashing
    use:
      - {self.provider_name}
    url: https://www.gstatic.com/generate_204
"""
