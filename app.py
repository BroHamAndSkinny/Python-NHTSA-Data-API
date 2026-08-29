import os
import re
import time
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

# Local module imports
from modules.vin.nhtsa_vin_decoder import NHTSAVinDecoder
from modules.vin.wmi_database import WMIDatabase
from modules.dtc.dtc_database import DTCDatabase

# Disable default /docs to render our custom styled Swagger UI
app = FastAPI(
    title="NHTSA & Automotive Diagnostics API",
    description="""
Unified REST API microservice for VIN decoding, safety recalls, and OBD-II DTC diagnostic trouble code lookups.

### Example Queries (Open in New Tab):
* <a href="/api/decode/5YJSA1E26EF000001" target="_blank"><code>/api/decode/5YJSA1E26EF000001</code></a>
* <a href="/api/recalls?vin=5YJSA1E26EF000001" target="_blank"><code>/api/recalls?vin=5YJSA1E26EF000001</code></a>
* <a href="/api/recalls?make=tesla&model=model%20s&year=2014" target="_blank"><code>/api/recalls?make=tesla&model=model%20s&year=2014</code></a>
* <a href="/api/recalls?campaign_number=17V260000" target="_blank"><code>/api/recalls?campaign_number=17V260000</code></a>
* <a href="/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01" target="_blank"><code>/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01</code></a>
* <a href="/api/dtc/P0300" target="_blank"><code>/api/dtc/P0300</code></a>
    """,
    version="1.4.0",
    docs_url=None,
    redoc_url="/redoc"
)

# -------------------------------------------------------------------
# In-Memory Recall Cache Config
# -------------------------------------------------------------------
RECALL_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 86400  # 24 Hours

# -------------------------------------------------------------------
# Custom Swagger UI Route
# -------------------------------------------------------------------
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
    """Creates a thread-safe database connection instance for the request."""
    if os.path.exists(dtc_db_path):
        return DTCDatabase(dtc_db_path)
    return DTCDatabase()

vin_decoder = NHTSAVinDecoder()

def parse_nhtsa_date(date_str: Optional[str]) -> Optional[str]:
    """Normalizes NHTSA date strings to YYYY-MM-DD."""
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

def format_recall_item(r: Dict[str, Any], fallback_make: Optional[str] = None, fallback_model: Optional[str] = None, fallback_year: Optional[int] = None) -> Dict[str, Any]:
    """Normalizes raw NHTSA JSON record into a standardized dictionary with Year/Make/Model."""
    raw_date = r.get("ReportReceivedDate") or r.get("reportReceivedDate")
    
    raw_make = r.get("Make") or r.get("make") or fallback_make
    raw_model = r.get("Model") or r.get("model") or fallback_model
    raw_year = r.get("ModelYear") or r.get("modelYear") or fallback_year

    parsed_year = None
    if raw_year is not None:
        try:
            parsed_year = int(str(raw_year).strip())
        except (ValueError, TypeError):
            parsed_year = raw_year

    return {
        "nhtsa_campaign_number": r.get("NHTSACampaignNumber") or r.get("nhtsaCampaignNumber"),
        "make": str(raw_make).upper() if raw_make else None,
        "model": str(raw_model).title() if raw_model else None,
        "year": parsed_year,
        "recall_date": parse_nhtsa_date(raw_date),
        "component": r.get("Component") or r.get("component"),
        "summary": r.get("Summary") or r.get("summary"),
        "consequence": r.get("Conequence") or r.get("consequence"),
        "remedy": r.get("Remedy") or r.get("remedy"),
        "notes": r.get("Notes") or r.get("notes"),
        "park_it": bool(r.get("parkIt") or r.get("ParkIt") or False),
        "park_outside": bool(r.get("parkOutSide") or r.get("ParkOutside") or r.get("parkOutside") or False),
        "over_the_air_update": bool(r.get("overTheAirUpdate") or r.get("OverTheAirUpdate") or False)
    }

