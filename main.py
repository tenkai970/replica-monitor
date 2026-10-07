"""Самостоятельный запуск: python main.py из любой рабочей папки."""
from Code.load_env import load_env
from Code.monitor import run_checks


if __name__ == "__main__":
    load_env()
    run_checks(create_xlsx=True)
