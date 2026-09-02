import os
import re
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select

# Database & ORM
import database
from database import (
    init_db, get_db, DecodedVIN, VehicleSyncProfile,
    RecallCampaign, CampaignVehicleAssociation
)

# Local module imports
from modules.vin.nhtsa_vin_decoder import NHTSAVinDecoder
from modules.vin.wmi_database import WMIDatabase
from modules.dtc.dtc_database import DTCDatabase
from modules.vehicles import client as vehicle_client

app = FastAPI(
    title="NHTSA & Automotive Diagnostics API",
    description="""
Unified REST API microservice for VIN decoding, safety recalls, OBD-II DTC diagnostic trouble code lookups, and vehicle dropdown lookups.

### Example Queries (Open in New Tab):
* <a href="/api/vehicles/years" target="_blank"><code>/api/vehicles/years</code></a>
* <a href="/api/vehicles/makes?year=2003" target="_blank"><code>/api/vehicles/makes?year=2003</code></a>
* <a href="/api/vehicles/models?year=2003&make=Mazda" target="_blank"><code>/api/vehicles/models?year=2003&make=Mazda</code></a>
* <a href="/api/vehicles/styles?year=2003&make=Mazda&model=Protege" target="_blank"><code>/api/vehicles/styles?year=2003&make=Mazda&model=Protege</code></a>
* <a href="/api/decode/5YJSA1E26EF000001" target="_blank"><code>/api/decode/5YJSA1E26EF000001</code></a>
* <a href="/api/decode/5YJSA1E26EF000001?refresh=true" target="_blank"><code>/api/decode/5YJSA1E26EF000001?refresh=true</code></a>
* <a href="/api/recalls?vin=5YJSA1E26EF000001" target="_blank"><code>/api/recalls?vin=5YJSA1E26EF000001</code></a>
* <a href="/api/recalls?make=tesla&model=model%20s&year=2014" target="_blank"><code>/api/recalls?make=tesla&model=model%20s&year=2014</code></a>
* <a href="/api/recalls/campaign/17V260000" target="_blank"><code>/api/recalls/campaign/17V260000</code></a>
* <a href="/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01" target="_blank"><code>/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01</code></a>
* <a href="/api/dtc/P0300" target="_blank"><code>/api/dtc/P0300</code></a>
* <a href="/api/admin/db/stats" target="_blank"><code>/api/admin/db/stats</code></a>
* <a href="/api/admin/db/tracked-vehicles" target="_blank"><code>/api/admin/db/tracked-vehicles</code></a>
* <a href="/api/admin/db/vins" target="_blank"><code>/api/admin/db/vins</code></a>
    """,
    version="2.1.0",
    docs_url=None,
    redoc_url="/redoc"
)

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Interactive Docs",
        swagger_ui_parameters={"tryItOutEnabled": True}
    )
    custom_css = "<style>.try-out { display: none !important; }</style></head>"
    return HTMLResponse(content=html.body.decode("utf-8").replace("</head>", custom_css))

# -------------------------------------------------------------------
# Database Path & Helper
# -------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
dtc_db_path = os.path.join(current_dir, "modules", "dtc", "dtc_codes.db")

def get_dtc_instance() -> DTCDatabase:
    if os.path.exists(dtc_db_path):
        return DTCDatabase(dtc_db_path)
    return DTCDatabase()

vin_decoder = NHTSAVinDecoder()

def parse_nhtsa_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    epoch_match = re.search(r"\d+", date_str)
    if epoch_match and len(epoch_match.group()) >= 10:
        try:
            ts = int(epoch_match.group()[:10])
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            pass
    return date_str

def extract_vin_specs(vehicle) -> Dict[str, Any]:
    """Helper to extract non-core attributes from decoded vehicle object."""
    specs: Dict[str, Any] = {}
    for attr, val in vars(vehicle).items():
        if val is not None and attr not in ["vin", "year", "make", "model", "trim", "body_class"]:
            specs[attr] = val
    return specs

