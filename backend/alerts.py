import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config
from database import SessionLocal
from models import Alert, Dispatch, Notice

router = APIRouter()
HERE = Path(__file__).resolve().parent
ACKS = set()      # (alert id, vehicle id) pairs that the crews have confirmed


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AlertIn(BaseModel):
    camera: str
    people: int = 0
    vehicles: int = 0
    kind: str = "Intrusion in restricted zone"
    anomaly: bool = False


def severity_for(people, vehicles, anomaly=False):
    score = people + 2 * vehicles + (2 if anomaly else 0)   # unusual movement raises the level
    if score <= 1:
        return "LOW"
    if score <= 3:
        return "MEDIUM"
    if score <= 5:
        return "HIGH"
    return "CRITICAL"


def camera_location(camera):
    # CAM-1 is at Outpost 1, CAM-2 at Outpost 2, and so on
    p = HERE / "posts.json"
    posts = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    try:
        post = posts[int(camera.split("-")[1]) - 1]
        return post["lat"], post["lon"], post["name"]
    except Exception:
        return config.BASE[0], config.BASE[1], config.BASE_NAME


def to_dict(a, db):
    team = []
    rows = db.query(Dispatch).filter(Dispatch.alert_id == a.id, Dispatch.status != "diverted").all()
    for d in rows:
        base_key = config.HOME.get(d.vehicle_id, "main")
        team.append({
            "vehicle": d.vehicle_id, "type": d.vehicle_type, "troops": d.troops,
            "km": round(d.km, 1), "eta_min": d.eta_min, "status": d.status,
            "acked": (a.id, d.vehicle_id) in ACKS,
            "reinforcement": d.vehicle_id in config.HOME,
            "source": config.BASES[base_key]["name"],
            "route": json.loads(d.route) if (d.route and a.status != "resolved") else [],
        })
    notes = db.query(Notice).filter(Notice.vehicle_id == "CONTROL",
                                    Notice.text.like(f"[#{a.id}]%")).order_by(Notice.id).all()
    control = [n.text.split("] ", 1)[-1] for n in notes]
    return {"id": a.id, "camera": a.camera, "place": a.place, "kind": a.kind,
            "severity": a.severity, "people": a.people, "vehicles": a.vehicles,
            "lat": a.lat, "lon": a.lon, "created": a.created, "status": a.status,
            "team": team, "control": control}


@router.post("/alerts")
def create_alert(data: AlertIn, db: Session = Depends(get_db)):
    # If this camera already has an open alert, do not make a second one
    existing = db.query(Alert).filter(Alert.camera == data.camera,
                                      Alert.status != "resolved").first()
    if existing:
        return to_dict(existing, db)

    lat, lon, place = camera_location(data.camera)
    a = Alert(camera=data.camera, place=place, kind=data.kind,
              severity=severity_for(data.people, data.vehicles, data.anomaly),
              people=data.people, vehicles=data.vehicles, lat=lat, lon=lon,
              created=datetime.now().isoformat(timespec="seconds"))
    db.add(a)
    db.commit()
    db.refresh(a)
    return to_dict(a, db)


@router.get("/alerts")
def list_alerts(db: Session = Depends(get_db)):
    rows = db.query(Alert).order_by(Alert.id.desc()).limit(30).all()
    return [to_dict(a, db) for a in rows]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    a.status = "resolved"
    db.commit()
    return to_dict(a, db)


@router.post("/alerts/{alert_id}/ack/{vehicle_id}")
def acknowledge(alert_id: int, vehicle_id: str):
    ACKS.add((alert_id, vehicle_id))
    return {"ok": True}