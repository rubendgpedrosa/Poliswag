"""Stand-ins shared by the tests."""

from unittest.mock import AsyncMock, MagicMock


class FakeHandle:
    """A database handle (poliswag.db, .site_db, .poracle_db, ...) for tests.

    unit() runs the work at once, in the test's own thread, on `connection`
    (a MagicMock unless one is given) and records the call, so a test can
    both drive the cursor the work sees and check how the unit was asked for.
    The single-statement methods are AsyncMocks that return nothing.
    """

    def __init__(self, connection=None, database="test"):
        self.database = database
        self.connection = connection if connection is not None else MagicMock()
        # One (work, args, options) per unit() call, in order.
        self.units = []
        self.get_data_from_database = AsyncMock(return_value=[])
        self.execute_query_to_database = AsyncMock(return_value=0)
        self.execute_transaction = AsyncMock()

    @property
    def cursor(self):
        """What `with connection.cursor() as cursor:` yields inside the work."""
        return self.connection.cursor.return_value.__enter__.return_value

    async def unit(self, work, *args, **options):
        self.units.append((work, args, options))
        return work(self.connection, *args)
