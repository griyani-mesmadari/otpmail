import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
import config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path=config.DB_PATH):
        self.db_path = db_path
        self._init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Orders table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,           -- 'gmail' or 'outlook'
                email_address TEXT NOT NULL,
                email_key TEXT,
                email_timestamp INTEGER,
                status TEXT DEFAULT 'waiting',   -- 'waiting', 'completed', 'cancelled', 'expired'
                cost INTEGER DEFAULT 200,
                otp_code TEXT,
                otp_sender TEXT,
                otp_subject TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            );
            """)

            # Topups table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS topups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                proof_file_id TEXT,
                status TEXT DEFAULT 'pending',   -- 'pending', 'approved', 'rejected'
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            );
            """)

            # Transactions table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,              -- 'topup', 'otp_deduct', 'admin_adjust', 'refund'
                amount INTEGER NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            );
            """)

            # Settings table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)

            conn.commit()
            logger.info("Database initialized successfully at %s", self.db_path)

    # ================= USER METHODS =================
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                # Update info if changed
                cursor.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (username or row["username"], first_name or row["first_name"], user_id)
                )
                conn.commit()
                return dict(row)
            else:
                cursor.execute(
                    "INSERT INTO users (user_id, username, first_name, balance, total_spent) VALUES (?, ?, ?, 0, 0)",
                    (user_id, username, first_name)
                )
                conn.commit()
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                return dict(cursor.fetchone())

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_balance(self, user_id: int, amount: int, tx_type: str, description: str = "") -> int:
        """
        Updates user balance. amount can be positive or negative.
        Returns new balance.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance, total_spent FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                raise ValueError(f"User {user_id} not found")

            old_balance = user["balance"]
            new_balance = old_balance + amount
            if new_balance < 0:
                raise ValueError("Insufficient balance")

            total_spent = user["total_spent"]
            if amount < 0 and tx_type == "otp_deduct":
                total_spent += abs(amount)

            cursor.execute(
                "UPDATE users SET balance = ?, total_spent = ? WHERE user_id = ?",
                (new_balance, total_spent, user_id)
            )
            cursor.execute(
                "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, ?, ?, ?)",
                (user_id, tx_type, amount, description)
            )
            conn.commit()
            return new_balance

    # ================= ORDER METHODS =================
    def create_order(self, user_id: int, service: str, email_address: str, email_key: str = None, email_timestamp: int = None, cost: int = 200) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders (user_id, service, email_address, email_key, email_timestamp, status, cost)
                VALUES (?, ?, ?, ?, ?, 'waiting', ?)
            """, (user_id, service, email_address, email_key, email_timestamp, cost))
            conn.commit()
            return cursor.lastrowid

    def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_order_by_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orders WHERE user_id = ? AND status = 'waiting' ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def complete_order(self, order_id: int, otp_code: str, sender: str = "", subject: str = "") -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orders
                SET status = 'completed',
                    otp_code = ?,
                    otp_sender = ?,
                    otp_subject = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'waiting'
            """, (otp_code, sender, subject, order_id))
            conn.commit()
            return cursor.rowcount > 0

    def cancel_order(self, order_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE orders SET status = 'cancelled' WHERE id = ? AND status = 'waiting'", (order_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_user_orders(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    # ================= TOPUP METHODS =================
    def create_topup(self, user_id: int, amount: int, proof_file_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO topups (user_id, amount, proof_file_id, status) VALUES (?, ?, ?, 'pending')",
                (user_id, amount, proof_file_id)
            )
            conn.commit()
            return cursor.lastrowid

    def get_topup(self, topup_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def approve_topup(self, topup_id: int, admin_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM topups WHERE id = ? AND status = 'pending'", (topup_id,))
            topup = cursor.fetchone()
            if not topup:
                return None

            user_id = topup["user_id"]
            amount = topup["amount"]

            cursor.execute("""
                UPDATE topups
                SET status = 'approved', admin_id = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (admin_id, topup_id))

            conn.commit()

        # Update balance through update_balance method to record transaction
        new_balance = self.update_balance(
            user_id=user_id,
            amount=amount,
            tx_type="topup",
            description=f"Top Up via Admin (Topup ID #{topup_id})"
        )
        return {
            "topup_id": topup_id,
            "user_id": user_id,
            "amount": amount,
            "new_balance": new_balance
        }

    def reject_topup(self, topup_id: int, admin_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM topups WHERE id = ? AND status = 'pending'", (topup_id,))
            topup = cursor.fetchone()
            if not topup:
                return None

            cursor.execute("""
                UPDATE topups
                SET status = 'rejected', admin_id = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (admin_id, topup_id))
            conn.commit()
            return dict(topup)

    # ================= STATS & ADMIN =================
    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = cursor.fetchone()["total_users"]

            cursor.execute("SELECT COUNT(*) as total_orders, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders FROM orders")
            order_data = cursor.fetchone()
            total_orders = order_data["total_orders"] or 0
            completed_orders = order_data["completed_orders"] or 0

            cursor.execute("SELECT SUM(amount) as total_revenue FROM transactions WHERE type = 'otp_deduct'")
            rev = cursor.fetchone()["total_revenue"]
            total_revenue = abs(rev) if rev else 0

            cursor.execute("SELECT SUM(balance) as total_user_balance FROM users")
            user_balance = cursor.fetchone()["total_user_balance"] or 0

            return {
                "total_users": total_users,
                "total_orders": total_orders,
                "completed_orders": completed_orders,
                "total_revenue": total_revenue,
                "total_user_balance": user_balance
            }

    def get_all_user_ids(self) -> List[int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            return [r["user_id"] for r in cursor.fetchall()]

# Global Database Instance
db = Database()
