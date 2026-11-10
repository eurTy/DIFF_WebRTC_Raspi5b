import csv
from pathlib import Path
import tempfile
import unittest

from pc_inspection.io_plan import build_points, export_csv


class IoPlanTests(unittest.TestCase):
    def test_count_names_addresses_and_module_channels(self):
        points = build_points()
        self.assertEqual(len(points), 100)
        self.assertEqual([sum(p.kind == kind for p in points)
                          for kind in ("DI", "DO", "AI", "AO")], [48, 32, 16, 4])
        self.assertEqual(len({p.name for p in points}), 100)
        self.assertEqual(len({(p.kind, p.address) for p in points}), 100)
        self.assertEqual({p.module for p in points if p.kind == "DI"},
                         {"DI-1", "DI-2", "DI-3"})
        self.assertEqual({p.module for p in points if p.kind == "DO"},
                         {"DO-1", "DO-2"})
        self.assertEqual({p.module for p in points if p.kind == "AI"},
                         {"AI-1", "AI-2"})
        self.assertEqual({p.module for p in points if p.kind == "AO"}, {"AO-1"})

    def test_address_ranges_do_not_overlap(self):
        points = build_points()
        self.assertEqual((points[0].address, points[47].address), ("I0.0", "I5.7"))
        self.assertEqual((points[48].address, points[79].address), ("Q0.0", "Q3.7"))
        self.assertEqual((points[80].address, points[95].address), ("IW64", "IW94"))
        self.assertEqual((points[96].address, points[99].address), ("QW64", "QW70"))

    def test_csv_is_planning_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "io_map.csv"
            self.assertEqual(export_csv(path), 100)
            with path.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 100)
            self.assertTrue(all(row["implementation"] == "PLANNED_SIMULATION"
                                for row in rows))


if __name__ == "__main__":
    unittest.main()
