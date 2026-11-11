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
                tx_id TEXT,
                order_id TEXT,
                qr_string TEXT,
                checkout_url TEXT,
                expires_at TEXT,
                status TEXT DEFAULT 'pending',   -- 'pending', 'approved', 'rejected', 'cancelled', 'expired'
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            );
            """)

            # Safe migration for existing topups table
            cursor.execute("PRAGMA table_info(topups)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            for col_name, col_type in [
                ("tx_id", "TEXT"),
                ("order_id", "TEXT"),
                ("qr_string", "TEXT"),
                ("checkout_url", "TEXT"),
                ("expires_at", "TEXT"),
            ]:
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE topups ADD COLUMN {col_name} {col_type}")

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
    def is_email_used(self, email_address: str) -> bool:
        """Checks if the email has ever been generated before in any order."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM orders WHERE LOWER(TRIM(email_address)) = LOWER(TRIM(?)) LIMIT 1",
                (email_address,)
            )
            return cursor.fetchone() is not None

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

    def cancel_all_waiting_orders(self, user_id: Optional[int] = None) -> int:
        """Cancels all waiting orders for a user (or globally if user_id is None). Returns count."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                cursor.execute(
                    "UPDATE orders SET status = 'cancelled' WHERE user_id = ? AND status = 'waiting'",
                    (user_id,)
                )
            else:
                cursor.execute(
                    "UPDATE orders SET status = 'cancelled' WHERE status = 'waiting'"
                )
            conn.commit()
            return cursor.rowcount

    def get_waiting_order_ids(self, user_id: Optional[int] = None) -> List[int]:
        """Returns a list of order IDs with status 'waiting' for a user (or globally)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                cursor.execute("SELECT id FROM orders WHERE user_id = ? AND status = 'waiting'", (user_id,))
            else:
                cursor.execute("SELECT id FROM orders WHERE status = 'waiting'")
            return [row["id"] for row in cursor.fetchall()]

    def get_user_orders(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    # ================= TOPUP METHODS =================
    def create_topup(
        self,
        user_id: int,
        amount: int,
        proof_file_id: str = "AUTOGOPAY_WAITING",
        tx_id: Optional[str] = None,
        order_id: Optional[str] = None,
        qr_string: Optional[str] = None,
        checkout_url: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO topups (user_id, amount, proof_file_id, tx_id, order_id, qr_string, checkout_url, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (user_id, amount, proof_file_id, tx_id, order_id, qr_string, checkout_url, expires_at)
            )
            conn.commit()
            return cursor.lastrowid

    def get_topup(self, topup_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_pending_topup_by_tx_id(self, tx_id: str) -> Optional[Dict[str, Any]]:
        """Finds a pending topup matching AutoGoPay transaction_id."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM topups
                WHERE tx_id = ? AND status = 'pending'
                ORDER BY id DESC LIMIT 1
            """, (tx_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_pending_topup_by_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Returns user's latest pending topup created within last 15 minutes."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM topups
                WHERE user_id = ? AND status = 'pending'
                  AND tx_id IS NOT NULL
                  AND datetime(created_at) >= datetime('now', '-15 minutes')
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_pending_autogopay_topups(self) -> List[Dict[str, Any]]:
        """Returns all active pending AutoGoPay topups (created within last 16 minutes) to resume polling."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM topups
                WHERE status = 'pending'
                  AND tx_id IS NOT NULL
                  AND datetime(created_at) >= datetime('now', '-16 minutes')
                ORDER BY id DESC
            """)
            return [dict(r) for r in cursor.fetchall()]

    def cancel_topup(self, topup_id: int, new_status: str = "cancelled") -> bool:
        """Marks a pending topup as cancelled or expired."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE topups
                SET status = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
            """, (new_status, topup_id))
            conn.commit()
            return cursor.rowcount > 0

    def approve_topup(self, topup_id: int, admin_id: int = 0, description: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
                WHERE id = ? AND status = 'pending'
            """, (admin_id, topup_id))

            if cursor.rowcount == 0:
                return None

            conn.commit()

        # Update balance through update_balance method to record transaction
        desc = description or (f"Top Up via Admin (Topup ID #{topup_id})" if admin_id else f"Top Up Otomatis QRIS AutoGoPay (Topup ID #{topup_id})")
        new_balance = self.update_balance(
            user_id=user_id,
            amount=amount,
            tx_type="topup",
            description=desc
        )
        return {
            "topup_id": topup_id,
            "user_id": user_id,
            "amount": amount,
            "new_balance": new_balance
        }

    def find_pending_topup_by_amount(self, amount: int) -> Optional[Dict[str, Any]]:
        """Finds the most recent pending topup matching the exact payment amount."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM topups 
                WHERE amount = ? AND status = 'pending'
                ORDER BY id DESC LIMIT 1
            """, (amount,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pending_topup_amounts(self) -> set:
        """Returns set of all currently pending topup amounts to avoid unique code collisions."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT amount FROM topups WHERE status = 'pending'")
            return {row["amount"] for row in cursor.fetchall()}

    def update_topup_proof(self, topup_id: int, proof_file_id: str) -> bool:
        """Updates payment proof photo for an existing topup."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE topups SET proof_file_id = ? WHERE id = ?", (proof_file_id, topup_id))
            conn.commit()
            return cursor.rowcount > 0

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

    def get_service_analytics(self) -> Dict[str, Any]:
        """
        Analyzes which email services are ordered most and which target services/senders
        the OTPs are actually being used for (e.g. WhatsApp, TikTok, Google, etc.).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Orders by email service (Gmail, Outlook, Hotmail)
            cursor.execute("""
                SELECT service, 
                       COUNT(*) as total_orders,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders
                FROM orders
                GROUP BY service
                ORDER BY total_orders DESC
            """)
            service_counts = [dict(r) for r in cursor.fetchall()]

            # 2. Senders / Platforms breakdown for completed orders
            cursor.execute("""
                SELECT TRIM(otp_sender) as sender, COUNT(*) as count
                FROM orders
                WHERE status = 'completed' AND otp_sender IS NOT NULL AND TRIM(otp_sender) != ''
                GROUP BY TRIM(otp_sender)
                ORDER BY count DESC
                LIMIT 15
            """)
            top_senders = [dict(r) for r in cursor.fetchall()]

            # 3. Common target platform keyword detection from sender & subject
            cursor.execute("""
                SELECT otp_sender, otp_subject
                FROM orders
                WHERE status = 'completed'
            """)
            all_completed = cursor.fetchall()

            platforms = {
                "TikTok": 0, "WhatsApp": 0, "Telegram": 0, "Google": 0,
                "Shopee": 0, "Tokopedia": 0, "Facebook": 0, "Instagram": 0,
                "Twitter / X": 0, "OpenAI / ChatGPT": 0, "Discord": 0,
                "Netflix": 0, "Steam": 0, "Microsoft": 0, "Lain-lain": 0
            }

            for row in all_completed:
                text = f"{row['otp_sender'] or ''} {row['otp_subject'] or ''}".lower()
                matched = False
                for p in platforms:
                    if p == "Lain-lain":
                        continue
                    keywords = p.lower().split(" / ")
                    if any(k in text for k in keywords):
                        platforms[p] += 1
                        matched = True
                        break
                if not matched:
                    platforms["Lain-lain"] += 1

            sorted_platforms = [
                {"platform": k, "count": v}
                for k, v in sorted(platforms.items(), key=lambda x: x[1], reverse=True)
                if v > 0
            ]

            return {
                "services": service_counts,
                "top_senders": top_senders,
                "top_platforms": sorted_platforms
            }

    def get_top_users_analytics(self, limit: int = 10) -> Dict[str, Any]:
        """
        Returns top users by OTP usage (most OTPs received / spent)
        and top users by Top Up (highest total topup & frequency).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Top OTP users
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.total_spent,
                       COUNT(o.id) as otp_count
                FROM users u
                LEFT JOIN orders o ON u.user_id = o.user_id AND o.status = 'completed'
                GROUP BY u.user_id
                ORDER BY otp_count DESC, u.total_spent DESC
                LIMIT ?
            """, (limit,))
            top_otp_users = [dict(r) for r in cursor.fetchall()]

            # Top Topup users
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name,
                       COUNT(t.id) as topup_count,
                       SUM(t.amount) as total_topup_amount
                FROM users u
                JOIN topups t ON u.user_id = t.user_id AND t.status = 'approved'
                GROUP BY u.user_id
                ORDER BY total_topup_amount DESC, topup_count DESC
                LIMIT ?
            """, (limit,))
            top_topup_users = [dict(r) for r in cursor.fetchall()]

            return {
                "top_otp_users": top_otp_users,
                "top_topup_users": top_topup_users
            }

    def get_all_user_ids(self) -> List[int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            return [r["user_id"] for r in cursor.fetchall()]

# Global Database Instance
db = Database()
