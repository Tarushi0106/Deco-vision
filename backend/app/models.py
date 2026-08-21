from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base


class AttendanceRecord(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    person_id = Column(String, nullable=False)
    person_name = Column(String, nullable=True)
    status = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)