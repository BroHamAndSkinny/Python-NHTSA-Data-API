"""
cron_sync_recalls.py

Queries all unique vehicle makes, models, and years stored in `vehicle_sync_profiles`,
calls the NHTSA API for each one, and refreshes the local database.
"""

import time
import requests
from datetime import datetime
from sqlalchemy import select
from database import (
    SessionLocal, VehicleSyncProfile, RecallCampaign,
    CampaignVehicleAssociation, init_db
)

def sync_all_tracked_vehicles():
    init_db()
    db = SessionLocal()

    try:
        profiles = db.scalars(select(VehicleSyncProfile)).all()
        print(f"[{datetime.utcnow().isoformat()}] Starting sync for {len(profiles)} tracked vehicle profiles...")

        for profile in profiles:
            make, model, year = profile.make, profile.model, profile.year
            print(f"Syncing {year} {make} {model}...")

            url = f"https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}"
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    print(f"Failed to fetch {year} {make} {model}: HTTP {resp.status_code}")
                    continue

                raw_results = resp.json().get("results", [])
                for r in raw_results:
                    c_num = (r.get("NHTSACampaignNumber") or r.get("nhtsaCampaignNumber") or "").strip().upper()
                    if not c_num:
                        continue

                    campaign = db.scalar(select(RecallCampaign).where(RecallCampaign.campaign_number == c_num))
                    if not campaign:
                        campaign = RecallCampaign(
                            campaign_number=c_num,
                            mfr_recall_number=r.get("ManufacturerCampaignNumber") or r.get("manufacturerCampaignNumber") or r.get("mfrRecallNumber"),
                            tsa_action_number=r.get("TSAActionNumber") or r.get("tsaActionNumber"),
                            recall_date=r.get("ReportReceivedDate") or r.get("reportReceivedDate"),
                            component=r.get("Component") or r.get("component"),
                            summary=r.get("Summary") or r.get("summary"),
                            consequence=r.get("Conequence") or r.get("consequence"),
                            remedy=r.get("Remedy") or r.get("remedy"),
                            notes=r.get("Notes") or r.get("notes"),
                            park_it=bool(r.get("parkIt") or r.get("ParkIt") or False),
                            park_outside=bool(r.get("parkOutSide") or r.get("ParkOutside") or r.get("parkOutside") or False),
                            over_the_air_update=bool(r.get("overTheAirUpdate") or r.get("OverTheAirUpdate") or False)
                        )
                        db.add(campaign)
                        db.flush()

                    assoc = db.scalar(
                        select(CampaignVehicleAssociation).where(
                            CampaignVehicleAssociation.campaign_number == c_num,
                            CampaignVehicleAssociation.make == make.upper(),
                            CampaignVehicleAssociation.model == model.lower(),
                            CampaignVehicleAssociation.year == int(year)
                        )
                    )
                    if not assoc:
                        db.add(CampaignVehicleAssociation(
                            campaign_number=c_num, make=make.upper(), model=model.lower(), year=int(year)
                        ))

                profile.last_synced_at = datetime.utcnow()
                db.commit()
                time.sleep(1)  # Respectful pause between NHTSA API calls

            except Exception as e:
                print(f"Error syncing {year} {make} {model}: {e}")
                db.rollback()

        print("Recall sync complete.")
    finally:
        db.close()

if __name__ == "__main__":
    sync_all_tracked_vehicles()