
import os
import hmac
import sqlite3
import secrets
import string
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

import qrcode
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only local runs do not require the PostgreSQL driver.
    psycopg = None
    dict_row = None
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, StreamingResponse

app = FastAPI(title="ThreadLoop")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "threadloop.db"
TEMPLATES_DIR = BASE_DIR / "templates"

DATABASE_URL = os.getenv("DATABASE_URL")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PARTNER_CONFIRM_TOKEN = os.getenv("PARTNER_CONFIRM_TOKEN")

garments = {
    "TL-1001": {
        "name": "Cotton T-shirt",
        "material": "100% cotton",
        "care": "Wash gently in cold water",
        "status": "Sample data — not verified"
    }
}


def get_connection():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use DATABASE_URL/PostgreSQL")
        return psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=10
        )

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_connection()

    try:
        if DATABASE_URL:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS handoffs (
                    id BIGSERIAL PRIMARY KEY,
                    garment_id TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0
                )
            """)
        else:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    garment_id TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0
                )
            """)

        # Backwards-compatible migration for existing databases.
        # Older handoffs did not belong to a particular demo user.
        if DATABASE_URL:
            connection.execute("ALTER TABLE handoffs ADD COLUMN IF NOT EXISTS user_id TEXT")
        else:
            columns = connection.execute("PRAGMA table_info(handoffs)").fetchall()
            if not any(column["name"] == "user_id" for column in columns):
                connection.execute("ALTER TABLE handoffs ADD COLUMN user_id TEXT")

        if DATABASE_URL:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reward_redemptions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    offer_name TEXT NOT NULL,
                    points_cost INTEGER NOT NULL,
                    coupon_code TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reward_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    offer_name TEXT NOT NULL,
                    points_cost INTEGER NOT NULL,
                    coupon_code TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)
        # Backwards-compatible migration: track coupon use without losing existing coupons.
        if DATABASE_URL:
            connection.execute("ALTER TABLE reward_redemptions ADD COLUMN IF NOT EXISTS used_at TEXT")
            connection.execute("ALTER TABLE reward_redemptions ADD COLUMN IF NOT EXISTS used_by TEXT")
        else:
            redemption_columns = connection.execute("PRAGMA table_info(reward_redemptions)").fetchall()
            redemption_column_names = {column["name"] for column in redemption_columns}
            if "used_at" not in redemption_column_names:
                connection.execute("ALTER TABLE reward_redemptions ADD COLUMN used_at TEXT")
            if "used_by" not in redemption_column_names:
                connection.execute("ALTER TABLE reward_redemptions ADD COLUMN used_by TEXT")

        connection.commit()
    finally:
        connection.close()


init_db()