# -------------------------------------------------------------------
# Database Recall Sync Helper
# -------------------------------------------------------------------
def sync_vehicle_recalls_from_api(db: Session, make: str, model: str, year: int) -> List[Dict[str, Any]]:
    """Calls NHTSA, writes to local DB tables, updates sync status, and returns list."""
    url = f"https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="NHTSA Recalls API request failed.")
    
    raw_results = response.json().get("results", [])
    formatted_recalls = []

    for r in raw_results:
        c_num = (r.get("NHTSACampaignNumber") or r.get("nhtsaCampaignNumber") or "").strip().upper()
        if not c_num:
            continue

        raw_date = r.get("ReportReceivedDate") or r.get("reportReceivedDate")
        parsed_date = parse_nhtsa_date(raw_date)

        campaign = db.scalar(select(RecallCampaign).where(RecallCampaign.campaign_number == c_num))
        if not campaign:
            campaign = RecallCampaign(
                campaign_number=c_num,
                mfr_recall_number=r.get("ManufacturerCampaignNumber") or r.get("manufacturerCampaignNumber") or r.get("mfrRecallNumber"),
                tsa_action_number=r.get("TSAActionNumber") or r.get("tsaActionNumber"),
                recall_date=parsed_date,
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
                campaign_number=c_num,
                make=make.upper(),
                model=model.lower(),
                year=int(year)
            ))

        formatted_recalls.append({
            "nhtsa_campaign_number": campaign.campaign_number,
            "mfr_recall_number": campaign.mfr_recall_number,
            "tsa_action_number": campaign.tsa_action_number,
            "make": make.upper(),
            "model": model.title(),
            "year": int(year),
            "recall_date": campaign.recall_date,
            "component": campaign.component,
            "summary": campaign.summary,
            "consequence": campaign.consequence,
            "remedy": campaign.remedy,
            "notes": campaign.notes,
            "park_it": campaign.park_it,
            "park_outside": campaign.park_outside,
            "over_the_air_update": campaign.over_the_air_update
        })

    profile = db.scalar(
        select(VehicleSyncProfile).where(
            VehicleSyncProfile.make == make.upper(),
            VehicleSyncProfile.model == model.lower(),
            VehicleSyncProfile.year == int(year)
        )
    )
    if not profile:
        profile = VehicleSyncProfile(make=make.upper(), model=model.lower(), year=int(year))
        db.add(profile)
    profile.last_synced_at = datetime.utcnow()

    db.commit()
    return formatted_recalls

def fetch_local_or_sync_recalls(db: Session, make: str, model: str, year: int) -> List[Dict[str, Any]]:
    """Checks DB for cached recalls; fetches from NHTSA on a miss."""
    profile = db.scalar(
        select(VehicleSyncProfile).where(
            VehicleSyncProfile.make == make.upper(),
            VehicleSyncProfile.model == model.lower(),
            VehicleSyncProfile.year == int(year)
        )
    )
    
    if profile and profile.last_synced_at:
        query = (
            select(RecallCampaign)
            .join(CampaignVehicleAssociation)
            .where(
                CampaignVehicleAssociation.make == make.upper(),
                CampaignVehicleAssociation.model == model.lower(),
                CampaignVehicleAssociation.year == int(year)
            )
        )
        results = db.scalars(query).all()
        return [{
            "nhtsa_campaign_number": c.campaign_number,
            "mfr_recall_number": c.mfr_recall_number,
            "tsa_action_number": c.tsa_action_number,
            "make": make.upper(),
            "model": model.title(),
            "year": int(year),
            "recall_date": c.recall_date,
            "component": c.component,
            "summary": c.summary,
            "consequence": c.consequence,
            "remedy": c.remedy,
            "notes": c.notes,
            "park_it": c.park_it,
            "park_outside": c.park_outside,
            "over_the_air_update": c.over_the_air_update
        } for c in results]

    return sync_vehicle_recalls_from_api(db, make, model, year)

# -------------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------------
class BatchRecallRequest(BaseModel):
    vins: List[str] = Field(..., description="List of 17-character VINs", example=["5YJSA1E26EF000001", "1HGCM82633A004352"])
    since_date: Optional[str] = Field(None, description="Filter recalls on/after date (YYYY-MM-DD)", example="2020-01-01")
    only_critical: bool = Field(False, description="Include only Park It / Park Outside critical safety recalls")

# -------------------------------------------------------------------
# System Probes
# -------------------------------------------------------------------
@app.get("/", tags=["System Probes"])
def root_status():
    return {"status": "ok", "service": "nhtsa-diagnostics-api"}

@app.get("/health", tags=["System Probes"])
def health_check():
    return {"status": "healthy"}

