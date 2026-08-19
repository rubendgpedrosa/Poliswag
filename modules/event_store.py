class EventStore:
    def __init__(self, db):
        self.db = db

    async def get_excluded_types(self):
        return await self.db.get_data_from_database(
            "SELECT type FROM excluded_event_type"
        )

    async def get_all_event_types(self):
        return await self.db.get_data_from_database(
            "SELECT event_type FROM event GROUP BY event_type"
        )

    async def is_excluded(self, event_type):
        rows = await self.db.get_data_from_database(
            "SELECT type FROM excluded_event_type WHERE type = %s",
            params=(event_type,),
        )
        return len(rows) > 0

    async def add_excluded(self, event_type):
        await self.db.execute_query_to_database(
            "INSERT INTO excluded_event_type (type) VALUES (%s)",
            params=(event_type,),
        )

    async def remove_excluded(self, event_type):
        return await self.db.execute_query_to_database(
            "DELETE FROM excluded_event_type WHERE type = %s",
            params=(event_type,),
        )

    async def clear_excluded(self):
        count_rows = await self.db.get_data_from_database(
            "SELECT COUNT(*) as count FROM excluded_event_type"
        )
        count = count_rows[0]["count"] if count_rows else 0
        await self.db.execute_query_to_database("DELETE FROM excluded_event_type")
        return count
