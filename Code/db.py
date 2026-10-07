from   Code.logger import create_logger
# from   Code.load_env import load_env
from   abc import ABC, abstractmethod
import clickhouse_connect
import sqlalchemy
import pandas as pd
import os

"""
Модуль для подключения и выполнения SQL-запросов к различным СУБД.

Поддерживаются:
    - ClickHouse;
    - MySQL (через SQLAlchemy).

Все параметры подключения считываются из .env файла.
"""

# load_env()
logger = create_logger()


class DBcon(ABC):
    """
    Абстрактный базовый класс для подключения к базам данных.

    Определяет единый интерфейс для создания соединения,
    выполнения SQL-запросов и корректного закрытия подключения.
    """
    @abstractmethod
    def connect(self):
        """
        Абстрактный базовый класс для подключения к базам данных.

        Определяет единый интерфейс для создания соединения,
        выполнения SQL-запросов и корректного закрытия подключения.
        """
        pass

    @abstractmethod
    def query(self):
        """
        Выполняет SQL-запрос.

        Returns:
            pd.DataFrame | None:
                Результат выполнения запроса.
        """
        pass

    @abstractmethod
    def close(self):
        """
        Закрывает соединение с базой данных.

        Returns:
            None
        """
        pass

class ClickHouseConn(DBcon):
    """
    Класс подключения к базе данных ClickHouse.

    Авторизация выполняется через переменные окружения
    с префиксом CLICKHOUSE_.

    Attributes:
        dbname (str | None):
            Имя базы данных.
        conn:
            Активное соединение с ClickHouse.
    """
    def __init__(self, dbname: str = None):
        """
        Создаёт объект подключения к ClickHouse.

        Args:
            dbname:
                Имя базы данных.
        """
        self.prefix = 'CLICKHOUSE_'
        self.dbname = dbname
        self.conn   = None
        self.__auth = {
            'HOST'      :'',
            'PORT'      :'',
            'USER'      :'',
            'PASSWORD'  :''
        }
        self.conn   = self.connect()


    def connect(self):
        """
        Создаёт подключение к ClickHouse.

        Если соединение уже активно,
        используется существующее.

        Returns:
            clickhouse_connect.driver.Client | None:
                Активное соединение либо None.
        """
        if self.conn is not None:
            try:
                self.conn.ping()
                logger.info("Соединение с %s уже активно, использование старого подключения", self.prefix[:-1])
                return self.conn
            except Exception as Ex:
                logger.warning("Существующее соединение с %s не отвечает, пересоздаие", self.prefix[:-1])
                self.conn = None

        for key in self.__auth.keys():
            env_key = os.getenv(self.prefix + key)
            if not os.getenv(self.prefix + key):
                logger.error(
                    'Нет необходимого ключа в .env: %s\nПодключение отменено',
                    self.prefix + key
                )
                return None

            self.__auth[key] = int(env_key) if key == 'PORT' else env_key
        try:
            client = clickhouse_connect.get_client(
                host        = self.__auth['HOST'],
                port        = self.__auth['PORT'],
                username    = self.__auth['USER'],
                password    = self.__auth['PASSWORD'],
                database    = self.dbname
            )
            logger.info("Успешное подключение к %s!", self.prefix[:-1])
            return client
        except Exception as Ex:
            logger.error(
                'Не удалось создать соединение с %s\n%s\nПодключение отменено\n',
                 self.prefix[:-1],Ex
            )
            return None
    def query(self,
              qbody: str = 'SELECT 1',
              params: dict[str, object] | None = None
              ) -> pd.DataFrame | None:
        """
        Выполняет SQL-запрос к ClickHouse.

        Args:
            qbody:
                SQL-запрос.
            params:
                 Словарь параметров запроса. Имена параметров
                должны соответствовать именованным параметрам
                в qbody.

        Returns:
            pd.DataFrame | None:
                Результат запроса.
        """
        if self.conn is None:
            logger.error(
                'Нет созданного соединения с %s\nОтмена запроса',
                 self.prefix[:-1]
            )
            return None
        try:
            # query = self.conn.query_df(query = qbody)
            query = self.conn.query_df(
                query=qbody,
                parameters=params or {}
            )
            # logger.info('Shape: %s', query.shape)
        except Exception as Ex:
            logger.error('Ошибка выполнения запроса!\n%s\n', Ex)
            return None
        return query

    def close(self):
        """
        Закрывает соединение с ClickHouse.

        Returns:
            None
        """
        if self.conn is None:
            #logger.warning('Нет активного соединения с %s для закрытия', self.prefix[:-1])
            return
        try:
            self.conn.close()
            # logger.info('Соединение с %s закрыто', self.prefix[:-1])
            self.conn = None
        except Exception as Ex:
            logger.error('Ошибка при закрытии соединения с %s\n%s', self.prefix[:-1], Ex)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class MySQLConn(DBcon):
    """
    Класс подключения к MySQL через SQLAlchemy.

    Авторизация выполняется через переменные окружения
    с префиксом MYSQL_.

    Attributes:
        dbname (str | None):
            Имя базы данных.
        engine (sqlalchemy.Engine | None):
            SQLAlchemy Engine.
    """
    def __init__(self, dbname: str = None):
        """
        Создаёт объект подключения к MySQL.

        Args:
            dbname:
                Имя базы данных.
        """
        self.prefix      = 'MYSQL_'
        self.driver_name = "mysql+mysqldb"
        self.dbname      = dbname
        self.engine      = None
        self.__auth      = {
            'HOST'      :'',
            'PORT'      :'',
            'USER'      :'',
            'PASSWORD'  :''
        }
        self.engine = self.connect()


    def connect(self):
        """
        Создаёт SQLAlchemy Engine.

        При наличии активного соединения
        используется существующий Engine.

        Returns:
            sqlalchemy.Engine | None:
                Объект Engine либо None.
        """
        if self.engine is not None:
            try:
                with self.engine.connect():
                    pass
                logger.info("Соединение с %s уже активно, использование старого подключения", self.prefix[:-1])
                return self.engine
            except Exception as Ex:
                logger.warning("Существующее соединение с %s не отвечает, пересоздание\n%s", self.prefix[:-1],Ex)
                self.engine = None

        for key in self.__auth.keys():
            env_key = os.getenv(self.prefix + key)
            if not os.getenv(self.prefix + key):
                logger.error(
                    'Нет необходимого ключа в .env: %s\nПодключение отменено',
                    self.prefix + key
                )
                return None

            self.__auth[key] = int(env_key) if key == 'PORT' else env_key
        try:
            conn_url = sqlalchemy.URL.create(
                host        = self.__auth['HOST'],
                port        = self.__auth['PORT'],
                username    = self.__auth['USER'],
                password    = self.__auth['PASSWORD'],
                database    = self.dbname,
                drivername  = self.driver_name
            )
            engine = sqlalchemy.create_engine(conn_url, pool_pre_ping=True)
            with engine.connect():  # проверка пула на контекстном менеджере
                pass
            logger.info("Успешное подключение к %s!", self.prefix[:-1])
            return engine

        except Exception as Ex:
            logger.error(
                'Не удалось создать соединение с %s\n%s\nПодключение отменено\n',
                 self.prefix[:-1],Ex
            )
            return None

    def query(self,
              qbody: str = 'SELECT 1',
              params: dict[str, object] | None = None
              ) -> pd.DataFrame | None:
        """
        Выполняет SQL-запрос к MySQL.

        Args:
            qbody:
                SQL-запрос.
            params
                 Словарь параметров запроса. Имена параметров
                должны соответствовать именованным параметрам
                в qbody.

        Returns:
            pd.DataFrame | None:
                Результат выполнения запроса.
        """
        if self.engine is None:
            logger.error(
                'Нет созданного соединения с %s\nОтмена запроса',
                 self.prefix[:-1]
            )
            return None
        try:
            query = pd.read_sql(sql = sqlalchemy.text(qbody), con = self.engine, params=params or {})
            # logger.info('Shape: %s', query.shape)
        except Exception as Ex:
            logger.error('Ошибка выполнения запроса!\n%s\n', Ex)
            return None
        return query

    def close(self):
        """
        Освобождает ресурсы SQLAlchemy Engine.

        Returns:
            None
        """
        if self.engine is None:
            #logger.warning('Нет активного соединения с %s для закрытия', self.prefix[:-1])
            return
        try:
            self.engine.dispose()
            # logger.info('Соединение с %s закрыто', self.prefix[:-1])
            self.engine = None
        except Exception as Ex:
            logger.error('Ошибка при закрытии соединения с %s\n%s', self.prefix[:-1], Ex)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
if __name__ == '__main__':
    pass