from datetime import datetime


class TrackerStore:
    def __init__(self, db):
        self.db = db

    async def get_all(self):
        return await self.db.get_data_from_database(
            "SELECT target, creator, createddate FROM tracked_quest_reward ORDER BY createddate DESC"
        )

    async def exists(self, target):
        rows = await self.db.get_data_from_database(
            "SELECT target FROM tracked_quest_reward WHERE target = %s",
            params=(target,),
        )
        return len(rows) > 0

    async def add(self, target, creator):
        await self.db.execute_query_to_database(
            "INSERT INTO tracked_quest_reward (target, creator, createddate) VALUES (%s, %s, %s)",
            params=(target, creator, datetime.now()),
        )

    async def remove(self, target):
        return await self.db.execute_query_to_database(
            "DELETE FROM tracked_quest_reward WHERE target = %s",
            params=(target,),
        )

    async def clear(self):
        count_rows = await self.db.get_data_from_database(
            "SELECT COUNT(*) as count FROM tracked_quest_reward"
        )
        count = count_rows[0]["count"] if count_rows else 0
        await self.db.execute_query_to_database("DELETE FROM tracked_quest_reward")
        return count
