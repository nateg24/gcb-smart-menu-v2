from __future__ import annotations
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
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
    or_,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Smart Menu Backend v0")
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static", html=True),
    name="static",
)
from fastapi.responses import FileResponse


@app.get("/tv")
def tv_page():
    return FileResponse(BASE_DIR / "static" / "tv" / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(BASE_DIR / "static" / "admin" / "index.html")


# -------------------------
# DB setup
# -------------------------
engine = create_engine("sqlite:///./menu.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class TapStatus(str, Enum):
    ON = "ON"
    OUT = "OUT"
    COMING_SOON = "COMING_SOON"


class Beer(Base):
    __tablename__ = "beers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brewery = Column(String, nullable=True)
    style = Column(String, nullable=True)
    abv = Column(Float, nullable=True)
    price = Column(String, nullable=True)  # keep string for "$6" etc
    description = Column(String, nullable=True)
    category = Column(String, nullable=True, default="CORE")
    is_active = Column(Integer, nullable=False, default=1)


class Tap(Base):
    __tablename__ = "taps"
    id = Column(Integer, primary_key=True, index=True)
    tap_number = Column(Integer, nullable=False, unique=True)
    beer_id = Column(Integer, ForeignKey("beers.id"), nullable=True)
    status = Column(String, nullable=False, default=TapStatus.ON.value)
    display_order = Column(Integer, nullable=False, default=0)
    last_updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    beer = relationship("Beer")


class BeerIn(BaseModel):
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "CORE"  # <-- ADD
    is_active: bool = True


class BeerUpdate(BaseModel):
    name: Optional[str] = None
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


Base.metadata.create_all(bind=engine)


# -------------------------
# Realtime: WS hub
# -------------------------
class MenuHub:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()
        self.version: int = 1

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast_menu_updated(self) -> None:
        self.version += 1
        dead: List[WebSocket] = []
        payload = {"type": "menu_updated", "version": self.version}
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = MenuHub()


# -------------------------
# Schemas
# -------------------------
ALLOWED_CATEGORIES = {"CORE", "GUEST", "CIDER"}


class BeerOut(BaseModel):
    id: int
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None


class TapOut(BaseModel):
    id: int
    tap_number: int
    status: TapStatus
    display_order: int
    last_updated_at: datetime
    beer_id: Optional[int] = None
    beer: Optional[BeerOut] = None


class MenuOut(BaseModel):
    version: int
    generated_at: datetime
    taps: List[TapOut]


class SetStatusIn(BaseModel):
    status: TapStatus


class AssignBeerIn(BaseModel):
    beer_id: Optional[int]  # allow clearing a tap


class BeerBulkItem(BaseModel):
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "CORE"
    is_active: bool = True


class BulkImportOptions(BaseModel):
    # If True, set all beers is_active=0 before importing
    disable_all_first: bool = False

    # If True, any beer not in payload gets disabled (is_active=0)
    disable_missing: bool = False

    # If True, clears all taps to empty before (optional) reassignment
    clear_taps_first: bool = False

    # If True, assigns beers to taps in order after import
    assign_to_taps: bool = False

    # Tap assignment order:
    # - "house_first": CORE then GUEST/CIDER
    # - "payload": in the exact order in payload
    assign_order: str = "house_first"  # "house_first" | "payload"


class BulkBeersIn(BaseModel):
    options: BulkImportOptions = Field(default_factory=BulkImportOptions)
    beers: List[BeerBulkItem]


# -------------------------
# App
# -------------------------


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        beer_count = db.query(Beer).count()
        tap_count = db.query(Tap).count()
        if beer_count == 0:
            beers = [
                # --- HOUSE BEERS (CORE) ---
                Beer(
                    name="Greenleaf Lager",
                    brewery="Gnarly Cedar",
                    style="America Light Lager",
                    abv=4.2,
                    price="6",
                    category="CORE",
                ),
                Beer(
                    name="Apostle Amber Ale",
                    brewery="Gnarly Cedar",
                    style="Malty sweetness, biscuit, caramel",
                    abv=5.6,
                    price="6",
                    category="CORE",
                ),
                Beer(
                    name="Daybreak",
                    brewery="Gnarly Cedar",
                    style="Blonde Ale, light, crisp, dry finish",
                    abv=4.8,
                    price="6",
                    category="CORE",
                ),
                Beer(
                    name="Goldenrod",
                    brewery="Gnarly Cedar",
                    style="Golden Ale, Honey, Hefeweizen yeast",
                    abv=5.0,
                    price="6",
                    category="CORE",
                ),
                Beer(
                    name="Crocs & Socks",
                    brewery="Gnarly Cedar",
                    style="Special Brown ale, caramelized sugar",
                    abv=7.6,
                    price="6",
                    category="CORE",
                ),
                Beer(
                    name="Supper Club",
                    brewery="Gnarly Cedar",
                    style="Orange zest, cherries, old fashion brown ale",
                    abv=7.6,
                    price="7",
                    category="CORE",
                ),
                Beer(
                    name="Mr. Hyde",
                    brewery="Gnarly Cedar",
                    style="Farmhouse saison brewed with Marquette",
                    abv=6.6,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Dr. Jekyll",
                    brewery="Gnarly Cedar",
                    style="Wit grape ale with Frontenac Blanc",
                    abv=4.8,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Tightlines IPA",
                    brewery="Gnarly Cedar",
                    style="West Coast style - Deep Cut Cascade",
                    abv=7.0,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Hop Duster IPA",
                    brewery="Gnarly Cedar",
                    style="Hazy IPA - Galaxy, Citra, Chinook",
                    abv=6.8,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Alien Philosopher IPA",
                    brewery="Gnarly Cedar",
                    style="Double IPA Sabro, Bru-1, and Dolcita",
                    abv=8.1,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Turtle Cowboy",
                    brewery="Gnarly Cedar",
                    style="Hazy IPA w/ El Dorado, Azacca and Vista hops",
                    abv=7.5,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Strawberry Shakedown",
                    brewery="Gnarly Cedar",
                    style="Milkshake IPA, belma + mosaic hops, vanilla",
                    abv=6.0,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Mammoth Milk Stout",
                    brewery="Gnarly Cedar",
                    style="Milk Stout, roasted, chocolate notes",
                    abv=6.5,
                    price="8",
                    category="CORE",
                ),
                Beer(
                    name="Woolly Wizard",
                    brewery="Gnarly Cedar",
                    style="Coffee Wizard Coffee Milk Stout",
                    abv=6.5,
                    price="8",
                    category="CORE",
                ),
                # --- GUEST BEERS / CIDERS ---
                Beer(
                    name="Drop Top",
                    brewery="Stubborn Brothers",
                    style="Sun Drop Radler",
                    abv=None,
                    price="8",
                    category="GUEST",
                ),
                Beer(
                    name="Cherry Mechanic",
                    brewery="Ahnapee",
                    style="Gluten Free",
                    abv=None,
                    price="8",
                    category="GUEST",
                ),
                Beer(
                    name="Blackberry Sour",
                    brewery="Stubborn Brothers",
                    style="Sour Ale",
                    abv=None,
                    price="8",
                    category="GUEST",
                ),
                Beer(
                    name="Strawberry Cider",
                    brewery="Cider Boys",
                    style="Cider",
                    abv=None,
                    price="8",
                    category="CIDER",
                ),
                Beer(
                    name="Pomegranate Cider",
                    brewery="DownEast Cider",
                    style="Cider",
                    abv=None,
                    price="8",
                    category="CIDER",
                ),
            ]
            db.add_all(beers)
            db.commit()

        if tap_count == 0:
            # create 20 taps to accommodate the larger list
            for i in range(1, 25):
                db.add(Tap(tap_number=i, display_order=i))
            db.commit()
    finally:
        db.close()


seed_if_empty()


# -------------------------
# Routes
# -------------------------
@app.get("/api/menu", response_model=MenuOut)
def get_menu():
    db = SessionLocal()
    try:
        taps = (
            db.query(Tap).order_by(Tap.display_order.asc(), Tap.tap_number.asc()).all()
        )

        out_taps: List[TapOut] = []
        for tap in taps:
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
    db = SessionLocal()
    try:
        q = db.query(Beer)
        if not include_inactive:
            q = q.filter(Beer.is_active == 1)

        beers = (
            q.order_by(Beer.brewery.asc().nullslast(), Beer.name.asc())
             .all()
        )

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
            )
            for b in beers
        ]
    finally:
        db.close()


