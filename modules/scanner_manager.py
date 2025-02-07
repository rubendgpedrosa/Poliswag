class ScannerManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag

    def start_pokestop_scan(self):
        # fetch_new_pvp_data()
        self.update_last_scanned_date(self.poliswag.utility.time_now())
        self.update_quest_scanning_state(0)

    def update_last_scanned_date(self, lastScannedDate):
        self.poliswag.db.execute_query_to_database(
            f"UPDATE poliswag SET last_scanned_date = '{lastScannedDate}';"
        )
        self.poliswag.utility.log_to_file(
            f"New last_scanned_date set to {lastScannedDate}"
        )

    def update_quest_scanning_state(self, state=1):
        self.poliswag.db.execute_query_to_database(
            f"UPDATE poliswag SET scanned = '{state}';"
        )
        self.poliswag.utility.log_to_file(
            f"{'Finished' if state == 1 else 'Started'} quest scanning mode"
        )

    def is_day_change(self):
        last_scanned_date = self.poliswag.db.get_data_from_database(
            f"SELECT last_scanned_date FROM poliswag WHERE last_scanned_date < '{self.poliswag.utility.time_now()}' OR last_scanned_date IS NULL;"
        )
        if len(last_scanned_date) > 0:
            self.poliswag.utility.log_to_file("Day change encountered")
            self.start_pokestop_scan()
            return True
        return False
