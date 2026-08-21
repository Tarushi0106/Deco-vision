from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import AttendanceRecord

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

@router.get("/")
def get_attendance_records(db: Session = Depends(get_db)):
    records = db.query(AttendanceRecord).all()
    return records