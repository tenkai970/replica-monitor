from Code.load_env import load_env
from pathlib import Path
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


class TableConfig(BaseModel):
    """
    Полная настройка сверки одной таблицы.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    primary: SourceConfig
    replica: SourceConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    max_row_diff: int = 0
    max_lag_minutes: int | None = None


class DatabaseConfig(BaseModel):
    """
    Корневой объект конфигурации приложения.
    """
    model_config = ConfigDict(extra="forbid")

    tables: list[TableConfig]


def load_config(path: str | Path = "config.yaml") -> DatabaseConfig:
    """
    Загружает YAML-конфиг и валидирует его через Pydantic.
    """
    config_path = Path(path)

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
        print(elem.name)
        print(elem.primary)
        print(elem.replica)