def execute_query(connection, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    return connection.execute(sql, params)


@app.get("/")
def home():
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/garment/{garment_id}")
def garment_passport(garment_id: str):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    return FileResponse(TEMPLATES_DIR / "garment.html")


@app.get("/api/garment/{garment_id}")
def get_garment(garment_id: str):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(status_code=404, detail="Garment not found")

    return {
        "garment_id": garment_id,
        "name": garment["name"],
        "material": garment["material"],
        "care": garment["care"],
        "status": garment["status"]
    }


@app.get("/api/next-life/{garment_id}")
def next_life(
    garment_id: str,
    condition: str = Query(default="good")
):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(status_code=404, detail="Garment not found")

    recommendations = {
        "good": (
            "Resale or donation",
            "The garment may still be useful to someone else."
        ),
        "minor_damage": (
            "Repair first",
            "Repair may extend the garment's useful life."
        ),
        "worn_out": (
            "Check recycling options",
            "Find a partner that accepts this material and condition."
        )
    }

    if condition not in recommendations:
        raise HTTPException(
            status_code=400,
            detail="Condition must be good, minor_damage, or worn_out"
        )

    recommendation, reason = recommendations[condition]

    return {
        "garment_id": garment_id,
        "garment": garment["name"],
        "material": garment["material"],
        "condition": condition,
        "recommendation": recommendation,
        "reason": reason,
        "note": (
            "Prototype recommendation. Confirm garment condition "
            "and partner requirements before handoff."
        )
    }


@app.get("/qr.png")
def generate_qr(garment_id: str = Query(default="TL-1001")):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    qr_url = f"{BASE_URL}/garment/{garment_id}"
    image = qrcode.make(qr_url)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")


@app.get("/rewards")
def rewards_page():
    return FileResponse(TEMPLATES_DIR / "rewards.html")


@app.get("/map")
def recovery_map():
    return FileResponse(TEMPLATES_DIR / "map.html")


@app.post("/api/handoffs")
def create_handoff(garment_id: str, destination: str, user_id: str = Query(default="")):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    destination = destination.strip()
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=400, detail="A valid demo user ID is required")
    if not destination or len(destination) > 200:
        raise HTTPException(
            status_code=400,
            detail="Destination must contain 1–200 characters"
        )

    connection = get_connection()

    try:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if DATABASE_URL:
            cursor = execute_query(
                connection,
                """
                INSERT INTO handoffs
                    (garment_id, destination, status, created_at, points, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (garment_id, destination, "pending", created_at, 0, user_id)
            )
            handoff_id = cursor.fetchone()["id"]
        else:
            cursor = execute_query(
                connection,
                """
                INSERT INTO handoffs
                    (garment_id, destination, status, created_at, points, user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (garment_id, destination, "pending", created_at, 0, user_id)
            )
            handoff_id = cursor.lastrowid

        connection.commit()

        return {
            "message": "Handoff submitted successfully",
            "handoff_id": handoff_id,
            "garment_id": garment_id,
            "destination": destination,
            "user_id": user_id,
            "status": "pending",
            "points": 0
        }
    finally:
        connection.close()


@app.post("/api/handoffs/{handoff_id}/confirm")
def confirm_handoff(
    handoff_id: int,
    x_partner_token: str | None = Header(default=None)
):
    if not PARTNER_CONFIRM_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Partner confirmation is not configured"
        )

    if (
        not x_partner_token
        or not hmac.compare_digest(
            x_partner_token,
            PARTNER_CONFIRM_TOKEN
        )
    ):
        raise HTTPException(status_code=403, detail="Invalid partner token")

    connection = get_connection()

    try:
        handoff = execute_query(
            connection,
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(status_code=404, detail="Handoff not found")

        if handoff["status"] == "confirmed":
            raise HTTPException(
                status_code=400,
                detail="Handoff already confirmed"
            )

        execute_query(
            connection,
            """
            UPDATE handoffs
            SET status = ?, points = ?
            WHERE id = ?
            """,
            ("confirmed", 10, handoff_id)
        )

        connection.commit()

        return {
            "message": "Prototype handoff confirmed",
            "handoff_id": handoff_id,
            "status": "confirmed",
            "points_awarded": 10
        }
    finally:
        connection.close()


@app.get("/api/handoffs/{handoff_id}")
def get_handoff(handoff_id: int):
    connection = get_connection()

    try:
        handoff = execute_query(
            connection,
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(status_code=404, detail="Handoff not found")

        return dict(handoff)
    finally:
        connection.close()


# Prototype reward catalogue. Replace these sample offers with confirmed partner offers.
REWARD_OFFERS = [
    {"id": "eco10", "store": "Sustainable Fashion Store", "title": "10% off", "description": "Discount on eligible sustainable fashion", "points_cost": 50},
    {"id": "essentials100", "store": "Eco Essentials Store", "title": "₹100 off", "description": "Discount on eligible eco-friendly essentials", "points_cost": 100},
    {"id": "green15", "store": "Green Living Market", "title": "15% off", "description": "Discount on selected low-impact products", "points_cost": 150}
]


def reward_balance(connection, user_id: str) -> int:
    earned_row = execute_query(
        connection,
        "SELECT COALESCE(SUM(points), 0) AS total FROM handoffs WHERE user_id = ? AND status = ?",
        (user_id, "confirmed")
    ).fetchone()
    spent_row = execute_query(
        connection,
        "SELECT COALESCE(SUM(points_cost), 0) AS total FROM reward_redemptions WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    return max(0, int(earned_row["total"] or 0) - int(spent_row["total"] or 0))


@app.get("/api/rewards/{user_id}")
def get_rewards_wallet(user_id: str):
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    connection = get_connection()
    try:
        balance = reward_balance(connection, user_id)
        earned = execute_query(
            connection,
            "SELECT COUNT(*) AS total FROM handoffs WHERE user_id = ? AND status = ?",
            (user_id, "confirmed")
        ).fetchone()["total"]
        redemptions = execute_query(
            connection,
            "SELECT id, offer_id, offer_name, points_cost, coupon_code, created_at, used_at FROM reward_redemptions WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        ).fetchall()
        return {
            "user_id": user_id,
            "balance": balance,
            "confirmed_handoffs": int(earned or 0),
            "offers": REWARD_OFFERS,
            "redemptions": [dict(item) for item in redemptions]
        }
    finally:
        connection.close()


@app.post("/api/rewards/{user_id}/redeem/{offer_id}")
def redeem_reward(user_id: str, offer_id: str):
    user_id = user_id.strip()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    offer = next((item for item in REWARD_OFFERS if item["id"] == offer_id), None)
    if offer is None:
        raise HTTPException(status_code=404, detail="Reward offer not found")

    connection = get_connection()
    try:
        balance = reward_balance(connection, user_id)
        if balance < offer["points_cost"]:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough points. You need {offer['points_cost']} points; your balance is {balance}."
            )

        alphabet = string.ascii_uppercase + string.digits
        coupon_code = "TL-" + "".join(secrets.choice(alphabet) for _ in range(10))
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        execute_query(
            connection,
            "INSERT INTO reward_redemptions (user_id, offer_id, offer_name, points_cost, coupon_code, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, offer["id"], f"{offer['store']} — {offer['title']}", offer["points_cost"], coupon_code, created_at)
        )
        connection.commit()
        return {
            "message": "Reward redeemed successfully",
            "offer": offer,
            "coupon_code": coupon_code,
            "points_spent": offer["points_cost"],
            "balance": reward_balance(connection, user_id)
        }
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

@app.post("/api/coupons/apply")
def apply_coupon(user_id: str = Query(...), coupon_code: str = Query(...)):
    """Validate and mark an issued coupon as used for this demo wallet.

    This endpoint is intended for prototype testing. A production store should
    call a partner-authenticated redemption endpoint instead of trusting a
    browser-supplied user ID.
    """
    user_id = user_id.strip()
    coupon_code = coupon_code.strip().upper()
    if not user_id or len(user_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    if not coupon_code or len(coupon_code) > 40:
        raise HTTPException(status_code=400, detail="Enter a valid coupon code")

    connection = get_connection()
    try:
        # Serialize SQLite writers and lock the matching PostgreSQL row so two
        # simultaneous requests cannot successfully use the same coupon.
        if not DATABASE_URL:
            connection.execute("BEGIN IMMEDIATE")
        row_sql = "SELECT id, user_id, offer_name, coupon_code, used_at FROM reward_redemptions WHERE coupon_code = ?"
        if DATABASE_URL:
            row_sql += " FOR UPDATE"
        coupon = execute_query(connection, row_sql, (coupon_code,)).fetchone()

        if coupon is None or coupon["user_id"] != user_id:
            connection.rollback()
            raise HTTPException(status_code=400, detail="Coupon code is invalid or does not belong to this wallet")
        if coupon["used_at"]:
            connection.rollback()
            raise HTTPException(status_code=409, detail="This coupon has already been used")

        used_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = execute_query(
            connection,
            "UPDATE reward_redemptions SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL",
            (used_at, user_id, coupon["id"])
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise HTTPException(status_code=409, detail="This coupon has already been used")
        connection.commit()
        return {
            "message": "Coupon validated and marked as used.",
            "coupon_code": coupon_code,
            "offer_name": coupon["offer_name"],
            "used_at": used_at
        }
    except HTTPException:
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

