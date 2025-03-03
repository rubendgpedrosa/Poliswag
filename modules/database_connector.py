import pymysql
import os
import logging


class DatabaseConnector:
    def __init__(self, database=None):
        self.database = database or os.environ.get("DB_POLISWAG")
        self.db = self.connect_to_db()

    def connect_to_db(self):
        try:
            db = pymysql.connect(
                host=os.environ.get("DB_HOST"),
                port=int(os.environ.get("DB_PORT")),
                user=os.environ.get("DB_USER"),
                password=os.environ.get("DB_PASSWORD"),
                db=self.database,
            )
            logging.info("Successfully connected to the database.")
            return db
        except pymysql.MySQLError as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def get_data_from_database(self, query, retries=3):
        return self.execute_query(query, fetch=True, retries=retries)

    def execute_query_to_database(self, query, retries=3):
        return self.execute_query(query, fetch=False, retries=retries)

    def execute_query(self, query, fetch, retries):
        for attempt in range(retries):
            try:
                with self.db.cursor() as cursor:
                    cursor.execute(query)
                    if fetch:
                        results = cursor.fetchall()
                        columns = [col[0] for col in cursor.description]
                        self.db.commit()

                        if len(results) == 1:
                            data = {
                                columns[i]: results[0][i] for i in range(len(columns))
                            }
                            logging.info(f"Query returned a single result: {data}")
                            return data
                        else:
                            data_list = [
                                {columns[i]: row[i] for i in range(len(columns))}
                                for row in results
                            ]
                            logging.info(
                                f"Query returned multiple results: {data_list}"
                            )
                            return data_list
                    else:
                        affected_rows = cursor.rowcount
                        self.db.commit()
                        logging.info(
                            f"Query executed successfully, affected rows: {affected_rows}"
                        )
                        return affected_rows
            except pymysql.MySQLError as e:
                logging.error(f"Database error on attempt {attempt + 1}: {e}")
                if (
                    "server has gone away" in str(e).lower()
                    or "lost connection" in str(e).lower()
                ):
                    self.db = self.connect_to_db()
                else:
                    raise
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                self.db = self.connect_to_db()
                raise

        raise RuntimeError("Exceeded maximum retry attempts")
