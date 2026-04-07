from datetime import datetime


class TrackerStore:
    def __init__(self, db):
        self.db = db

    def get_all(self):
        return self.db.get_data_from_database(
            "SELECT target, creator, createddate FROM tracked_quest_reward ORDER BY createddate DESC"
        )

    def exists(self, target):
        rows = self.db.get_data_from_database(
            "SELECT target FROM tracked_quest_reward WHERE target = %s",
            params=(target,),
        )
        return len(rows) > 0

    def add(self, target, creator):
        self.db.execute_query_to_database(
            "INSERT INTO tracked_quest_reward (target, creator, createddate) VALUES (%s, %s, %s)",
            params=(target, creator, datetime.now()),
        )

    def remove(self, target):
        return self.db.execute_query_to_database(
            "DELETE FROM tracked_quest_reward WHERE target = %s",
            params=(target,),
        )

    def clear(self):
        count_rows = self.db.get_data_from_database(
            "SELECT COUNT(*) as count FROM tracked_quest_reward"
        )
        count = count_rows[0]["count"] if count_rows else 0
        self.db.execute_query_to_database("DELETE FROM tracked_quest_reward")
        return count