# -------------------------------------------------------------------
# 1. Vehicle Dropdown Lookup Endpoints
# -------------------------------------------------------------------
@app.get("/api/vehicles/years", tags=["Vehicle Dropdown Lookups"])
def get_vehicle_years():
    current_year = datetime.now().year + 1
    return {
        "min_year": 1981,
        "max_year": current_year,
        "years": list(range(current_year, 1980, -1))
    }

@app.get("/api/vehicles/makes", tags=["Vehicle Dropdown Lookups"])
def get_vehicle_makes(year: int = Query(..., description="Model Year (e.g. 2003, 2024)")):
    try:
        makes = vehicle_client.list_makes_for_year(year)
        make_names = sorted([getattr(m, "make_name", str(m)) for m in makes])
        return {"year": year, "total_makes": len(make_names), "makes": make_names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading vehicle makes: {str(e)}")

@app.get("/api/vehicles/models", tags=["Vehicle Dropdown Lookups"])
def get_vehicle_models(
    year: int = Query(..., description="Model Year (e.g. 2003, 2024)"),
    make: str = Query(..., description="Make Name (e.g. Mazda, Toyota, Tesla)")
):
    try:
        models = vehicle_client.list_models_for_year_make(year=year, make_name=make.strip())
        model_names = sorted([getattr(m, "model_name", str(m)) for m in models])
        return {"year": year, "make": make, "total_models": len(model_names), "models": model_names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading vehicle models: {str(e)}")

@app.get("/api/vehicles/styles", tags=["Vehicle Dropdown Lookups"])
def get_vehicle_styles(
    year: int = Query(..., description="Model Year (e.g. 2003, 2024)"),
    make: str = Query(..., description="Make Name (e.g. Mazda, Toyota)"),
    model: str = Query(..., description="Model Name (e.g. Protege, Camry)")
):
    try:
        styles = vehicle_client.list_styles_for_year_make_model(year=year, make=make.strip(), model=model.strip())
        style_names = [getattr(s, "style_name", str(s)) for s in styles]
        return {"year": year, "make": make, "model": model, "total_styles": len(style_names), "styles": style_names}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading vehicle styles: {str(e)}")

# -------------------------------------------------------------------
# 2. VIN Decoder Endpoint (Auto-Heals & Supports ?refresh=true)
# -------------------------------------------------------------------
@app.get("/api/decode/{vin}", tags=["VIN Decoder"])
def decode_vin(
    vin: str, 
    refresh: bool = Query(False, description="Force re-query upstream NHTSA vPIC and overwrite local cached specs"),
    db: Session = Depends(get_db)
):
    """
    Decodes a 17-character VIN.
    Checks the local database first. If specifications are missing or `refresh=true`, queries NHTSA vPIC and updates the DB.

    Live examples:
    * Cached query: <a href="/api/decode/5YJSA1E26EF000001" target="_blank"><code>/api/decode/5YJSA1E26EF000001</code></a>
    * Force refresh: <a href="/api/decode/5YJSA1E26EF000001?refresh=true" target="_blank"><code>/api/decode/5YJSA1E26EF000001?refresh=true</code></a>
    """
    vin = vin.strip().upper()
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="Invalid VIN length. VIN must be exactly 17 characters.")
    
    cached = db.get(DecodedVIN, vin)
    
    # Return immediately if cached and specifications are populated (unless refresh is requested)
    if cached and not refresh and cached.specifications:
        return {
            "vin": cached.vin,
            "year": cached.year,
            "make": cached.make,
            "model": cached.model,
            "trim": cached.trim,
            "body_class": cached.body_class,
            "specifications": cached.specifications,
            "source": "local_database"
        }

    # Query NHTSA vPIC to populate or self-heal
    try:
        vehicle = vin_decoder.decode(vin)
        specs = extract_vin_specs(vehicle)

        if not cached:
            cached = DecodedVIN(
                vin=vin,
                year=getattr(vehicle, "year", None),
                make=getattr(vehicle, "make", None),
                model=getattr(vehicle, "model", None),
                trim=getattr(vehicle, "trim", None),
                body_class=getattr(vehicle, "body_class", None),
                specifications=specs
            )
            db.add(cached)
        else:
            # Update existing row with full data
            cached.year = getattr(vehicle, "year", cached.year)
            cached.make = getattr(vehicle, "make", cached.make)
            cached.model = getattr(vehicle, "model", cached.model)
            cached.trim = getattr(vehicle, "trim", cached.trim)
            cached.body_class = getattr(vehicle, "body_class", cached.body_class)
            cached.specifications = specs

        db.commit()

        return {
            "vin": vin,
            "year": cached.year,
            "make": cached.make,
            "model": cached.model,
            "trim": cached.trim,
            "body_class": cached.body_class,
            "specifications": specs,
            "source": "nhtsa_vpic"
        }
    except Exception as e:
        if cached:
            return {
                "vin": cached.vin,
                "year": cached.year,
                "make": cached.make,
                "model": cached.model,
                "trim": cached.trim,
                "body_class": cached.body_class,
                "specifications": cached.specifications or {},
                "source": "local_database",
                "warning": f"Refresh failed: {str(e)}"
            }
        
        manufacturer = WMIDatabase.get_manufacturer(vin)
        year = WMIDatabase.get_year(vin)
        return {
            "vin": vin,
            "year": year,
            "make": manufacturer,
            "model": None,
            "trim": None,
            "body_class": None,
            "specifications": {},
            "source": "offline_fallback",
            "warning": str(e)
        }

# -------------------------------------------------------------------
# 3. Safety Recalls Endpoints (Database First)
# -------------------------------------------------------------------
@app.get("/api/recalls/campaign/{campaign_number}", tags=["Safety Recalls"])
def get_recall_by_campaign(
    campaign_number: str, 
    refresh: bool = Query(False, description="Force re-query upstream NHTSA and update local database"),
    db: Session = Depends(get_db)
):
    """
    Looks up a safety recall campaign by NHTSA Campaign Number.
    Checks local DB first; falls back to NHTSA on a miss or when `refresh=true`.

    Live example: <a href="/api/recalls/campaign/17V260000" target="_blank"><code>/api/recalls/campaign/17V260000</code></a>
    """
    c_num = campaign_number.strip().upper()

    cached_campaign = db.scalar(select(RecallCampaign).where(RecallCampaign.campaign_number == c_num))
    if cached_campaign and cached_campaign.affected_vehicles and not refresh:
        return {
            "campaign_number": cached_campaign.campaign_number,
            "mfr_recall_number": cached_campaign.mfr_recall_number,
            "tsa_action_number": cached_campaign.tsa_action_number,
            "recall_date": cached_campaign.recall_date,
            "component": cached_campaign.component,
            "summary": cached_campaign.summary,
            "consequence": cached_campaign.consequence,
            "remedy": cached_campaign.remedy,
            "notes": cached_campaign.notes,
            "park_it": cached_campaign.park_it,
            "park_outside": cached_campaign.park_outside,
            "over_the_air_update": cached_campaign.over_the_air_update,
            "total_affected_models": len(cached_campaign.affected_vehicles),
            "affected_vehicles": [
                {"make": v.make, "model": v.model.title(), "year": v.year}
                for v in cached_campaign.affected_vehicles
            ],
            "source": "local_database"
        }

    url = f"https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber={c_num}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="NHTSA Campaign API request failed.")
        
        raw_results = response.json().get("results", [])
        if not raw_results:
            raise HTTPException(status_code=404, detail=f"No recall found matching campaign number '{c_num}'.")
        
        first = raw_results[0]
        raw_date = first.get("ReportReceivedDate") or first.get("reportReceivedDate")

        if not cached_campaign:
            cached_campaign = RecallCampaign(
                campaign_number=c_num,
                mfr_recall_number=first.get("ManufacturerCampaignNumber") or first.get("manufacturerCampaignNumber") or first.get("mfrRecallNumber"),
                tsa_action_number=first.get("TSAActionNumber") or first.get("tsaActionNumber"),
                recall_date=parse_nhtsa_date(raw_date),
                component=first.get("Component") or first.get("component"),
                summary=first.get("Summary") or first.get("summary"),
                consequence=first.get("Conequence") or first.get("consequence"),
                remedy=first.get("Remedy") or first.get("remedy"),
                notes=first.get("Notes") or first.get("notes"),
                park_it=bool(first.get("parkIt") or first.get("ParkIt") or False),
                park_outside=bool(first.get("parkOutSide") or first.get("ParkOutside") or False),
                over_the_air_update=bool(first.get("overTheAirUpdate") or first.get("OverTheAirUpdate") or False)
            )
            db.add(cached_campaign)
            db.flush()

        affected = []
        seen = set()
        for r in raw_results:
            v_make = str(r.get("Make") or r.get("make") or "").upper()
            v_model = str(r.get("Model") or r.get("model") or "").strip().lower()
            raw_y = r.get("ModelYear") or r.get("modelYear")
            try:
                v_year = int(str(raw_y).strip())
            except Exception:
                continue

            v_key = (v_make, v_model, v_year)
            if v_key not in seen and any(v_key):
                seen.add(v_key)
                existing = db.scalar(
                    select(CampaignVehicleAssociation).where(
                        CampaignVehicleAssociation.campaign_number == c_num,
                        CampaignVehicleAssociation.make == v_make,
                        CampaignVehicleAssociation.model == v_model,
                        CampaignVehicleAssociation.year == v_year
                    )
                )
                if not existing:
                    db.add(CampaignVehicleAssociation(
                        campaign_number=c_num, make=v_make, model=v_model, year=v_year
                    ))
                affected.append({"make": v_make, "model": v_model.title(), "year": v_year})

        db.commit()

        return {
            "campaign_number": c_num,
            "mfr_recall_number": cached_campaign.mfr_recall_number,
            "tsa_action_number": cached_campaign.tsa_action_number,
            "recall_date": cached_campaign.recall_date,
            "component": cached_campaign.component,
            "summary": cached_campaign.summary,
            "consequence": cached_campaign.consequence,
            "remedy": cached_campaign.remedy,
            "notes": cached_campaign.notes,
            "park_it": cached_campaign.park_it,
            "park_outside": cached_campaign.park_outside,
            "over_the_air_update": cached_campaign.over_the_air_update,
            "total_affected_models": len(affected),
            "affected_vehicles": affected,
            "source": "nhtsa_live"
        }
    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"NHTSA Recalls API connection error: {str(e)}")

