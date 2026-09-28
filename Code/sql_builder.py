import re
from Code.config import SourceConfig


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    """
    Проверяет имя базы, таблицы или колонки и экранирует его.
    """
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(f"\nНекорректный SQL-идентификатор: {identifier}")

    return f"`{identifier}`"


def build_table_name(source: SourceConfig) -> str:
    """
    Собирает полное имя таблицы database.table.
    """
    return f"{quote_identifier(source.database)}.{quote_identifier(source.table)}"


def build_metrics_query(source: SourceConfig) -> str:
    """
    Собирает SQL-запрос для получения метрик по одной таблице.
    """
    metrics = ["COUNT(*) AS row_count"]

    if source.datetime_column:
        datetime_column = quote_identifier(source.datetime_column)
        metrics.append(f"MAX({datetime_column}) AS max_datetime")

    table_name = build_table_name(source)
    final_modifier = " FINAL" if source.type == "clickhouse" and source.final else ""
    metrics_sql = ",\n    ".join(metrics)

    return (
        "SELECT\n"
        f"    {metrics_sql}\n"
        f"FROM {table_name}{final_modifier}"
    )


def build_column_exists_query(source: SourceConfig) -> tuple[str, dict[str, str]]:
    """
    Собирает запрос для проверки существования datetime_column в таблице.
    """
    if not source.datetime_column:
        raise ValueError(f"\nДля {source.full_name} не задан datetime_column")

    quote_identifier(source.datetime_column)

    params = {
        "database": source.database,
        "table": source.table,
        "column": source.datetime_column,
    }

    if source.type == "mysql":
        return (
            "SELECT COUNT(*) AS column_exists\n"
            "FROM information_schema.columns\n"
            "WHERE table_schema = :database\n"
            "  AND table_name = :table\n"
            "  AND column_name = :column",
            params,
        )

    if source.type == "clickhouse":
        return (
            "SELECT COUNT(*) AS column_exists\n"
            "FROM system.columns\n"
            "WHERE database = {database:String}\n"
            "  AND table = {table:String}\n"
            "  AND name = {column:String}",
            params,
        )

    raise ValueError(f"\nНеподдерживаемый тип источника: {source.type}")


if __name__ == "__main__":
    from Code.config import load_config
    from Code.load_env import load_env

    load_env()
    config = load_config()

    for table in config.tables:
        print(f"-- {table.name}: primary")
        print(build_metrics_query(table.primary))
        print()
        print(f"-- {table.name}: replica")
        print(build_metrics_query(table.replica))
        print()
