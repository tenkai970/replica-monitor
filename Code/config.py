from Code.load_env import load_env
from pathlib import Path
import os
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class SourceConfig(BaseModel):
    """
    Описание одной таблицы из группы
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["mysql", "clickhouse"]
    database: str
    table: str
    datetime_column: str | None = None
    last_update_column: str | None = None
    final: bool = True
    @model_validator(mode='after')
    def normalize_final_by_source_type(self):
        if self.type != "clickhouse":
            self.final = False

        return self

    @property
    def full_name(self) -> str:
        return f"{self.database}.{self.table}"


class StrategyConfig(BaseModel):
    """
    Набор проверок, которые нужно выполнить для таблицы.
    """
    model_config = ConfigDict(extra="forbid")

    row_count: bool = True
    lag_check: bool = False
    last_update: bool = False

class RulesConfig(BaseModel):
    """
    Погрешности, допустимые для сравнения таблиц.
    """
    model_config = ConfigDict(extra="forbid")

    max_row_diff: int = Field(default=0, ge=0)
    max_lag_seconds: int = Field(default=0, ge=0)
    last_update_seconds: int = Field(default=0, ge=0)

class TableConfig(BaseModel):
    """
    Полная настройка сверки одной таблицы.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    primary:  SourceConfig
    replica:  SourceConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    rules:     RulesConfig

class DatabaseConfig(BaseModel):
    """
    Корневой объект конфигурации приложения.
    """
    model_config = ConfigDict(extra="forbid")

    tables: list[TableConfig]

    @model_validator(mode='after')
    def check_unique_table_names(self):
        names = set()
        for index, table in enumerate(self.tables):
            if table.name in names:
                raise ValueError(f"YAML tables[{index}].name: повторяющееся имя {table.name}")
            names.add(table.name)
        return self


def get_config_path() -> Path:
    """Возвращает путь к пресету CONFIG_PATH / CONFIG_NAME из окружения."""
    for key in ("CONFIG_PATH", "CONFIG_NAME"):
        if not os.getenv(key, "").strip():
            raise ValueError(f"\nНе задан обязательный параметр .env: {key}")

    directory = Path(os.path.expandvars(os.environ["CONFIG_PATH"])).expanduser().resolve()
    name = os.environ["CONFIG_NAME"].strip()

    if name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        raise ValueError("\nCONFIG_NAME должен содержать только имя YAML-файла")

    if Path(name).suffix.lower() not in (".yaml", ".yml"):
        raise ValueError("\nCONFIG_NAME должен иметь расширение .yaml или .yml")

    if not directory.is_dir():
        raise NotADirectoryError(f"\nCONFIG_PATH должен указывать на папку пресетов: {directory}")
    return directory / name


def load_config(path: str | Path | None = None) -> DatabaseConfig:
    """
    Загружает YAML-конфиг и валидирует его через Pydantic.
    """
    config_path = Path(path) if path is not None else get_config_path()

    if not config_path.exists():
        raise FileNotFoundError(f"\nНе найден файл конфигурации: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file)

    if not raw_config:
        raise ValueError(f"\nФайл конфигурации пустой: {config_path}")

    try:
        return DatabaseConfig.model_validate(raw_config)
    except ValidationError as error:
        raise ValueError(f"\nОшибка валидации конфигурации {config_path}:\n{error}") from error


if __name__ == "__main__":
    load_env()
    config = load_config()
    for elem in config.tables:
        print(elem)
        # print(elem.name)
        # print(elem.primary)
        # print(elem.replica)