def fetch_vehicle_recalls(make: str, model: str, year: int) -> List[Dict[str, Any]]:
    """Fetches and caches recalls for a specific make/model/year."""
    cache_key = f"{make.strip().upper()}|{model.strip().lower()}|{year}"
    now = time.time()
    
    if cache_key in RECALL_CACHE and (now - RECALL_CACHE[cache_key]["time"]) < CACHE_TTL_SECONDS:
        return RECALL_CACHE[cache_key]["data"]

    url = f"https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="NHTSA Recalls API request failed.")
    
    raw_results = response.json().get("results", [])
    formatted = [format_recall_item(r, fallback_make=make, fallback_model=model, fallback_year=year) for r in raw_results]
    
    RECALL_CACHE[cache_key] = {"time": now, "data": formatted}
    return formatted

def run_batch_recall_logic(
    vin_list: List[str], 
    since_date: Optional[str] = None, 
    only_critical: bool = False
) -> Dict[str, Any]:
    """Shared execution engine for batch recall queries."""
    results: List[Dict[str, Any]] = []
    decoded_vehicles: Dict[str, Dict[str, Any]] = {}
    unique_groups: Dict[tuple, List[str]] = {}

    for raw_vin in vin_list:
        v = raw_vin.strip().strip('"').strip("'").upper()
        if not v:
            continue
        if len(v) != 17:
            decoded_vehicles[v] = {"status": "error", "message": "Invalid VIN length (must be 17 characters)"}
            continue
        try:
            vehicle = vin_decoder.decode(v)
            make = getattr(vehicle, "make", None)
            model = getattr(vehicle, "model", None)
            year = getattr(vehicle, "year", None)

            if not make or not model or not year:
                decoded_vehicles[v] = {"status": "error", "message": "Could not decode vehicle metadata from VIN"}
                continue

            group_key = (str(make).upper(), str(model).lower(), int(year))
            decoded_vehicles[v] = {
                "status": "success",
                "make": str(make).upper(),
                "model": str(model).title(),
                "year": int(year),
                "group_key": group_key
            }
            unique_groups.setdefault(group_key, []).append(v)
        except Exception as e:
            decoded_vehicles[v] = {"status": "error", "message": f"Decode error: {str(e)}"}

    # Fetch recalls ONCE per unique vehicle group
    group_recalls: Dict[tuple, List[Dict[str, Any]]] = {}
    for (make, model, year) in unique_groups.keys():
        try:
            group_recalls[(make, model, year)] = fetch_vehicle_recalls(make, model, year)
        except Exception:
            group_recalls[(make, model, year)] = []

    # Assemble response
    for raw_vin in vin_list:
        v = raw_vin.strip().strip('"').strip("'").upper()
        if not v:
            continue
        v_data = decoded_vehicles.get(v, {"status": "error", "message": "Unknown error"})

        if v_data["status"] != "success":
            results.append({
                "vin": v,
                "status": "error",
                "message": v_data["message"]
            })
            continue

        raw_list = group_recalls.get(v_data["group_key"], [])
        filtered_recalls = []
        for r in raw_list:
            if since_date and r.get("recall_date") and r["recall_date"] < since_date:
                continue
            if only_critical and not (r.get("park_it") or r.get("park_outside")):
                continue
            filtered_recalls.append(r)

        results.append({
            "vin": v,
            "status": "success",
            "make": v_data["make"],
            "model": v_data["model"],
            "year": v_data["year"],
            "total_recalls": len(filtered_recalls),
            "recalls": filtered_recalls
        })

    return {
        "total_queried": len(results),
        "unique_vehicle_types": len(unique_groups),
        "results": results
    }

# -------------------------------------------------------------------
# Pydantic Schemas for Batch Processing
# -------------------------------------------------------------------
class BatchRecallRequest(BaseModel):
    vins: List[str] = Field(..., description="List of 17-character VINs", example=["5YJSA1E26EF000001", "1HGCM82633A004352"])
    since_date: Optional[str] = Field(None, description="Filter recalls on/after date (YYYY-MM-DD)", example="2020-01-01")
    only_critical: bool = Field(False, description="Include only Park It / Park Outside critical safety recalls")

