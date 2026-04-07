import pymysql
import logging
from modules.config import Config


class DatabaseConnector:
    def __init__(self, database=None):
        self.database = database or Config.DB_POLISWAG
        self.db = self.connect_to_db()

    def connect_to_db(self):
        try:
            db = pymysql.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                db=self.database,
            )
            logging.info("Successfully connected to the database.")
            return db
        except pymysql.MySQLError as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def get_data_from_database(self, query, retries=3, params=None):
        return self.execute_query(query, fetch=True, retries=retries, params=params)

    def execute_query_to_database(self, query, retries=3, params=None):
        return self.execute_query(query, fetch=False, retries=retries, params=params)

    def execute_query(self, query, fetch, retries, params=None):
        last_error = None
        for attempt in range(retries):
            try:
                with self.db.cursor() as cursor:
                    cursor.execute(query, params)
                    if fetch:
                        results = cursor.fetchall()
                        if cursor.description is None:
                            self.db.commit()
                            return []
                        columns = [col[0] for col in cursor.description]
                        self.db.commit()
                        return [
                            {columns[i]: row[i] for i in range(len(columns))}
                            for row in results
                        ]
                    else:
                        affected_rows = cursor.rowcount
                        self.db.commit()
                        return affected_rows
            except pymysql.MySQLError as e:
                last_error = e
                logging.error(f"Database error on attempt {attempt + 1}: {e}")
                if (
                    "server has gone away" in str(e).lower()
                    or "lost connection" in str(e).lower()
                ):
                    try:
                        self.db = self.connect_to_db()
                    except Exception:
                        pass  # will retry or raise at end
                else:
                    raise
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                raise

        raise RuntimeError(f"Exceeded maximum retry attempts: {last_error}")
