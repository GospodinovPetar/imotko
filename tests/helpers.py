import tempfile
import unittest
from pathlib import Path
from unittest import mock

from imotko import store
from imotko.geo import driving, transit


class TempDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(store, "DB_PATH", Path(self.tmp.name) / "t.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        store._create_schema.cache_clear()
        self.addCleanup(store._create_schema.cache_clear)
        driving.forget_calibration()
        self.addCleanup(driving.forget_calibration)
        # разписанията никога не се свалят в тестовете
        for patcher in (
            mock.patch.object(transit, "FEED_DIR", Path(self.tmp.name) / "gtfs"),
            mock.patch.object(transit, "download_feed", return_value=False),
            mock.patch.dict(transit._timetables_by_city, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