@app.post("/api/beers", response_model=BeerOut)
async def create_beer(body: BeerIn):
    db = SessionLocal()
    try:
        b = Beer(
            name=body.name.strip(),
            brewery=(body.brewery or None),
            style=(body.style or None),
            abv=body.abv,
            price=(body.price or None),
            description=(body.description or None),
            category=(body.category or "CORE"),  # <-- ADD
            is_active=1 if body.is_active else 0,
        )
        db.add(b)
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
            category=b.category,  # <-- ADD
        )
    finally:
        db.close()


@app.put("/api/beers/{beer_id}", response_model=BeerOut)
async def update_beer(beer_id: int, body: BeerUpdate):
    db = SessionLocal()
    try:
        b = db.query(Beer).filter(Beer.id == beer_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Beer not found")

        if body.name is not None:
            b.name = body.name.strip()
        if body.brewery is not None:
            b.brewery = body.brewery or None
        if body.style is not None:
            b.style = body.style or None
        if body.abv is not None:
            b.abv = body.abv
        if body.price is not None:
            b.price = body.price or None
        if body.description is not None:
            b.description = body.description or None
        if body.is_active is not None:
            b.is_active = 1 if body.is_active else 0
        if body.category is not None:
            b.category = body.category

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
        )
    finally:
        db.close()


