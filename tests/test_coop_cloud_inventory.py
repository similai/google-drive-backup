import tempfile
import unittest

from coop_cloud_inventory import CoopInventorySystem, Product


class CoopInventorySystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db")
        self.system = CoopInventorySystem(db_path=self.temp_db.name, duplicate_window_sec=0.3)
        self.system.upsert_product(
            Product(
                barcode="4710001000012",
                name="麥香紅茶",
                cost_price=8.0,
                sale_price=12.0,
                stock_qty=10,
                safety_stock=3,
            )
        )

    def test_sale_by_scan_reduces_stock(self) -> None:
        remain = self.system.sale_by_scan("4710001000012")
        self.assertEqual(remain, 9)

    def test_duplicate_scan_is_throttled(self) -> None:
        t0 = 1000.0
        self.system.sale_by_scan("4710001000012", when=t0)
        with self.assertRaises(RuntimeError):
            self.system.sale_by_scan("4710001000012", when=t0 + 0.1)

    def test_stock_in_increases_stock(self) -> None:
        new_qty = self.system.stock_in("4710001000012", 5)
        self.assertEqual(new_qty, 15)

    def test_low_stock_query(self) -> None:
        self.system.sale_by_scan("4710001000012", qty=7)
        low = self.system.low_stock_products()
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0].barcode, "4710001000012")


if __name__ == "__main__":
    unittest.main()