# -------------------------------------------------------------------
# Health Checks
# -------------------------------------------------------------------
@app.get("/", tags=["System Probes"])
def root_status():
    """Basic service identifier probe."""
    return {"status": "ok", "service": "nhtsa-diagnostics-api"}

@app.get("/health", tags=["System Probes"])
def health_check():
    """Health status probe for reverse proxies and orchestrators."""
    return {"status": "healthy"}

# -------------------------------------------------------------------
# 1. VIN Decoder Endpoint
# -------------------------------------------------------------------
@app.get("/api/decode/{vin}", tags=["VIN Decoder"])
def decode_vin(vin: str):
    """
    Decodes a 17-character VIN using the live NHTSA vPIC API with offline WMI fallback.
    Returns core vehicle metadata and all available technical specifications.

    Live example: <a href="/api/decode/5YJSA1E26EF000001" target="_blank"><code>/api/decode/5YJSA1E26EF000001</code></a>
    """
    vin = vin.strip().upper()
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="Invalid VIN length. VIN must be exactly 17 characters.")
    
    try:
        vehicle = vin_decoder.decode(vin)
        
        specs: Dict[str, Any] = {}
        for attr, val in vars(vehicle).items():
            if val is not None and attr not in ["vin", "year", "make", "model", "trim", "body_class"]:
                specs[attr] = val

        return {
            "vin": vin,
            "year": getattr(vehicle, "year", None),
            "make": getattr(vehicle, "make", None),
            "model": getattr(vehicle, "model", None),
            "trim": getattr(vehicle, "trim", None),
            "body_class": getattr(vehicle, "body_class", None),
            "specifications": specs,
            "source": "nhtsa_vpic"
        }
    except Exception as e:
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
# 2. Safety Recalls Endpoints (Single & Batch)
# -------------------------------------------------------------------
@app.get("/api/recalls", tags=["Safety Recalls"])
def get_recalls(
    vin: Optional[str] = Query(None, description="17-character VIN (auto-resolves Year, Make, Model)"),
    make: Optional[str] = Query(None, description="Vehicle Make (e.g., Tesla, Honda)"),
    model: Optional[str] = Query(None, description="Vehicle Model (e.g., Model S, Accord)"),
    year: Optional[int] = Query(None, description="Model Year (e.g., 2014, 2023)"),
    campaign_number: Optional[str] = Query(None, description="NHTSA Campaign Number (e.g., 17V260000)"),
    since_date: Optional[str] = Query(None, description="Filter recalls on/after this date (YYYY-MM-DD)"),
    only_critical: bool = Query(False, description="Filter for Park It / Park Outside warnings only")
):
    """
    Queries official NHTSA safety recalls by Campaign Number, VIN, or Make/Model/Year.

    Live examples:
    * Query by VIN: <a href="/api/recalls?vin=5YJSA1E26EF000001" target="_blank"><code>/api/recalls?vin=5YJSA1E26EF000001</code></a>
    * Query by Make/Model/Year: <a href="/api/recalls?make=tesla&model=model%20s&year=2014" target="_blank"><code>/api/recalls?make=tesla&model=model%20s&year=2014</code></a>
    * Query by Campaign Number: <a href="/api/recalls?campaign_number=17V260000" target="_blank"><code>/api/recalls?campaign_number=17V260000</code></a>
    * Query with date filter: <a href="/api/recalls?make=tesla&model=model%20s&year=2014&since_date=2020-01-01" target="_blank"><code>/api/recalls?make=tesla&model=model%20s&year=2014&since_date=2020-01-01</code></a>
    """
    recalls: List[Dict[str, Any]] = []

    # Query by Campaign Number
    if campaign_number:
        c_num = campaign_number.strip().upper()
        url = f"https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber={c_num}"
        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="NHTSA Campaign API request failed.")
            raw_results = response.json().get("results", [])
            recalls = [format_recall_item(r) for r in raw_results]
            return {
                "campaign_number": c_num,
                "total_recalls": len(recalls),
                "recalls": recalls
            }
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=502, detail=f"NHTSA Recalls API connection error: {str(e)}")

    # Resolve Make/Model/Year if VIN provided
    if vin:
        vin = vin.strip().upper()
        if len(vin) != 17:
            raise HTTPException(status_code=400, detail="Invalid VIN length. Must be 17 characters.")
        try:
            vehicle = vin_decoder.decode(vin)
            make = getattr(vehicle, "make", None)
            model = getattr(vehicle, "model", None)
            year = getattr(vehicle, "year", None)
            
            if not make or not model or not year:
                raise HTTPException(status_code=422, detail="Could not resolve Make, Model, or Year from VIN.")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to resolve VIN for recall check: {str(e)}")

    if not make or not model or not year:
        raise HTTPException(
            status_code=400, 
            detail="Provide 'campaign_number', 'vin', OR all three of ('make', 'model', 'year')."
        )

    try:
        raw_recalls = fetch_vehicle_recalls(make=make, model=model, year=int(year))
        for r in raw_recalls:
            if since_date and r.get("recall_date") and r["recall_date"] < since_date:
                continue
            if only_critical and not (r.get("park_it") or r.get("park_outside")):
                continue
            recalls.append(r)

        return {
            "query_vin": vin if vin else None,
            "make": str(make).upper(),
            "model": str(model).title(),
            "year": int(year),
            "total_recalls": len(recalls),
            "recalls": recalls
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"NHTSA Recalls API connection error: {str(e)}")


