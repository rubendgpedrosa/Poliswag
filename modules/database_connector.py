import pymysql
import os
import subprocess
import logging


class DatabaseConnector:
    def __init__(self, database=None):
        self.CONTAINER_NAME = "db"
        self.database = (
            database if database is not None else os.environ.get("DB_POLISWAG")
        )
        self.connect_to_db()

    def connect_to_db(self):
        try:
            self.db = pymysql.connect(
                host=self.get_container_ip(),
                user=os.environ.get("DB_USER"),
                password=os.environ.get("DB_PASSWORD"),
                db=self.database,
            )
            logging.info("Successfully connected to the database.")
        except pymysql.MySQLError as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def get_container_ip(self):
        command = "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' db"
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            logging.info(
                f"Obtained IP address for container {self.CONTAINER_NAME}: {result.stdout.strip()}"
            )
            return result.stdout.strip()
        else:
            logging.error(
                f"Failed to obtain IP address for container {self.CONTAINER_NAME}: {result.stderr}"
            )
            raise RuntimeError(
                f"Failed to obtain IP address for container {self.CONTAINER_NAME}"
            )

    def get_data_from_database(self, query, retries=3):
        for attempt in range(retries):
            try:
                with self.db.cursor() as cursor:
                    cursor.execute(query)
                    results = cursor.fetchall()
                    columns = [col[0] for col in cursor.description]
                    self.db.commit()

                if len(results) == 1:
                    obj = {columns[i]: results[0][i] for i in range(len(columns))}
                    logging.info(f"Query returned a single result: {obj}")
                    return obj
                else:
                    objects_list = [
                        {columns[i]: row[i] for i in range(len(columns))}
                        for row in results
                    ]
                    logging.info(f"Query returned multiple results: {objects_list}")
                    return objects_list
            except pymysql.MySQLError as e:
                logging.error(f"Database error on attempt {attempt + 1}: {e}")
                if (
                    "server has gone away" in str(e).lower()
                    or "lost connection" in str(e).lower()
                ):
                    self.connect_to_db()
                else:
                    raise
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                raise
        raise RuntimeError("Exceeded maximum retry attempts")

    def execute_query_to_database(self, query, retries=3):
        for attempt in range(retries):
            try:
                with self.db.cursor() as cursor:
                    cursor.execute(query)
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
                    self.connect_to_db()
                else:
                    raise
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                raise
        raise RuntimeError("Exceeded maximum retry attempts")
