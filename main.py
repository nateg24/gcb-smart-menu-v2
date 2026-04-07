from __future__ import annotations

import os
import secrets
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Set

from fastapi import Depends, FastAPI, Header, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
    DateTime,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Absolute path to the directory containing this file — used to locate static assets
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Smart Menu Backend v0")

# Serve everything under /static from the local "static" folder
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static", html=True),
    name="static",
)


# Both /tv and /menu serve the TV display page (same HTML, different URL aliases)
@app.get("/tv")
@app.get("/menu")
def tv_page():
    return FileResponse(BASE_DIR / "static" / "tv" / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(BASE_DIR / "static" / "admin" / "index.html")


# -------------------------
# DB setup
# -------------------------

# SQLite database stored next to this file; check_same_thread=False allows
# FastAPI's async routes to share a connection across threads
engine = create_engine("sqlite:///./menu.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# Tap status values — what's currently flowing, kicked, or coming next
class TapStatus(str, Enum):
    ON = "ON"
    OUT = "OUT"
    COMING_SOON = "COMING_SOON"


# ORM model for beers in the inventory
class Beer(Base):
    __tablename__ = "beers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brewery = Column(String, nullable=True)
    style = Column(String, nullable=True)
    abv = Column(Float, nullable=True)
    price = Column(String, nullable=True)  # kept as string to support "$6", "6", etc.
    description = Column(String, nullable=True)
    category = Column(String, nullable=True, default="CORE")  # CORE | GUEST | CIDER
    is_active = Column(Integer, nullable=False, default=1)    # soft-delete flag (1=active, 0=deleted)
    display_order = Column(Integer, nullable=False, default=0) # controls sort order on the menu


# ORM model for physical taps on the wall
class Tap(Base):
    __tablename__ = "taps"
    id = Column(Integer, primary_key=True, index=True)
    tap_number = Column(Integer, nullable=False, unique=True)  # physical tap label (1–24)
    beer_id = Column(Integer, ForeignKey("beers.id"), nullable=True)  # null = empty tap
    status = Column(String, nullable=False, default=TapStatus.ON.value)
    display_order = Column(Integer, nullable=False, default=0)
    last_updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # SQLAlchemy relationship — lets us access tap.beer directly
    beer = relationship("Beer")


# -------------------------
# Schemas
# -------------------------
ALLOWED_CATEGORIES = {"CORE", "GUEST", "CIDER"}


# Request body for creating a new beer
class BeerIn(BaseModel):
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "CORE"
    is_active: bool = True
    display_order: Optional[int] = None  # if omitted, appended to the end


# Request body for partial updates — all fields optional
class BeerUpdate(BaseModel):
    name: Optional[str] = None
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None


# Response shape returned to clients for a beer
class BeerOut(BaseModel):
    id: int
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    display_order: int = 0


# Response shape for a tap (optionally includes the beer assigned to it)
class TapOut(BaseModel):
    id: int
    tap_number: int
    status: TapStatus
    display_order: int
    last_updated_at: datetime
    beer_id: Optional[int] = None
    beer: Optional[BeerOut] = None


# Top-level response returned by GET /api/menu
class MenuOut(BaseModel):
    version: int          # increments each time the menu changes (used to skip redundant re-renders)
    generated_at: datetime
    taps: List[TapOut]


# Request body for changing a tap's status (ON / OUT / COMING_SOON)
class SetStatusIn(BaseModel):
    status: TapStatus


# Request body for assigning a beer to a tap; beer_id=null clears the tap
class AssignBeerIn(BaseModel):
    beer_id: Optional[int]  # allow clearing a tap


# A single item inside a bulk import payload
class BeerBulkItem(BaseModel):
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "CORE"
    is_active: bool = True


# Options that control how a bulk import behaves
class BulkImportOptions(BaseModel):
    disable_all_first: bool = False   # mark every existing beer inactive before importing
    disable_missing: bool = False     # deactivate beers not present in the payload
    clear_taps_first: bool = False    # unassign all taps before re-assigning
    assign_to_taps: bool = False      # auto-assign imported beers to taps after upsert
    assign_order: str = "house_first" # "house_first" puts CORE beers before GUEST/CIDER; "payload" uses payload order


# Full bulk import request: options + list of beers
class BulkBeersIn(BaseModel):
    options: BulkImportOptions = Field(default_factory=BulkImportOptions)
    beers: List[BeerBulkItem]


# Request body for reordering taps — list of tap IDs in the desired order
class ReorderTapsIn(BaseModel):
    order: List[int]


# -------------------------
# Realtime: WS hub
# -------------------------

# Central hub that tracks all open WebSocket connections from TV displays.
# When the menu changes, it broadcasts a "menu_updated" event to every connected client.
class MenuHub:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()
        self.version: int = 1  # monotonically increasing; TV clients skip re-renders if version is unchanged

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast_menu_updated(self) -> None:
        """Increment version and notify all connected TV displays."""
        self.version += 1
        dead: List[WebSocket] = []
        payload = {"type": "menu_updated", "version": self.version}
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                # Client disconnected — collect for cleanup
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = MenuHub()

# -------------------------
# Auth
# -------------------------

# PIN is read from the environment; falls back to "1515" for local dev
ADMIN_PIN: str = os.environ.get("ADMIN_PIN", "1515")

# In-memory set of valid Bearer tokens — tokens are lost on server restart (intentional)
_valid_tokens: Set[str] = set()


class LoginIn(BaseModel):
    pin: str


@app.post("/api/auth/login")
def admin_login(body: LoginIn):
    """Exchange a PIN for a session token. Returns 401 on wrong PIN."""
    if body.pin != ADMIN_PIN:
        raise HTTPException(status_code=401, detail="Invalid PIN")
    token = secrets.token_hex(32)  # cryptographically random 64-char hex string
    _valid_tokens.add(token)
    return {"token": token}


def verify_token(authorization: str = Header(None)):
    """FastAPI dependency — validates the Bearer token on protected routes."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        if token in _valid_tokens:
            return token
    raise HTTPException(status_code=401, detail="Unauthorized")


# -------------------------
# Startup: schema + seed
# -------------------------
def ensure_schema() -> None:
    """
    Create missing tables, then manually add any columns that were added after
    the DB was first created (SQLite doesn't support ALTER TABLE for new columns
    automatically, so we probe and patch).
    """
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # Add beers.display_order if it doesn't exist yet
        try:
            conn.execute(text("SELECT display_order FROM beers LIMIT 1"))
        except OperationalError:
            conn.execute(
                text(
                    "ALTER TABLE beers ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
                )
            )

        # Add taps.display_order if it doesn't exist yet
        try:
            conn.execute(text("SELECT display_order FROM taps LIMIT 1"))
        except OperationalError:
            conn.execute(
                text(
                    "ALTER TABLE taps ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
                )
            )


def seed_if_empty() -> None:
    """Populate the DB with Gnarly Cedar sample beers and 24 empty taps on first run."""
    db = SessionLocal()
    try:
        beer_count = db.query(Beer).count()
        tap_count = db.query(Tap).count()

        if beer_count == 0:
            beers = [
                Beer(
                    name="Greenleaf Lager",
                    brewery="Gnarly Cedar",
                    style="America Light Lager",
                    abv=4.2,
                    price="6",
                    category="CORE",
                    display_order=0,
                ),
                Beer(
                    name="Apostle Amber Ale",
                    brewery="Gnarly Cedar",
                    style="Malty sweetness, biscuit, caramel",
                    abv=5.6,
                    price="6",
                    category="CORE",
                    display_order=1,
                ),
                Beer(
                    name="Daybreak",
                    brewery="Gnarly Cedar",
                    style="Blonde Ale, light, crisp, dry finish",
                    abv=4.8,
                    price="6",
                    category="CORE",
                    display_order=2,
                ),
                Beer(
                    name="Goldenrod",
                    brewery="Gnarly Cedar",
                    style="Golden Ale, Honey, Hefeweizen yeast",
                    abv=5.0,
                    price="6",
                    category="CORE",
                    display_order=3,
                ),
                Beer(
                    name="Crocs & Socks",
                    brewery="Gnarly Cedar",
                    style="Special Brown ale, caramelized sugar",
                    abv=7.6,
                    price="6",
                    category="CORE",
                    display_order=4,
                ),
                Beer(
                    name="Supper Club",
                    brewery="Gnarly Cedar",
                    style="Orange zest, cherries, old fashion brown ale",
                    abv=7.6,
                    price="7",
                    category="CORE",
                    display_order=5,
                ),
                Beer(
                    name="Mr. Hyde",
                    brewery="Gnarly Cedar",
                    style="Farmhouse saison brewed with Marquette",
                    abv=6.6,
                    price="8",
                    category="CORE",
                    display_order=6,
                ),
                Beer(
                    name="Dr. Jekyll",
                    brewery="Gnarly Cedar",
                    style="Wit grape ale with Frontenac Blanc",
                    abv=4.8,
                    price="8",
                    category="CORE",
                    display_order=7,
                ),
                Beer(
                    name="Tightlines IPA",
                    brewery="Gnarly Cedar",
                    style="West Coast style - Deep Cut Cascade",
                    abv=7.0,
                    price="8",
                    category="CORE",
                    display_order=8,
                ),
                Beer(
                    name="Hop Duster IPA",
                    brewery="Gnarly Cedar",
                    style="Hazy IPA - Galaxy, Citra, Chinook",
                    abv=6.8,
                    price="8",
                    category="CORE",
                    display_order=9,
                ),
                Beer(
                    name="Alien Philosopher IPA",
                    brewery="Gnarly Cedar",
                    style="Double IPA Sabro, Bru-1, and Dolcita",
                    abv=8.1,
                    price="8",
                    category="CORE",
                    display_order=10,
                ),
                Beer(
                    name="Turtle Cowboy",
                    brewery="Gnarly Cedar",
                    style="Hazy IPA w/ El Dorado, Azacca and Vista hops",
                    abv=7.5,
                    price="8",
                    category="CORE",
                    display_order=11,
                ),
                Beer(
                    name="Strawberry Shakedown",
                    brewery="Gnarly Cedar",
                    style="Milkshake IPA, belma + mosaic hops, vanilla",
                    abv=6.0,
                    price="8",
                    category="CORE",
                    display_order=12,
                ),
                Beer(
                    name="Mammoth Milk Stout",
                    brewery="Gnarly Cedar",
                    style="Milk Stout, roasted, chocolate notes",
                    abv=6.5,
                    price="8",
                    category="CORE",
                    display_order=13,
                ),
                Beer(
                    name="Woolly Wizard",
                    brewery="Gnarly Cedar",
                    style="Coffee Wizard Coffee Milk Stout",
                    abv=6.5,
                    price="8",
                    category="CORE",
                    display_order=14,
                ),
                Beer(
                    name="Drop Top",
                    brewery="Stubborn Brothers",
                    style="Sun Drop Radler",
                    abv=None,
                    price="8",
                    category="GUEST",
                    display_order=15,
                ),
                Beer(
                    name="Cherry Mechanic",
                    brewery="Ahnapee",
                    style="Gluten Free",
                    abv=None,
                    price="8",
                    category="GUEST",
                    display_order=16,
                ),
                Beer(
                    name="Blackberry Sour",
                    brewery="Stubborn Brothers",
                    style="Sour Ale",
                    abv=None,
                    price="8",
                    category="GUEST",
                    display_order=17,
                ),
                Beer(
                    name="Strawberry Cider",
                    brewery="Cider Boys",
                    style="Cider",
                    abv=None,
                    price="8",
                    category="CIDER",
                    display_order=18,
                ),
                Beer(
                    name="Pomegranate Cider",
                    brewery="DownEast Cider",
                    style="Cider",
                    abv=None,
                    price="8",
                    category="CIDER",
                    display_order=19,
                ),
            ]
            db.add_all(beers)
            db.commit()

        # Create 24 empty taps (tap_number 1–24) if none exist
        if tap_count == 0:
            for i in range(1, 25):
                db.add(Tap(tap_number=i, display_order=i))
            db.commit()
    finally:
        db.close()


# Request body for reordering beers — list of beer IDs in the desired order
class ReorderBeersIn(BaseModel):
    order: List[int]


@app.on_event("startup")
def on_startup():
    """Run schema migrations and seed data before accepting requests."""
    ensure_schema()
    seed_if_empty()


# -------------------------
# Routes
# -------------------------

@app.get("/api/menu", response_model=MenuOut)
def get_menu():
    """
    Return the full current menu: all taps ordered by display_order,
    each with their assigned beer (if active). Used by both the TV display
    and the admin panel.
    """
    db = SessionLocal()
    try:
        taps = (
            db.query(Tap).order_by(Tap.display_order.asc(), Tap.tap_number.asc()).all()
        )

        out_taps: List[TapOut] = []
        for tap in taps:
            beer_out = None
            # Only surface the beer if it hasn't been soft-deleted
            if tap.beer is not None and tap.beer.is_active == 1:
                beer_out = BeerOut(
                    id=tap.beer.id,
                    name=tap.beer.name,
                    brewery=tap.beer.brewery,
                    style=tap.beer.style,
                    abv=tap.beer.abv,
                    price=tap.beer.price,
                    description=tap.beer.description,
                    category=tap.beer.category,
                    display_order=tap.beer.display_order,
                )

            out_taps.append(
                TapOut(
                    id=tap.id,
                    tap_number=tap.tap_number,
                    status=TapStatus(tap.status),
                    display_order=tap.display_order,
                    last_updated_at=tap.last_updated_at,
                    beer_id=tap.beer_id,
                    beer=beer_out,
                )
            )

        return MenuOut(
            version=hub.version, generated_at=datetime.utcnow(), taps=out_taps
        )
    finally:
        db.close()


@app.get("/api/beers", response_model=list[BeerOut])
def list_beers(include_inactive: bool = False):
    """
    List beers. By default only active (non-deleted) beers are returned.
    Pass ?include_inactive=true to see everything (used by admin tools).
    """
    db = SessionLocal()
    try:
        q = db.query(Beer)
        if not include_inactive:
            q = q.filter(Beer.is_active == 1)

        beers = q.order_by(Beer.display_order.asc(), Beer.name.asc()).all()

        return [
            BeerOut(
                id=b.id,
                name=b.name,
                brewery=b.brewery,
                style=b.style,
                abv=b.abv,
                price=b.price,
                description=b.description,
                category=b.category,
                display_order=b.display_order,
            )
            for b in beers
        ]
    finally:
        db.close()


@app.post("/api/beers", response_model=BeerOut)
async def create_beer(body: BeerIn, _=Depends(verify_token)):
    """Create a new beer. Appends to end of display order if display_order not specified."""
    db = SessionLocal()
    try:
        # if no display_order specified, append to end
        if body.display_order is None:
            max_order = (
                db.query(Beer.display_order).order_by(Beer.display_order.desc()).first()
            )
            next_order = (
                (max_order[0] + 1) if max_order and max_order[0] is not None else 0
            )
        else:
            next_order = int(body.display_order)

        b = Beer(
            name=body.name.strip(),
            brewery=(
                body.brewery.strip() if body.brewery and body.brewery.strip() else None
            ),
            style=(body.style.strip() if body.style and body.style.strip() else None),
            abv=body.abv,
            price=(body.price.strip() if body.price and body.price.strip() else None),
            description=(
                body.description.strip()
                if body.description and body.description.strip()
                else None
            ),
            category=(body.category or "CORE"),
            is_active=1 if body.is_active else 0,
            display_order=next_order,
        )
        db.add(b)
        db.commit()
        db.refresh(b)

        # Notify all connected TV displays that the menu changed
        await hub.broadcast_menu_updated()

        return BeerOut(
            id=b.id,
            name=b.name,
            brewery=b.brewery,
            style=b.style,
            abv=b.abv,
            price=b.price,
            description=b.description,
            category=b.category,
            display_order=b.display_order,
        )
    finally:
        db.close()


@app.put("/api/beers/{beer_id}", response_model=BeerOut)
async def update_beer(beer_id: int, body: BeerUpdate, _=Depends(verify_token)):
    """Partial update — only fields present in the request body are changed."""
    db = SessionLocal()
    try:
        b = db.query(Beer).filter(Beer.id == beer_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Beer not found")

        if body.name is not None:
            b.name = body.name.strip()
        if body.brewery is not None:
            b.brewery = (
                body.brewery.strip() if body.brewery and body.brewery.strip() else None
            )
        if body.style is not None:
            b.style = body.style.strip() if body.style and body.style.strip() else None
        if body.abv is not None:
            b.abv = body.abv
        if body.price is not None:
            b.price = body.price.strip() if body.price and body.price.strip() else None
        if body.description is not None:
            b.description = (
                body.description.strip()
                if body.description and body.description.strip()
                else None
            )
        if body.is_active is not None:
            b.is_active = 1 if body.is_active else 0
        if body.category is not None:
            cat = body.category.strip().upper()
            if cat not in ALLOWED_CATEGORIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid category '{body.category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}",
                )
            b.category = cat
        if body.display_order is not None:
            b.display_order = int(body.display_order)

        db.commit()
        db.refresh(b)

        await hub.broadcast_menu_updated()

        return BeerOut(
            id=b.id,
            name=b.name,
            brewery=b.brewery,
            style=b.style,
            abv=b.abv,
            price=b.price,
            description=b.description,
            category=b.category,
            display_order=b.display_order,
        )
    finally:
        db.close()


@app.delete("/api/beers/{beer_id}")
async def delete_beer(beer_id: int, _=Depends(verify_token)):
    """
    Soft delete: sets is_active=0 instead of removing the row.
    This preserves history and avoids broken foreign keys on taps.
    """
    db = SessionLocal()
    try:
        b = db.query(Beer).filter(Beer.id == beer_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Beer not found")

        b.is_active = 0
        db.commit()

        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.post("/api/taps/{tap_id}/status", response_model=TapOut)
async def set_tap_status(tap_id: int, body: SetStatusIn, _=Depends(verify_token)):
    """Change a tap's status (ON / OUT / COMING_SOON) without touching its beer assignment."""
    db = SessionLocal()
    try:
        tap = db.query(Tap).filter(Tap.id == tap_id).first()
        if not tap:
            raise HTTPException(status_code=404, detail="Tap not found")

        tap.status = body.status.value
        tap.last_updated_at = datetime.utcnow()
        db.commit()
        db.refresh(tap)

        await hub.broadcast_menu_updated()

        beer_out = None
        if tap.beer is not None and tap.beer.is_active == 1:
            beer_out = BeerOut(
                id=tap.beer.id,
                name=tap.beer.name,
                brewery=tap.beer.brewery,
                style=tap.beer.style,
                abv=tap.beer.abv,
                price=tap.beer.price,
                description=tap.beer.description,
                category=tap.beer.category,
                display_order=tap.beer.display_order,
            )

        return TapOut(
            id=tap.id,
            tap_number=tap.tap_number,
            status=TapStatus(tap.status),
            display_order=tap.display_order,
            last_updated_at=tap.last_updated_at,
            beer_id=tap.beer_id,
            beer=beer_out,
        )
    finally:
        db.close()


@app.post("/api/taps/{tap_id}/assign", response_model=TapOut)
async def assign_beer(tap_id: int, body: AssignBeerIn, _=Depends(verify_token)):
    """Assign a beer to a tap, or clear the tap by passing beer_id=null."""
    db = SessionLocal()
    try:
        tap = db.query(Tap).filter(Tap.id == tap_id).first()
        if not tap:
            raise HTTPException(status_code=404, detail="Tap not found")

        if body.beer_id is not None:
            beer = db.query(Beer).filter(Beer.id == body.beer_id).first()
            if not beer:
                raise HTTPException(status_code=404, detail="Beer not found")
            tap.beer_id = beer.id
        else:
            tap.beer_id = None  # clear the tap

        tap.last_updated_at = datetime.utcnow()
        db.commit()
        db.refresh(tap)

        await hub.broadcast_menu_updated()

        beer_out = None
        if tap.beer is not None and tap.beer.is_active == 1:
            beer_out = BeerOut(
                id=tap.beer.id,
                name=tap.beer.name,
                brewery=tap.beer.brewery,
                style=tap.beer.style,
                abv=tap.beer.abv,
                price=tap.beer.price,
                description=tap.beer.description,
                category=tap.beer.category,
                display_order=tap.beer.display_order,
            )

        return TapOut(
            id=tap.id,
            tap_number=tap.tap_number,
            status=TapStatus(tap.status),
            display_order=tap.display_order,
            last_updated_at=tap.last_updated_at,
            beer_id=tap.beer_id,
            beer=beer_out,
        )
    finally:
        db.close()


@app.post("/api/beers/bulk")
async def bulk_upsert_beers(body: BulkBeersIn, _=Depends(verify_token)):
    """
    Bulk import/update beers. Matches existing records by (name, brewery) and
    updates them in place; creates new rows for unrecognized beers.

    Options control whether to disable all beers first, clear taps, or
    auto-assign the imported beers to taps after the upsert.
    """
    db = SessionLocal()
    try:
        beers_in = body.beers
        opts = body.options

        if not beers_in:
            raise HTTPException(status_code=400, detail="beers list cannot be empty")

        # Validate and normalize categories up front so we fail early
        normalized = []
        for b in beers_in:
            cat = (b.category or "CORE").strip().upper()
            if cat not in ALLOWED_CATEGORIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid category '{b.category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}",
                )
            normalized.append((b, cat))

        # Optionally mark every existing beer inactive before processing the payload
        if opts.disable_all_first:
            db.query(Beer).update({Beer.is_active: 0})
            db.flush()

        # Optionally clear all tap assignments so taps can be re-assigned fresh
        if opts.clear_taps_first:
            db.query(Tap).update({Tap.beer_id: None})
            db.flush()

        created = 0
        updated = 0

        seen_keys = set()  # tracks (name, brewery) pairs already processed to skip duplicates
        key_to_beer = {}  # maps key → beer ORM object for use in payload-order assignment

        max_order_row = db.query(Beer.display_order).order_by(Beer.display_order.desc()).first()
        next_order = (max_order_row[0] + 1) if max_order_row and max_order_row[0] is not None else 0

        for b, cat in normalized:
            name = b.name.strip()
            brewery = b.brewery.strip() if b.brewery and b.brewery.strip() else None

            # Deduplicate within the payload itself
            key = (name.lower(), (brewery or "").lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Match by exact name + brewery; update if found, insert if not
            existing = (
                db.query(Beer)
                .filter(Beer.name == name, Beer.brewery == brewery)
                .first()
            )

            if existing:
                existing.style = b.style
                existing.abv = b.abv
                existing.price = b.price
                existing.description = b.description
                existing.category = cat
                existing.is_active = 1 if b.is_active else 0
                key_to_beer[key] = existing
                updated += 1
            else:
                new_beer = Beer(
                    name=name,
                    brewery=brewery,
                    style=b.style,
                    abv=b.abv,
                    price=b.price,
                    description=b.description,
                    category=cat,
                    is_active=1 if b.is_active else 0,
                    display_order=next_order,
                )
                db.add(new_beer)
                key_to_beer[key] = new_beer
                next_order += 1
                created += 1

        db.flush()

        # Optionally deactivate any beers that weren't in the payload (roster cleanup)
        disabled_missing = 0
        if opts.disable_missing:
            all_beers = db.query(Beer).all()
            keep = {(k[0], k[1]) for k in seen_keys}

            for beer in all_beers:
                k = (beer.name.lower(), (beer.brewery or "").lower())
                if k not in keep:
                    if beer.is_active != 0:
                        beer.is_active = 0
                        disabled_missing += 1

        # Optionally assign beers to taps sequentially
        assigned = 0
        if opts.assign_to_taps:
            taps = db.query(Tap).order_by(Tap.tap_number.asc()).all()

            if opts.assign_order == "payload":
                # Use the order beers appear in the payload; IDs are known from upsert
                ids_to_assign = []
                for b, cat in normalized:
                    if not b.is_active:
                        continue
                    name = b.name.strip()
                    brewery = b.brewery.strip() if b.brewery else None
                    key = (name.lower(), (brewery or "").lower())
                    beer_row = key_to_beer.get(key)
                    if beer_row:
                        ids_to_assign.append(beer_row.id)
            else:
                # "house_first": CORE beers fill taps before GUEST/CIDER beers
                active = db.query(Beer).filter(Beer.is_active == 1).all()
                core = [b for b in active if (b.category or "CORE").upper() == "CORE"]
                guest = [
                    b
                    for b in active
                    if (b.category or "").upper() in ("GUEST", "CIDER")
                ]
                core.sort(key=lambda x: (x.display_order, (x.brewery or ""), x.name))
                guest.sort(key=lambda x: (x.display_order, (x.brewery or ""), x.name))
                ids_to_assign = [b.id for b in (core + guest)]

            # Zip beers onto taps — extras are silently dropped if there are more beers than taps
            for tap, beer_id in zip(taps, ids_to_assign):
                tap.beer_id = beer_id
                tap.last_updated_at = datetime.utcnow()
                assigned += 1

        db.commit()

        await hub.broadcast_menu_updated()

        return {
            "ok": True,
            "created": created,
            "updated": updated,
            "disabled_missing": disabled_missing,
            "assigned_to_taps": assigned,
        }
    finally:
        db.close()


@app.post("/api/beers/reorder")
async def reorder_beers(body: ReorderBeersIn, _=Depends(verify_token)):
    """
    Accept an ordered list of beer IDs (from drag-and-drop) and update
    each beer's display_order to match the new sequence.
    """
    db = SessionLocal()
    try:
        # Validate all IDs before writing anything
        existing_ids = {b.id for b in db.query(Beer.id).all()}
        for beer_id in body.order:
            if beer_id not in existing_ids:
                raise HTTPException(
                    status_code=400, detail=f"Beer id {beer_id} not found"
                )

        # Assign sequential display_order values based on the supplied order
        for idx, beer_id in enumerate(body.order):
            db.query(Beer).filter(Beer.id == beer_id).update({"display_order": idx})

        db.commit()
        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.post("/api/taps/reorder")
async def reorder_taps(body: ReorderTapsIn, _=Depends(verify_token)):
    """
    Accept an ordered list of tap IDs (from drag-and-drop) and update
    each tap's display_order to match the new sequence.
    """
    db = SessionLocal()
    try:
        existing_ids = {t.id for t in db.query(Tap.id).all()}
        for tap_id in body.order:
            if tap_id not in existing_ids:
                raise HTTPException(
                    status_code=400, detail=f"Tap id {tap_id} not found"
                )

        for idx, tap_id in enumerate(body.order):
            db.query(Tap).filter(Tap.id == tap_id).update({"display_order": idx})

        db.commit()
        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.websocket("/ws/menu")
async def ws_menu(ws: WebSocket):
    """
    WebSocket endpoint for TV displays. On connect, sends a "hello" with the
    current version so the client can immediately check if its cached menu is stale.
    Stays open and waits for client messages (we don't expect any, but we need
    to keep the receive loop alive to detect disconnections).
    """
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "version": hub.version})
        while True:
            await ws.receive()  # blocks until a message arrives or the client disconnects
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)
