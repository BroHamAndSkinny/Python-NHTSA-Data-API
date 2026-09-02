import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    create_engine, String, Integer, Boolean, Text, DateTime, JSON,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
)

# -------------------------------------------------------------------
# Connection Setup (Defaults to SQLite; accepts Postgres connection string)
# -------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./fleet_data.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

# -------------------------------------------------------------------
# Table Models
# -------------------------------------------------------------------
class DecodedVIN(Base):
    __tablename__ = "decoded_vins"

    vin: Mapped[str] = mapped_column(String(17), primary_key=True, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    make: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    trim: Mapped[Optional[str]] = mapped_column(String(50))
    body_class: Mapped[Optional[str]] = mapped_column(String(50))
    specifications: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class VehicleSyncProfile(Base):
    """Stores unique Year/Make/Model specs queried by the API to drive cron jobs."""
    __tablename__ = "vehicle_sync_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("make", "model", "year", name="uq_sync_profile_mmy"),
    )

class RecallCampaign(Base):
    __tablename__ = "recall_campaigns"

    campaign_number: Mapped[str] = mapped_column(String(25), primary_key=True, index=True)
    mfr_recall_number: Mapped[Optional[str]] = mapped_column(String(50))
    tsa_action_number: Mapped[Optional[str]] = mapped_column(String(50))
    recall_date: Mapped[Optional[str]] = mapped_column(String(15))
    component: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    consequence: Mapped[Optional[str]] = mapped_column(Text)
    remedy: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    park_it: Mapped[bool] = mapped_column(Boolean, default=False)
    park_outside: Mapped[bool] = mapped_column(Boolean, default=False)
    over_the_air_update: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    affected_vehicles: Mapped[List["CampaignVehicleAssociation"]] = relationship(
        "CampaignVehicleAssociation", back_populates="campaign", cascade="all, delete-orphan"
    )

class CampaignVehicleAssociation(Base):
    """Maps which vehicle make, model, and year combinations are affected by a campaign."""
    __tablename__ = "campaign_vehicle_associations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_number: Mapped[str] = mapped_column(String(25), ForeignKey("recall_campaigns.campaign_number", ondelete="CASCADE"), index=True)
    make: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    campaign: Mapped["RecallCampaign"] = relationship("RecallCampaign", back_populates="affected_vehicles")

    __table_args__ = (
        UniqueConstraint("campaign_number", "make", "model", "year", name="uq_camp_vehicle"),
    )

class VehicleSafetyRating(Base):
    __tablename__ = "vehicle_safety_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    make: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_rating: Mapped[Optional[str]] = mapped_column(String(20))
    overall_front_crash_rating: Mapped[Optional[str]] = mapped_column(String(20))
    front_crash_driverside_rating: Mapped[Optional[str]] = mapped_column(String(20))
    front_crash_passengerside_rating: Mapped[Optional[str]] = mapped_column(String(20))
    overall_side_crash_rating: Mapped[Optional[str]] = mapped_column(String(20))
    side_crash_driverside_rating: Mapped[Optional[str]] = mapped_column(String(20))
    side_crash_passengerside_rating: Mapped[Optional[str]] = mapped_column(String(20))
    rollover_rating: Mapped[Optional[str]] = mapped_column(String(20))
    rollover_possibility: Mapped[Optional[float]] = mapped_column(JSON)
    complaints_count: Mapped[Optional[int]] = mapped_column(Integer)
    recalls_count: Mapped[Optional[int]] = mapped_column(Integer)
    investigation_count: Mapped[Optional[int]] = mapped_column(Integer)
    raw_ratings: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("make", "model", "year", name="uq_safety_rating_mmy"),
    )

class VehicleComplaint(Base):
    __tablename__ = "vehicle_complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    odi_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    make: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    crash: Mapped[bool] = mapped_column(Boolean, default=False)
    fire: Mapped[bool] = mapped_column(Boolean, default=False)
    injured: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int] = mapped_column(Integer, default=0)
    incident_date: Mapped[Optional[str]] = mapped_column(String(20))
    date_complaint_filed: Mapped[Optional[str]] = mapped_column(String(20))
    components: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("odi_number", name="uq_complaint_odi"),
    )

def init_db():
    """Initializes tables on startup."""
    Base.metadata.create_all(bind=engine)

def get_db():
    """Dependency helper for route handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()