@app.get("/api/recalls", tags=["Safety Recalls"])
def get_recalls(
    vin: Optional[str] = Query(None, description="17-character VIN (auto-resolves Year, Make, Model)"),
    make: Optional[str] = Query(None, description="Vehicle Make (e.g., Tesla, Honda)"),
    model: Optional[str] = Query(None, description="Vehicle Model (e.g., Model S, Accord)"),
    year: Optional[int] = Query(None, description="Model Year (e.g., 2014, 2023)"),
    since_date: Optional[str] = Query(None, description="Filter recalls on/after this date (YYYY-MM-DD)"),
    only_critical: bool = Query(False, description="Filter for Park It / Park Outside warnings only"),
    db: Session = Depends(get_db)
):
    """
    Queries safety recalls for a vehicle via **VIN** OR **Make/Model/Year**.
    Checks the local database before making an external call.

    Live examples:
    * Query by VIN: <a href="/api/recalls?vin=5YJSA1E26EF000001" target="_blank"><code>/api/recalls?vin=5YJSA1E26EF000001</code></a>
    * Query by Make/Model/Year: <a href="/api/recalls?make=tesla&model=model%20s&year=2014" target="_blank"><code>/api/recalls?make=tesla&model=model%20s&year=2014</code></a>
    """
    if vin:
        vin = vin.strip().upper()
        if len(vin) != 17:
            raise HTTPException(status_code=400, detail="Invalid VIN length. Must be 17 characters.")
        
        cached_vin = db.get(DecodedVIN, vin)
        if cached_vin:
            make, model, year = cached_vin.make, cached_vin.model, cached_vin.year
        else:
            try:
                vehicle = vin_decoder.decode(vin)
                make = getattr(vehicle, "make", None)
                model = getattr(vehicle, "model", None)
                year = getattr(vehicle, "year", None)
                if not make or not model or not year:
                    raise HTTPException(status_code=422, detail="Could not resolve Make, Model, or Year from VIN.")
                
                # Save full specs even when resolved through recalls
                specs = extract_vin_specs(vehicle)
                db.add(DecodedVIN(
                    vin=vin, year=int(year), make=str(make).upper(), model=str(model).title(),
                    trim=getattr(vehicle, "trim", None), body_class=getattr(vehicle, "body_class", None),
                    specifications=specs
                ))
                db.commit()
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to resolve VIN: {str(e)}")

    if not make or not model or not year:
        raise HTTPException(status_code=400, detail="Provide either 'vin' OR all three of ('make', 'model', 'year').")

    had_profile = db.scalar(
        select(VehicleSyncProfile).where(
            VehicleSyncProfile.make == str(make).upper(),
            VehicleSyncProfile.model == str(model).lower(),
            VehicleSyncProfile.year == int(year)
        )
    )

    raw_recalls = fetch_local_or_sync_recalls(db, str(make), str(model), int(year))
    filtered = []
    for r in raw_recalls:
        if since_date and r.get("recall_date") and r["recall_date"] < since_date:
            continue
        if only_critical and not (r.get("park_it") or r.get("park_outside")):
            continue
        filtered.append(r)

    return {
        "query_vin": vin if vin else None,
        "make": str(make).upper(),
        "model": str(model).title(),
        "year": int(year),
        "total_recalls": len(filtered),
        "source": "local_database" if (had_profile and had_profile.last_synced_at) else "nhtsa_live",
        "recalls": filtered
    }

