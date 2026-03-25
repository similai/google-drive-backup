"""Core backend prototype for Luodong JHS cooperative cloud inventory system.

Phase 1 scope:
- Product master (barcode, name, prices, stock)
- Purchase stock-in transactions
- POS sale by barcode scan
- Low-stock query
- Duplicate scan throttling guard
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Product:
    barcode: str
    name: str
    cost_price: float
    sale_price: float
    stock_qty: int
    safety_stock: int = 0
    enabled: bool = True


class CoopInventorySystem:
    def __init__(self, db_path: str = "coop_inventory.db", duplicate_window_sec: float = 0.25) -> None:
        self.db_path = Path(db_path)
        self.duplicate_window_sec = duplicate_window_sec
        self._recent_scans: Dict[str, float] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    barcode TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cost_price REAL NOT NULL,
                    sale_price REAL NOT NULL,
                    stock_qty INTEGER NOT NULL DEFAULT 0,
                    safety_stock INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory_txns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    barcode TEXT NOT NULL,
                    txn_type TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    note TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (barcode) REFERENCES products(barcode)
                );
                """
            )

    def upsert_product(self, product: Product) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO products (barcode, name, cost_price, sale_price, stock_qty, safety_stock, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(barcode) DO UPDATE SET
                    name=excluded.name,
                    cost_price=excluded.cost_price,
                    sale_price=excluded.sale_price,
                    stock_qty=excluded.stock_qty,
                    safety_stock=excluded.safety_stock,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    product.barcode,
                    product.name,
                    product.cost_price,
                    product.sale_price,
                    product.stock_qty,
                    product.safety_stock,
                    1 if product.enabled else 0,
                    now,
                ),
            )

    def get_product(self, barcode: str) -> Optional[Product]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM products WHERE barcode = ?", (barcode,)).fetchone()
        if row is None:
            return None
        return Product(
            barcode=row["barcode"],
            name=row["name"],
            cost_price=row["cost_price"],
            sale_price=row["sale_price"],
            stock_qty=row["stock_qty"],
            safety_stock=row["safety_stock"],
            enabled=bool(row["enabled"]),
        )

    def stock_in(self, barcode: str, qty: int, note: str = "purchase") -> int:
        if qty <= 0:
            raise ValueError("qty must be > 0")

        with self._connect() as conn:
            row = conn.execute("SELECT stock_qty FROM products WHERE barcode = ?", (barcode,)).fetchone()
            if row is None:
                raise KeyError(f"barcode not found: {barcode}")

            new_qty = row["stock_qty"] + qty
            now = time.time()
            conn.execute(
                "UPDATE products SET stock_qty = ?, updated_at = ? WHERE barcode = ?",
                (new_qty, now, barcode),
            )
            conn.execute(
                "INSERT INTO inventory_txns (barcode, txn_type, qty, note, created_at) VALUES (?, 'IN', ?, ?, ?)",
                (barcode, qty, note, now),
            )
            return new_qty

    def sale_by_scan(self, barcode: str, qty: int = 1, when: Optional[float] = None) -> int:
        if qty <= 0:
            raise ValueError("qty must be > 0")

        timestamp = time.time() if when is None else when
        last = self._recent_scans.get(barcode)
        if last is not None and timestamp - last < self.duplicate_window_sec:
            raise RuntimeError("duplicate scan throttled")
        self._recent_scans[barcode] = timestamp

        with self._connect() as conn:
            row = conn.execute(
                "SELECT stock_qty, enabled FROM products WHERE barcode = ?", (barcode,)
            ).fetchone()
            if row is None:
                raise KeyError(f"barcode not found: {barcode}")
            if row["enabled"] == 0:
                raise RuntimeError("product is disabled")
            if row["stock_qty"] < qty:
                raise RuntimeError("insufficient stock")

            new_qty = row["stock_qty"] - qty
            now = time.time()
            conn.execute(
                "UPDATE products SET stock_qty = ?, updated_at = ? WHERE barcode = ?",
                (new_qty, now, barcode),
            )
            conn.execute(
                "INSERT INTO inventory_txns (barcode, txn_type, qty, note, created_at) VALUES (?, 'OUT', ?, 'sale', ?)",
                (barcode, qty, now),
            )
            return new_qty

    def low_stock_products(self) -> List[Product]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM products WHERE enabled = 1 AND stock_qty <= safety_stock ORDER BY stock_qty ASC"
            ).fetchall()
        return [
            Product(
                barcode=row["barcode"],
                name=row["name"],
                cost_price=row["cost_price"],
                sale_price=row["sale_price"],
                stock_qty=row["stock_qty"],
                safety_stock=row["safety_stock"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]
