# NHTSA Automotive Data API Microservice

## Stack & Architecture
- Framework: FastAPI (Python 3.11)
- Persistence: SQLAlchemy ORM with SQLite (`fleet_data.db` stored in `/app/data` on Easypanel), architected for easy PostgreSQL migration.
- Modules:
  - `modules/vin`: NHTSA vPIC online decode + offline WMI fallback.
  - `modules/dtc`: Offline SQLite database for OBD-II codes.
  - `modules/vehicles`: `open-vehicle-db` CSV reader (`modules/vehicles/data/`) for cascading Year/Make/Model/Style dropdowns.
- Sync & Cron: Standalone script `cron_sync_recalls.py` updates tracked vehicle recalls daily.

## Core API Capabilities
- Vehicle Dropdowns: `/api/vehicles/years`, `/makes`, `/models`, `/styles`
- VIN Decoding: `/api/decode/{vin}` (caches full specs in `decoded_vins`, supports `?refresh=true`)
- Recalls:
  - By vehicle: `/api/recalls` (checks `vehicle_sync_profiles` and cached `recall_campaigns`)
  - By campaign: `/api/recalls/campaign/{campaign_number}`
  - Batch: `/api/recalls/batch` (groups by make/model/year)
- Comprehensive Report: `/api/vehicle-report/{vin}` (aggregates specs, recalls, safety ratings, complaints)
- Safety & ODI Complaints: `/api/safety-ratings`, `/api/complaints`
- Admin & Maintenance: `/api/admin/db/stats`, `/tracked-vehicles`, `/vins`, `/recalls`