@app.get("/api/recalls/batch", tags=["Safety Recalls"])
def get_batch_recalls_query(
    vins: str = Query(..., description="Comma-separated list of VINs (e.g., 5YJSA1E26EF000001, 1HGCM82633A004352)"),
    since_date: Optional[str] = Query(None, description="Filter recalls on/after this date (YYYY-MM-DD)"),
    only_critical: bool = Query(False, description="Filter for Park It / Park Outside warnings only")
):
    """
    Checks recalls for multiple fleet vehicles using URL query parameters.
    Only issues one external request per unique vehicle make/model/year group.

    Live example: <a href="/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01" target="_blank"><code>/api/recalls/batch?vins=5YJSA1E26EF000001,1HGCM82633A004352&since_date=2020-01-01</code></a>
    """
    vin_list = [v.strip() for v in vins.split(",") if v.strip()]
    return run_batch_recall_logic(vin_list, since_date=since_date, only_critical=only_critical)


@app.post("/api/recalls/batch", tags=["Safety Recalls"])
def get_batch_recalls_json(payload: BatchRecallRequest):
    """
    Checks recalls for multiple fleet vehicles using a JSON request body.
    Only issues one external request per unique vehicle make/model/year group.
    """
    return run_batch_recall_logic(payload.vins, since_date=payload.since_date, only_critical=payload.only_critical)

# -------------------------------------------------------------------
# 3. OBD-II DTC Lookup Endpoint (Thread-Safe)
# -------------------------------------------------------------------
@app.get("/api/dtc/{code}", tags=["OBD-II Diagnostics"])
def get_dtc(
    code: str, 
    manufacturer: Optional[str] = Query(None, description="Optional OEM filter (e.g. FORD, GM, HONDA)")
):
    """
    Looks up OBD-II Diagnostic Trouble Code definitions (Powertrain, Chassis, Body, Network).
    Queries generic SAE J2012 definitions or manufacturer-specific codes offline.

    Live examples:
    * Generic DTC query: <a href="/api/dtc/P0300" target="_blank"><code>/api/dtc/P0300</code></a>
    * Manufacturer-specific query: <a href="/api/dtc/P1000?manufacturer=FORD" target="_blank"><code>/api/dtc/P1000?manufacturer=FORD</code></a>
    """
    code = code.strip().upper()
    try:
        db = get_dtc_instance()
        
        dtc_info = None
        if manufacturer:
            dtc_info = db.get_dtc(code, manufacturer.strip().upper())
            
        if not dtc_info:
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