@app.get("/api/recalls/batch", tags=["Safety Recalls"])
def get_batch_recalls_query(
    vins: str = Query(..., description="Comma-separated list of VINs"),
    since_date: Optional[str] = Query(None, description="Filter recalls on/after this date (YYYY-MM-DD)"),
    only_critical: bool = Query(False, description="Filter for Park It / Park Outside warnings only"),
    db: Session = Depends(get_db)
):
    vin_list = [v.strip() for v in vins.split(",") if v.strip()]
    return process_batch(vin_list, since_date, only_critical, db)

@app.post("/api/recalls/batch", tags=["Safety Recalls"])
def get_batch_recalls_json(payload: BatchRecallRequest, db: Session = Depends(get_db)):
    return process_batch(payload.vins, payload.since_date, payload.only_critical, db)

def process_batch(vin_list: List[str], since_date: Optional[str], only_critical: bool, db: Session):
    results = []
    for raw_vin in vin_list:
        v = raw_vin.strip().strip('"').strip("'").upper()
        if len(v) != 17:
            results.append({"vin": v, "status": "error", "message": "Invalid VIN length"})
            continue
        
        cached_vin = db.get(DecodedVIN, v)
        if cached_vin:
            make, model, year = cached_vin.make, cached_vin.model, cached_vin.year
        else:
            try:
                vehicle = vin_decoder.decode(v)
                make, model, year = getattr(vehicle, "make", None), getattr(vehicle, "model", None), getattr(vehicle, "year", None)
                if not make or not model or not year:
                    results.append({"vin": v, "status": "error", "message": "Could not resolve metadata"})
                    continue
                specs = extract_vin_specs(vehicle)
                db.add(DecodedVIN(
                    vin=v, year=int(year), make=str(make).upper(), model=str(model).title(),
                    trim=getattr(vehicle, "trim", None), body_class=getattr(vehicle, "body_class", None),
                    specifications=specs
                ))
                db.commit()
            except Exception as e:
                results.append({"vin": v, "status": "error", "message": str(e)})
                continue

        had_profile = db.scalar(
            select(VehicleSyncProfile).where(
                VehicleSyncProfile.make == str(make).upper(),
                VehicleSyncProfile.model == str(model).lower(),
                VehicleSyncProfile.year == int(year)
            )
        )

        raw = fetch_local_or_sync_recalls(db, str(make), str(model), int(year))
        filtered = [
            r for r in raw
            if not (since_date and r.get("recall_date") and r["recall_date"] < since_date)
            and not (only_critical and not (r.get("park_it") or r.get("park_outside")))
        ]
        results.append({
            "vin": v,
            "status": "success",
            "make": str(make).upper(),
            "model": str(model).title(),
            "year": int(year),
            "total_recalls": len(filtered),
            "source": "local_database" if (had_profile and had_profile.last_synced_at) else "nhtsa_live",
            "recalls": filtered
        })

    return {"total_queried": len(results), "results": results}

