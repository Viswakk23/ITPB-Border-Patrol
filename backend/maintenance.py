import json
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import predictor
from database import SessionLocal
from models import Maint, Notice, Vehicle

router = APIRouter()
HERE = Path(__file__).resolve().parent
DAILY_KM = 100        # assumed km driven per day, to turn km into days


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def status_of(m):
    if m.fault:
        return "FAULT"
    if m.km_since_service >= m.interval_km:
        return "OVERDUE"
    if m.km_since_service >= 0.85 * m.interval_km:
        return "DUE SOON"
    if m.health < 50:
        return "AT RISK"
    return "OK"


def to_dict(m, v):
    return {"vehicle": m.vehicle_id, "type": v.type if v else "",
            "state": v.status if v else "",
            "health": round(m.health, 1),
            "km_since_service": round(m.km_since_service, 1),
            "interval_km": m.interval_km,
            "km_left": round(max(0, m.interval_km - m.km_since_service), 1),
            "last_service": m.last_service, "fault": m.fault,
            "status": status_of(m)}


@router.get("/maintenance")
def list_maintenance(db: Session = Depends(get_db)):
    vehicles = {v.id: v for v in db.query(Vehicle).all()}
    rows = db.query(Maint).order_by(Maint.vehicle_id).all()
    return [to_dict(m, vehicles.get(m.vehicle_id)) for m in rows]


@router.post("/maintenance/{vehicle_id}/service")
def complete_service(vehicle_id: str, db: Session = Depends(get_db)):
    m = db.get(Maint, vehicle_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    m.km_since_service = 0
    m.health = 100
    m.fault = ""
    m.last_service = str(date.today())
    for n in db.query(Notice).filter(Notice.vehicle_id == vehicle_id,
                                     Notice.status == "open").all():
        n.status = "done"
    db.add(Notice(vehicle_id=vehicle_id, level="INFO", text="Service completed",
                  created=datetime.now().isoformat(timespec="seconds"), status="done"))
    db.commit()
    return to_dict(m, db.get(Vehicle, vehicle_id))


@router.get("/notices")
def list_notices(db: Session = Depends(get_db)):
    rows = db.query(Notice).order_by(Notice.id.desc()).limit(30).all()
    return [{"id": n.id, "vehicle": n.vehicle_id, "level": n.level, "text": n.text,
             "created": n.created, "status": n.status} for n in rows]


@router.get("/predictions")
def predictions(db: Session = Depends(get_db)):
    metrics = {}
    mf = HERE / "rul_metrics.json"
    if mf.exists():
        metrics = json.loads(mf.read_text(encoding="utf-8"))

    vehicles = {v.id: v for v in db.query(Vehicle).all()}
    rows = []
    for m in db.query(Maint).order_by(Maint.vehicle_id).all():
        res = predictor.predict(m.vehicle_id, m.health)
        if res is None:
            return {"ready": False, "model": {}, "rows": []}
        km, (temp, vib, volt) = res
        if m.fault:
            km = 0.0
        risk = "HIGH" if km < 150 else "MEDIUM" if km < 300 else "LOW"

        if risk == "HIGH":
            text = "Predictive warning: failure likely soon, schedule maintenance"
            if not db.query(Notice).filter(Notice.vehicle_id == m.vehicle_id,
                                           Notice.text == text,
                                           Notice.status == "open").first():
                db.add(Notice(vehicle_id=m.vehicle_id, level="WARNING", text=text,
                              created=datetime.now().isoformat(timespec="seconds")))

        v = vehicles.get(m.vehicle_id)
        rows.append({"vehicle": m.vehicle_id, "type": v.type if v else "",
                     "health": round(m.health, 1), "risk": risk,
                     "km_left": round(km), "days_left": round(km / DAILY_KM, 1),
                     "temp": round(temp), "vib": round(vib, 1), "volt": round(volt, 1),
                     "fault": m.fault})
    db.commit()
    return {"ready": True, "model": metrics, "rows": rows}