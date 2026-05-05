import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "carrier_sales.db"

LOADS_SEED = [
    {
        "load_id": "DRY001", "origin": "Chicago, IL", "destination": "Atlanta, GA",
        "pickup_datetime": "2026-05-05 08:00", "delivery_datetime": "2026-05-07 17:00",
        "equipment_type": "Dry Van", "loadboard_rate": 2100.0, "weight": 42000,
        "commodity_type": "General Freight", "num_of_pieces": 24, "miles": 716,
        "dimensions": "48x96x96", "notes": "No touch freight. Dock to dock.",
    },
    {
        "load_id": "DRY002", "origin": "Dallas, TX", "destination": "Memphis, TN",
        "pickup_datetime": "2026-05-05 10:00", "delivery_datetime": "2026-05-06 14:00",
        "equipment_type": "Dry Van", "loadboard_rate": 1450.0, "weight": 38000,
        "commodity_type": "Auto Parts", "num_of_pieces": 50, "miles": 452,
        "dimensions": "53x102x102", "notes": "Team driver preferred.",
    },
    {
        "load_id": "DRY003", "origin": "Columbus, OH", "destination": "Nashville, TN",
        "pickup_datetime": "2026-05-06 06:00", "delivery_datetime": "2026-05-07 10:00",
        "equipment_type": "Dry Van", "loadboard_rate": 1200.0, "weight": 35000,
        "commodity_type": "Consumer Goods", "num_of_pieces": 40, "miles": 389,
        "dimensions": "53x102x102", "notes": "Appointment required.",
    },
    {
        "load_id": "DRY004", "origin": "Los Angeles, CA", "destination": "Phoenix, AZ",
        "pickup_datetime": "2026-05-05 14:00", "delivery_datetime": "2026-05-06 08:00",
        "equipment_type": "Dry Van", "loadboard_rate": 1800.0, "weight": 44000,
        "commodity_type": "Electronics", "num_of_pieces": 12, "miles": 372,
        "dimensions": "53x102x102", "notes": "High value — no overnight parking.",
    },
    {
        "load_id": "DRY005", "origin": "Denver, CO", "destination": "Kansas City, MO",
        "pickup_datetime": "2026-05-07 09:00", "delivery_datetime": "2026-05-08 15:00",
        "equipment_type": "Dry Van", "loadboard_rate": 1350.0, "weight": 30000,
        "commodity_type": "Hardware", "num_of_pieces": 60, "miles": 600,
        "dimensions": "48x96x96", "notes": "FCFS, open dock.",
    },
    {
        "load_id": "REF001", "origin": "Miami, FL", "destination": "Charlotte, NC",
        "pickup_datetime": "2026-05-05 07:00", "delivery_datetime": "2026-05-07 12:00",
        "equipment_type": "Reefer", "loadboard_rate": 3200.0, "weight": 40000,
        "commodity_type": "Produce", "num_of_pieces": 30, "miles": 762,
        "dimensions": "53x102x102", "notes": "Temp: 34°F. Pre-cool required.",
    },
    {
        "load_id": "REF002", "origin": "Fresno, CA", "destination": "Seattle, WA",
        "pickup_datetime": "2026-05-06 05:00", "delivery_datetime": "2026-05-08 10:00",
        "equipment_type": "Reefer", "loadboard_rate": 4200.0, "weight": 43000,
        "commodity_type": "Dairy", "num_of_pieces": 20, "miles": 1114,
        "dimensions": "53x102x102", "notes": "Temp: 38°F continuous. Reefer fuel provided.",
        # High-margin reefer lane — broker has room to flex UP from posted rate
        # 120% ceiling (vs default 115%) — willing to pay more to secure capacity
        "maximum_rate": 5040,
    },
    {
        "load_id": "REF003", "origin": "Houston, TX", "destination": "Chicago, IL",
        "pickup_datetime": "2026-05-05 15:00", "delivery_datetime": "2026-05-07 08:00",
        "equipment_type": "Reefer", "loadboard_rate": 3600.0, "weight": 41000,
        "commodity_type": "Meat", "num_of_pieces": 10, "miles": 1092,
        "dimensions": "53x102x102", "notes": "Temp: 28°F. Driver assist unload.",
    },
    {
        "load_id": "REF004", "origin": "Portland, OR", "destination": "San Francisco, CA",
        "pickup_datetime": "2026-05-07 06:00", "delivery_datetime": "2026-05-08 14:00",
        "equipment_type": "Reefer", "loadboard_rate": 2800.0, "weight": 37000,
        "commodity_type": "Beverages", "num_of_pieces": 55, "miles": 640,
        "dimensions": "53x102x102", "notes": "Temp: 40°F. Liftgate required.",
    },
    {
        "load_id": "FLT001", "origin": "Detroit, MI", "destination": "St. Louis, MO",
        "pickup_datetime": "2026-05-06 07:00", "delivery_datetime": "2026-05-07 16:00",
        "equipment_type": "Flatbed", "loadboard_rate": 2400.0, "weight": 46000,
        "commodity_type": "Steel Coils", "num_of_pieces": 4, "miles": 529,
        "dimensions": "48x102", "notes": "Coil racks required. Tarps and straps.",
        # Tight-margin steel lane — broker can't flex up much from posted
        # 108% ceiling (vs default 115%) — limited margin to give before walking
        "maximum_rate": 2590,
    },
    {
        "load_id": "FLT002", "origin": "Pittsburgh, PA", "destination": "Indianapolis, IN",
        "pickup_datetime": "2026-05-05 08:00", "delivery_datetime": "2026-05-06 12:00",
        "equipment_type": "Flatbed", "loadboard_rate": 1900.0, "weight": 44000,
        "commodity_type": "Lumber", "num_of_pieces": 80, "miles": 441,
        "dimensions": "48x102", "notes": "Tarps required. Oversize permit needed.",
    },
    {
        "load_id": "FLT003", "origin": "Minneapolis, MN", "destination": "Omaha, NE",
        "pickup_datetime": "2026-05-07 10:00", "delivery_datetime": "2026-05-08 15:00",
        "equipment_type": "Flatbed", "loadboard_rate": 1600.0, "weight": 40000,
        "commodity_type": "Farm Equipment", "num_of_pieces": 2, "miles": 367,
        "dimensions": "53x102", "notes": "Pilot car required. Wide load 14ft.",
    },
    {
        "load_id": "FLT004", "origin": "San Antonio, TX", "destination": "El Paso, TX",
        "pickup_datetime": "2026-05-06 09:00", "delivery_datetime": "2026-05-07 08:00",
        "equipment_type": "Flatbed", "loadboard_rate": 2200.0, "weight": 47000,
        "commodity_type": "Pipe", "num_of_pieces": 1, "miles": 552,
        "dimensions": "53x102", "notes": "Chains and binders required.",
    },
    {
        "load_id": "DRY006", "origin": "Boston, MA", "destination": "New York, NY",
        "pickup_datetime": "2026-05-05 12:00", "delivery_datetime": "2026-05-05 18:00",
        "equipment_type": "Dry Van", "loadboard_rate": 850.0, "weight": 20000,
        "commodity_type": "Apparel", "num_of_pieces": 100, "miles": 215,
        "dimensions": "48x96x96", "notes": "Short haul. Same day delivery.",
    },
    {
        "load_id": "REF005", "origin": "Tampa, FL", "destination": "Atlanta, GA",
        "pickup_datetime": "2026-05-08 06:00", "delivery_datetime": "2026-05-09 10:00",
        "equipment_type": "Reefer", "loadboard_rate": 2600.0, "weight": 39000,
        "commodity_type": "Seafood", "num_of_pieces": 8, "miles": 468,
        "dimensions": "53x102x102", "notes": "Temp: 32°F. Live product — driver must monitor.",
    },
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS loads (
            load_id           TEXT PRIMARY KEY,
            origin            TEXT NOT NULL,
            destination       TEXT NOT NULL,
            pickup_datetime   TEXT,
            delivery_datetime TEXT,
            equipment_type    TEXT,
            loadboard_rate    REAL,
            notes             TEXT,
            weight            REAL,
            commodity_type    TEXT,
            num_of_pieces     INTEGER,
            miles             REAL,
            dimensions        TEXT,
            maximum_rate      REAL
        )
    """)
    # Migration for existing DBs — add maximum_rate column if missing.
    # Note: legacy DBs may also have a `minimum_rate` column from the inverted
    # earlier model. We leave it in place (SQLite doesn't easily DROP COLUMN
    # pre-3.35) but new code only reads/writes `maximum_rate`.
    load_cols = [r[1] for r in c.execute("PRAGMA table_info(loads)").fetchall()]
    if "maximum_rate" not in load_cols:
        c.execute("ALTER TABLE loads ADD COLUMN maximum_rate REAL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS calls (
            id                     TEXT PRIMARY KEY,
            created_at             TEXT NOT NULL,
            mc_number              TEXT,
            carrier_name           TEXT,
            reference_number       TEXT,
            loadboard_rate         REAL,
            final_agreed_rate      REAL,
            num_negotiation_rounds INTEGER,
            booking_decision       TEXT,
            decline_reason         TEXT,
            fmcsa_eligible         TEXT,
            call_outcome           TEXT,
            sentiment              TEXT,
            call_duration_sec      INTEGER,
            reviewed_at            TEXT,
            audit_results          TEXT,
            run_id                 TEXT,
            carrier_initial_price  REAL
        )
    """)

    # Migration for existing DBs missing newer columns
    cols = [r[1] for r in c.execute("PRAGMA table_info(calls)").fetchall()]
    if "reviewed_at" not in cols:
        c.execute("ALTER TABLE calls ADD COLUMN reviewed_at TEXT")
    if "audit_results" not in cols:
        c.execute("ALTER TABLE calls ADD COLUMN audit_results TEXT")
    if "run_id" not in cols:
        c.execute("ALTER TABLE calls ADD COLUMN run_id TEXT")
    if "carrier_initial_price" not in cols:
        c.execute("ALTER TABLE calls ADD COLUMN carrier_initial_price REAL")

    # Seed loads if empty
    existing = c.execute("SELECT COUNT(*) FROM loads").fetchone()[0]
    if existing == 0:
        for load in LOADS_SEED:
            c.execute("""
                INSERT INTO loads (
                    load_id, origin, destination, pickup_datetime, delivery_datetime,
                    equipment_type, loadboard_rate, notes, weight, commodity_type,
                    num_of_pieces, miles, dimensions, maximum_rate
                ) VALUES (
                    :load_id, :origin, :destination, :pickup_datetime, :delivery_datetime,
                    :equipment_type, :loadboard_rate, :notes, :weight, :commodity_type,
                    :num_of_pieces, :miles, :dimensions, :maximum_rate
                )
            """, {**load, "maximum_rate": load.get("maximum_rate")})

    conn.commit()
    conn.close()
