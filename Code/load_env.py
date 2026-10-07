from   dotenv import load_dotenv
from   Code.logger import create_logger
import os, sys

logger = create_logger()
REQUIRED_ENV_VARS = ['ROOT_DIR', 'CONFIG_PATH', 'CONFIG_NAME'] # Список обязательных переменных окружения

def _check_env_vars(keys: list[str]) -> None:
    """
    Проверяет наличие обязательных переменных окружения.

    Если хотя бы одна переменная отсутствует или пуста,
    выводится сообщение об ошибке и выполнение программы
    завершается.

    Args:
        keys:
            Список обязательных переменных окружения.

    Returns:
        None
    """
    missing_or_empty = [k for k in keys if not os.getenv(k, "").strip()]
    if missing_or_empty:
        logger.error(
            "Не заданы обязательные переменные окружения: %s. "
            "Проверь .env файл в корне проекта или запусти ~check_env.py",
            missing_or_empty
        )
        sys.exit(1)
        # raise EnvironmentError(f"Пустые или отсутствующие переменные .env: {missing_or_empty}")


def load_env() -> None:
    """
    Загружает переменные окружения из .env файла.

    После загрузки проверяет наличие обязательных переменных,
    устанавливает рабочую директорию проекта и делает её
    текущей.

    Returns:
        None
    """
    load_dotenv()
    _check_env_vars(REQUIRED_ENV_VARS)
    root_dir = os.getenv('ROOT_DIR')
    os.chdir(root_dir)
    return None