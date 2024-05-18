import pymysql, os, subprocess

class DatabaseConnector:
    def __init__(self, database = None):
        self.CONTAINER_NAME = "db"
        self.db = pymysql.connect(
            host=self.get_container_ip(),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            db=database if database is not None else os.environ.get("DB_POLISWAG")
        )

    def get_container_ip(self):
        command = "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' db"
        result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            raise RuntimeError(f"Failed to obtain IP address for container {self.CONTAINER_NAME}")

    def get_data_from_database(self, query):
        with self.db.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

        if len(results) == 1:
            # If there's only one row, return it as a single object
            obj = {}
            for i, col in enumerate(columns):
                obj[col] = results[0][i]
            return obj
        else:
            # If there are multiple rows, return them as a list of objects
            objects_list = []
            for row in results:
                obj = {}
                for i, col in enumerate(columns):
                    obj[col] = row[i]
                objects_list.append(obj)
            return objects_list

    def execute_query_to_database(self, query):
        with self.db.cursor() as cursor:
            cursor.execute(query)
            affected_rows = cursor.rowcount
            self.db.commit()

        return affected_rows