@app.delete("/api/beers/{beer_id}")
async def delete_beer(beer_id: int):
    db = SessionLocal()
    try:
        b = db.query(Beer).filter(Beer.id == beer_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Beer not found")

        # soft delete: deactivate instead of removing
        b.is_active = 0
        db.commit()

        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.post("/api/taps/{tap_id}/status", response_model=TapOut)
async def set_tap_status(tap_id: int, body: SetStatusIn):
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
                category=tap.beer.category,  # <-- ADD
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
async def assign_beer(tap_id: int, body: AssignBeerIn):
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
            tap.beer_id = None

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
                category=tap.beer.category,  # <-- ADD
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
async def bulk_upsert_beers(body: BulkBeersIn):
    db = SessionLocal()
    try:
        beers_in = body.beers
        opts = body.options

        if not beers_in:
            raise HTTPException(status_code=400, detail="beers list cannot be empty")

        # normalize + validate categories
        normalized = []
        for b in beers_in:
            cat = (b.category or "CORE").strip().upper()
            if cat not in ALLOWED_CATEGORIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid category '{b.category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}",
                )
            normalized.append((b, cat))

        # optional: nuke actives first
        if opts.disable_all_first:
            db.query(Beer).update({Beer.is_active: 0})
            db.flush()

        if opts.clear_taps_first:
            db.query(Tap).update({Tap.beer_id: None})
            db.flush()

        created = 0
        updated = 0

        # We'll treat (name, brewery) as the unique identity for upsert
        # (works well for menus; avoids duplicate "IPA" name collisions across breweries)
        seen_keys = set()

        for b, cat in normalized:
            name = b.name.strip()
            brewery = b.brewery.strip() if b.brewery and b.brewery.strip() else None

            key = (name.lower(), (brewery or "").lower())
            if key in seen_keys:
                # same beer duplicated in payload -> skip / or error
                continue
            seen_keys.add(key)

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
                updated += 1
            else:
                db.add(
                    Beer(
                        name=name,
                        brewery=brewery,
                        style=b.style,
                        abv=b.abv,
                        price=b.price,
                        description=b.description,
                        category=cat,
                        is_active=1 if b.is_active else 0,
                    )
                )
                created += 1

        db.flush()

        # disable missing (beers not in payload)
        disabled_missing = 0
        if opts.disable_missing:
            # build a filter list of keys to keep active
            # We’ll disable beers that are NOT in payload keys
            all_beers = db.query(Beer).all()
            keep = {(k[0], k[1]) for k in seen_keys}

            for beer in all_beers:
                k = (beer.name.lower(), (beer.brewery or "").lower())
                if k not in keep:
                    if beer.is_active != 0:
                        beer.is_active = 0
                        disabled_missing += 1

        # optional: assign active beers to taps
        assigned = 0
        if opts.assign_to_taps:
            taps = db.query(Tap).order_by(Tap.tap_number.asc()).all()

            if opts.assign_order == "payload":
                # assign in incoming order (only active ones)
                # NOTE: we need to re-fetch Beer rows in DB order matching payload
                ids_to_assign = []
                for b, cat in normalized:
                    if not b.is_active:
                        continue
                    name = b.name.strip()
                    brewery = b.brewery.strip() if b.brewery else None
                    beer_row = (
                        db.query(Beer)
                        .filter(Beer.name == name)
                        .filter(
                            or_(
                                Beer.brewery == brewery,
                                (Beer.brewery.is_(None) if brewery is None else False),
                            )
                        )
                        .first()
                    )
                    if beer_row:
                        ids_to_assign.append(beer_row.id)
            else:
                # house_first: CORE first then GUEST/CIDER
                active = db.query(Beer).filter(Beer.is_active == 1).all()
                core = [b for b in active if (b.category or "CORE").upper() == "CORE"]
                guest = [
                    b
                    for b in active
                    if (b.category or "").upper() in ("GUEST", "CIDER")
                ]
                # stable ordering
                core.sort(key=lambda x: ((x.brewery or ""), x.name))
                guest.sort(key=lambda x: ((x.brewery or ""), x.name))
                ids_to_assign = [b.id for b in (core + guest)]

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


@app.delete("/api/beers/{beer_id}")
async def delete_beer(beer_id: int):
    db = SessionLocal()
    try:
        # 1. Find the beer record
        b = db.query(Beer).filter(Beer.id == beer_id).first()
        if not b:
            raise HTTPException(status_code=404, detail="Beer not found")

        # 2. Clear this beer from any Taps it is currently assigned to
        # This prevents "Foreign Key" errors that block deletion
        db.query(Tap).filter(Tap.beer_id == beer_id).update({Tap.beer_id: None})
        db.flush()

        # 3. PERMANENTLY remove the beer from the database
        db.delete(b)
        db.commit()

        # 4. Notify TV displays to refresh
        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.websocket("/ws/menu")
async def ws_menu(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "version": hub.version})
        while True:
            await ws.receive()  # <-- IMPORTANT
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)
