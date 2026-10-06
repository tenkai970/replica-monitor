"""
Экспорт готовых строк отчёта в Excel.

Каждая строка — словарь с table_name, status, comment, временем,
именами источников, флагами проверок и их погрешностями.
Подготовка данных, чтение .env и выполнение проверок находятся снаружи.
"""
from datetime import date, datetime
from pathlib import Path
from typing import Any
import os
import subprocess
import sys

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from Code.logger import create_logger


logger = create_logger(__name__)


def _open_folder(path: Path) -> None:
    """
    Открывает папку средствами ОС. Ошибка открытия не отменяет экспорт.
    """
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True, capture_output=True, timeout=5)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(path)], check=True, capture_output=True, timeout=5)
        else:
            logger.warning("Автоматическое открытие папки не поддерживается: %s", sys.platform)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("Отчёт сохранён, но не удалось открыть папку %s: %s", path, error)


COLUMN_TITLES = {
    "checked_at": "Время проверки",
    "primary": "Основная таблица",
    "replica": "Реплика",
    "status": "Статус",
    "comment": "Комментарий",
    "metrics": "Фактические метрики",
    "checks": "Проверки и погрешности",
}

STATUS_COLORS = {"ERROR": "F8A9A9", "WARNING": "FFE699", "OK": "C6EFCE"}

COLUMN_WIDTHS = {
    "checked_at": 32, "primary": 30, "replica": 34, "status": 12,
    "comment": 55, "metrics": 60, "checks": 43,
}


def _prepare_row(row: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for strategy, rule, label, unit in (
        ("row_count", "max_row_diff", "Разница строк", "строк"),
        ("lag_check", "max_lag_seconds", "Отставание", "сек."),
        ("last_update", "last_update_seconds", "Возраст обновления", "сек."),
    ):
        if row.get(strategy):
            limit = row.get(rule)
            if limit is None or pd.isna(limit):
                checks.append(f"{label}: погрешность не передана")
            else:
                checks.append(f"{label}: до {limit} {unit}")

    if not checks:
        flags = ("row_count", "lag_check", "last_update")
        checks.append(
            "Проверки отключены" if all(key in row for key in flags)
            else "Сведения о проверках не переданы"
        )

    return {
        "checked_at": row.get("checked_at"),
        "primary": ".".join(str(value) for value in (
            row.get("primary_database"), row.get("primary_table")
        ) if value is not None and value != "") or row["table_name"],
        "replica": ".".join(str(value) for value in (
            row.get("replica_database"), row.get("replica_table")
        ) if value is not None and value != "") or None,
        "status": row["status"],
        "comment": row["comment"],
        "metrics": row.get("metrics") or "Метрики не переданы",
        "checks": "\n".join(checks),
    }


def export_report(
        rows: list[dict[str, Any]],
        output_dir: str | Path,
        open_folder: bool = True,
    ) -> Path:
    """
    Сохраняет новый Excel и возвращает полный путь к нему.

    Выводятся семь колонок: время, основная таблица, реплика, статус,
    комментарий, фактические метрики, включённые проверки с погрешностями.
    Готовый текст фактических метрик передаётся в поле metrics.
    Имена источников передаются в primary_database/primary_table и
    replica_database/replica_table, время — в checked_at, флаги и rules —
    отдельными полями row_count, max_row_diff и т. д.
    Отсутствующие значения остаются пустыми, нули сохраняются.
    Даты с часовым поясом записываются текстом с сохранением смещения,
    поскольку Excel не поддерживает часовые пояса в ячейках дат.

    output_dir поддерживает относительные пути, ~ и переменные окружения
    в формате текущей ОС. Относительный путь считается от рабочей папки.
    При open_folder=True папка открывается после сохранения отчёта.
    """
    if not str(output_dir).strip():
        raise ValueError("\nНе задана папка для экспорта отчёта")
    output_dir = Path(os.path.expandvars(str(output_dir))).expanduser().resolve()

    for index, row in enumerate(rows):
        for field in ("table_name", "status", "comment"):
            if field not in row:
                raise ValueError(f"\nВ строке отчёта {index} отсутствует поле {field}")
        if row["status"] not in ("OK", "WARNING", "ERROR"):
            raise ValueError(f"\nНеизвестный статус в строке отчёта {index}: {row['status']}")

    columns = list(COLUMN_TITLES)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Проверки"
    sheet.sheet_view.showGridLines = True
    sheet.sheet_view.zoomScale = 85
    sheet.freeze_panes = "A2"
    sheet.append([COLUMN_TITLES.get(key, key) for key in columns])

    side = Side(style="thin", color="A6A6A6")
    border = Border(left=side, right=side, top=side, bottom=side)
    for cell in sheet[1]:
        cell.data_type = "s"
        cell.font = Font(name="Calibri", size=11, bold=True, color="000000")
        cell.fill = PatternFill("solid", fgColor="E7E6E6")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    sheet.row_dimensions[1].height = 32

    for row_index, row in enumerate(rows, start=2):
        row = _prepare_row(row)
        color = STATUS_COLORS.get(row["status"])
        for column_index, key in enumerate(columns, start=1):
            value = row.get(key)
            if value is None or pd.isna(value):
                value = None
            elif isinstance(value, datetime) and value.utcoffset() is not None:
                value = value.strftime("%d.%m.%Y %H:%M:%S %z")
            cell = sheet.cell(row_index, column_index, value)
            if isinstance(value, str):
                cell.data_type = "s"
            elif isinstance(value, (date, datetime)):
                cell.number_format = "dd.mm.yyyy hh:mm:ss"
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cell.number_format = "#,##0.###"
            cell.font = Font(name="Calibri", size=11, color="000000", bold=key == "status")
            cell.alignment = Alignment(horizontal="center" if key == "status" else "left", vertical="center", wrap_text=True)
            cell.border = border
            if color:
                cell.fill = PatternFill("solid", fgColor=color)
        # Высота учитывает переносы во всех колонках, включая метрики.
        lines = 1
        for key in columns:
            width = COLUMN_WIDTHS[key] - 3
            text_lines = str(row.get(key) or "").split("\n")
            lines = max(lines, sum(max(1, (len(line) + width - 1) // width) for line in text_lines))
        sheet.row_dimensions[row_index].height = min(409, max(36, lines * 16 + 10))

    for index, key in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = COLUMN_WIDTHS[key]
    sheet.auto_filter.ref = sheet.dimensions

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"replica_monitor_{datetime.now():%Y-%m-%d_%H-%M-%S-%f}"
    output_path = output_dir / f"{filename}.xlsx"
    suffix = 0
    while True:
        try:
            output_file = output_path.open("xb")
            break
        except FileExistsError:
            suffix += 1
            output_path = output_dir / f"{filename}_{suffix}.xlsx"

    try:
        with output_file:
            workbook.save(output_file)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        workbook.close()
    logger.info("Отчёт сохранён: %s", output_path)
    if open_folder:
        _open_folder(output_dir)
    return output_path
