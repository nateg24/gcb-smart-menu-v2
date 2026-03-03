from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
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
    or_,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Smart Menu Backend v0")
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static", html=True),
    name="static",
)


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
    display_order = Column(Integer, nullable=False, default=0)


class Tap(Base):
    __tablename__ = "taps"
    id = Column(Integer, primary_key=True, index=True)
    tap_number = Column(Integer, nullable=False, unique=True)
    beer_id = Column(Integer, ForeignKey("beers.id"), nullable=True)
    status = Column(String, nullable=False, default=TapStatus.ON.value)
    display_order = Column(Integer, nullable=False, default=0)
    last_updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    beer = relationship("Beer")


# -------------------------
# Schemas
# -------------------------
ALLOWED_CATEGORIES = {"CORE", "GUEST", "CIDER"}


class BeerIn(BaseModel):
    name: str
    brewery: Optional[str] = None
    style: Optional[str] = None
    abv: Optional[float] = None
    price: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "CORE"
    is_active: bool = True
    display_order: Optional[int] = None


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
    disable_all_first: bool = False
    disable_missing: bool = False
    clear_taps_first: bool = False
    assign_to_taps: bool = False
    assign_order: str = "house_first"  # "house_first" | "payload"


class BulkBeersIn(BaseModel):
    options: BulkImportOptions = Field(default_factory=BulkImportOptions)
    beers: List[BeerBulkItem]


class ReorderTapsIn(BaseModel):
    order: List[int]


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
# Startup: schema + seed
# -------------------------
def ensure_schema() -> None:
    """
    create_all() only creates missing tables.
    This function ALSO adds missing columns for older SQLite DBs.
    """
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # beers.display_order
        try:
            conn.execute(text("SELECT display_order FROM beers LIMIT 1"))
        except OperationalError:
            conn.execute(
                text(
                    "ALTER TABLE beers ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
                )
            )

        # taps.display_order
        try:
            conn.execute(text("SELECT display_order FROM taps LIMIT 1"))
        except OperationalError:
            conn.execute(
                text(
                    "ALTER TABLE taps ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
                )
            )


def seed_if_empty() -> None:
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

        if tap_count == 0:
            for i in range(1, 25):
                db.add(Tap(tap_number=i, display_order=i))
            db.commit()
    finally:
        db.close()


class ReorderBeersIn(BaseModel):
    order: List[int]


@app.on_event("startup")
def on_startup():
    ensure_schema()
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
async def create_beer(body: BeerIn):
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
async def update_beer(beer_id: int, body: BeerUpdate):
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
            b.category = body.category
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
async def delete_beer(beer_id: int):
    """
    Soft delete: deactivate instead of removing row.
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
async def bulk_upsert_beers(body: BulkBeersIn):
    db = SessionLocal()
    try:
        beers_in = body.beers
        opts = body.options

        if not beers_in:
            raise HTTPException(status_code=400, detail="beers list cannot be empty")

        normalized = []
        for b in beers_in:
            cat = (b.category or "CORE").strip().upper()
            if cat not in ALLOWED_CATEGORIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid category '{b.category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}",
                )
            normalized.append((b, cat))

        if opts.disable_all_first:
            db.query(Beer).update({Beer.is_active: 0})
            db.flush()

        if opts.clear_taps_first:
            db.query(Tap).update({Tap.beer_id: None})
            db.flush()

        created = 0
        updated = 0

        seen_keys = set()

        for b, cat in normalized:
            name = b.name.strip()
            brewery = b.brewery.strip() if b.brewery and b.brewery.strip() else None

            key = (name.lower(), (brewery or "").lower())
            if key in seen_keys:
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

        assigned = 0
        if opts.assign_to_taps:
            taps = db.query(Tap).order_by(Tap.tap_number.asc()).all()

            if opts.assign_order == "payload":
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
async def reorder_beers(body: ReorderBeersIn):
    db = SessionLocal()
    try:
        # ensure all IDs exist (optional, but nice)
        existing_ids = {b.id for b in db.query(Beer.id).all()}
        for beer_id in body.order:
            if beer_id not in existing_ids:
                raise HTTPException(
                    status_code=400, detail=f"Beer id {beer_id} not found"
                )

        # update display_order in the new sequence
        for idx, beer_id in enumerate(body.order):
            db.query(Beer).filter(Beer.id == beer_id).update({"display_order": idx})

        db.commit()
        await hub.broadcast_menu_updated()
        return {"ok": True}
    finally:
        db.close()


@app.post("/api/taps/reorder")
async def reorder_taps(body: ReorderTapsIn):
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
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello", "version": hub.version})
        while True:
            await ws.receive()
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)
