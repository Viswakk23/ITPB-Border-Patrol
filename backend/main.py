import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

import alerts
import cameras
import config
import console
import maintenance
import reports
import simulator
from database import Base, engine, SessionLocal
from models import Vehicle

HERE = Path(__file__).resolve().parent
Base.metadata.create_all(bind=engine)

# (id, type, fuel %, home base)
FLEET = [
    ("JP-1", "Patrol Jeep", 85, "main"),
    ("JP-2", "Patrol Jeep", 70, "main"),
    ("QRT-1", "Quick Reaction Vehicle", 92, "main"),
    ("APC-1", "Armoured APC", 78, "main"),
    ("EV-1", "Emergency Vehicle", 100, "main"),
    ("DR-1", "Recon Drone", 90, "main"),
    ("DR-2", "Recon Drone", 88, "main"),
    ("HC-1", "Helicopter", 95, "main"),
    ("R-QRV-1", "Quick Reaction Vehicle", 100, "reserve"),
    ("R-APC-1", "Armoured APC", 100, "reserve"),
    ("R-EV-1", "Emergency Vehicle", 100, "reserve"),
    ("R-HC-1", "Helicopter", 100, "kargil"),
    ("R-HC-2", "Helicopter", 100, "rear"),
]

with SessionLocal() as db:
    if db.query(Vehicle).count() == 0:
        for vid, vtype, fuel, base in FLEET:
            b = config.BASES[base]
            db.add(Vehicle(id=vid, type=vtype, fuel=fuel, lat=b["lat"], lon=b["lon"]))
        db.commit()


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=simulator.run, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(alerts.router)
app.include_router(maintenance.router)
app.include_router(reports.router)
app.include_router(cameras.router)
app.include_router(console.router)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def to_dict(v):
    return {"id": v.id, "type": v.type, "fuel": v.fuel,
            "status": v.status, "lat": v.lat, "lon": v.lon}


def read_json(name):
    p = HERE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


class VehicleIn(BaseModel):
    id: str
    type: str
    fuel: float
    lat: float
    lon: float
    status: str = "patrol"


@app.get("/")
def home():
    return {"message": "Border dashboard is running"}


@app.get("/config")
def get_config():
    return {"sector": config.SECTOR_NAME, "center": config.MAP_CENTER,
            "zoom": config.MAP_ZOOM,
            "base": {"name": config.BASE_NAME, "lat": config.BASE[0], "lon": config.BASE[1]}}


@app.get("/bases")
def get_bases():
    return [{"key": k, "name": b["name"], "lat": b["lat"], "lon": b["lon"], "kind": b["kind"]}
            for k, b in config.BASES.items()]


@app.get("/border")
def get_border():
    return read_json("border.json")


@app.get("/posts")
def get_posts():
    return read_json("posts.json")


@app.get("/vehicles")
def get_vehicles(db: Session = Depends(get_db)):
    return [to_dict(v) for v in db.query(Vehicle).order_by(Vehicle.id).all()]


@app.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: str, db: Session = Depends(get_db)):
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return to_dict(v)


@app.post("/vehicles")
def add_vehicle(data: VehicleIn, db: Session = Depends(get_db)):
    if db.get(Vehicle, data.id):
        raise HTTPException(status_code=400, detail="This id already exists")
    v = Vehicle(**data.model_dump())
    db.add(v)
    db.commit()
    return to_dict(v)


FRONTEND = HERE.parent / "frontend"
app.mount("/map", StaticFiles(directory=FRONTEND, html=True), name="map")