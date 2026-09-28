import colorlog
import logging
import sys

def create_logger(name: str = __name__) -> logging.Logger:
    """
    Создаёт и настраивает экземпляр логгера.

    Если логгер с указанным именем уже существует и содержит
    обработчик, возвращается существующий экземпляр без
    повторной настройки.

    Args:
        name:
            Имя логгера. По умолчанию используется имя текущего
            модуля.

    Returns:
        logging.Logger:
            Настроенный экземпляр логгера.
    """
    text_ = 25
    logging.addLevelName(text_, "TEXT")

    def text(self, msg, *args, **kwargs):
        if self.isEnabledFor(text_):
            self._log(text_, msg, args, **kwargs)

    logging.Logger.text = text
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    handler = colorlog.StreamHandler(sys.stdout)
    handler.setFormatter(colorlog.ColoredFormatter(
        '%(log_color)s%(message)s',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
            "TEXT": "reset"
        }
    ))

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger

if __name__ == '__main__':
    logger = create_logger()
    logger.text('test')