# -------------------------------------------------------------------
# 4. Database Management & Admin Endpoints
# -------------------------------------------------------------------
@app.get("/api/admin/db/stats", tags=["Database Management"])
def get_database_stats(db: Session = Depends(get_db)):
    """Returns total record counts across all persistent tables."""
    return {
        "database_engine": database.DATABASE_URL.split(":///")[0],
        "tables": {
            "decoded_vins": db.query(DecodedVIN).count(),
            "tracked_vehicle_profiles": db.query(VehicleSyncProfile).count(),
            "saved_recall_campaigns": db.query(RecallCampaign).count(),
            "campaign_vehicle_associations": db.query(CampaignVehicleAssociation).count()
        }
    }

@app.get("/api/admin/db/tracked-vehicles", tags=["Database Management"])
def list_tracked_vehicles(db: Session = Depends(get_db)):
    """Lists all unique vehicle specifications currently scheduled for nightly cron sync."""
    profiles = db.scalars(select(VehicleSyncProfile).order_by(VehicleSyncProfile.make)).all()
    return {
        "total_tracked": len(profiles),
        "vehicles": [
            {
                "id": p.id,
                "make": p.make,
                "model": p.model.title(),
                "year": p.year,
                "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None
            }
            for p in profiles
        ]
    }

@app.get("/api/admin/db/vins", tags=["Database Management"])
def list_saved_vins(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    """Lists stored VINs and vehicle summaries currently saved in the database."""
    vins = db.scalars(select(DecodedVIN).order_by(DecodedVIN.created_at.desc()).limit(limit)).all()
    return {
        "count": len(vins),
        "vins": [
            {
                "vin": v.vin,
                "year": v.year,
                "make": v.make,
                "model": v.model,
                "has_specifications": bool(v.specifications),
                "created_at": v.created_at.isoformat() if v.created_at else None
            }
            for v in vins
        ]
    }

@app.delete("/api/admin/db/vins/{vin}", tags=["Database Management"])
def purge_saved_vin(vin: str, db: Session = Depends(get_db)):
    """Purges a single VIN from the database so it will be re-decoded on next request."""
    v_clean = vin.strip().upper()
    cached = db.get(DecodedVIN, v_clean)
    if not cached:
        raise HTTPException(status_code=404, detail=f"VIN '{v_clean}' not found in database.")
    db.delete(cached)
    db.commit()
    return {"status": "purged", "vin": v_clean}

@app.delete("/api/admin/db/recalls", tags=["Database Management"])
def purge_vehicle_recalls(
    make: str = Query(..., description="Make Name (e.g. Tesla)"),
    model: str = Query(..., description="Model Name (e.g. Model S)"),
    year: int = Query(..., description="Model Year (e.g. 2014)"),
    db: Session = Depends(get_db)
):
    """Removes cached recall records and sync tracking for a vehicle to force an upstream NHTSA re-fetch."""
    m, mod, y = make.strip().upper(), model.strip().lower(), int(year)
    assocs = db.scalars(
        select(CampaignVehicleAssociation).where(
            CampaignVehicleAssociation.make == m,
            CampaignVehicleAssociation.model == mod,
            CampaignVehicleAssociation.year == y
        )
    ).all()
    for a in assocs:
        db.delete(a)

    profile = db.scalar(
        select(VehicleSyncProfile).where(
            VehicleSyncProfile.make == m,
            VehicleSyncProfile.model == mod,
            VehicleSyncProfile.year == y
        )
    )
    if profile:
        db.delete(profile)

    db.commit()
    return {
        "status": "purged",
        "vehicle": f"{y} {m} {mod.title()}",
        "associations_removed": len(assocs)
    }

# -------------------------------------------------------------------
# 5. OBD-II DTC Lookup Endpoint
# -------------------------------------------------------------------
@app.get("/api/dtc/{code}", tags=["OBD-II Diagnostics"])
def get_dtc(
    code: str, 
    manufacturer: Optional[str] = Query(None, description="Optional OEM filter (e.g. FORD, GM, HONDA)")
):
    code = code.strip().upper()
    try:
        db = get_dtc_instance()
        dtc_info = db.get_dtc(code, manufacturer.strip().upper()) if manufacturer else db.get_dtc(code)
        if not dtc_info and manufacturer:
            dtc_info = db.get_dtc(code)
        if hasattr(db, "close"):
            db.close()
        if not dtc_info:
            raise HTTPException(status_code=404, detail=f"DTC code '{code}' not found in database.")
        
        return {
            "code": getattr(dtc_info, "code", code),
            "type": getattr(dtc_info, "type_name", None) or getattr(dtc_info, "category", "Powertrain"),
            "description": getattr(dtc_info, "description", None),
            "manufacturer": getattr(dtc_info, "manufacturer", manufacturer or "GENERIC")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DTC Database query error: {str(e)}")