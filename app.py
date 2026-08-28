import os
import requests
from fastapi import FastAPI, HTTPException, Query
from typing import Optional, Dict, Any

# Local module imports
from modules.vin.nhtsa_vin_decoder import NHTSAVinDecoder
from modules.vin.wmi_database import WMIDatabase
from modules.dtc.dtc_database import DTCDatabase

# -------------------------------------------------------------------
# FastAPI App with auto-enabled inputs for Swagger Docs
# -------------------------------------------------------------------
app = FastAPI(
    title="NHTSA & Automotive Diagnostics API",
    description="Unified REST API microservice for VIN decoding, safety recalls, and OBD-II DTC diagnostic trouble code lookups.",
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={"tryItOutEnabled": True}  # Auto-enables inputs
)

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
# 2. Safety Recalls Endpoint (Supports VIN OR Make/Model/Year)
# -------------------------------------------------------------------
@app.get("/api/recalls", tags=["Safety Recalls"])
def get_recalls(
    vin: Optional[str] = Query(None, description="17-character VIN (auto-resolves Year, Make, Model)"),
    make: Optional[str] = Query(None, description="Vehicle Make (e.g., Tesla, Honda)"),
    model: Optional[str] = Query(None, description="Vehicle Model (e.g., Model S, Accord)"),
    year: Optional[int] = Query(None, description="Model Year (e.g., 2014, 2023)")
):
    """
    Queries official NHTSA safety recall campaigns. 
    You can query by providing either a **VIN** OR by providing **make, model, and year**.
    """
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
                raise HTTPException(status_code=422, detail="Could not resolve Make, Model, or Year from this VIN.")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to resolve VIN for recall check: {str(e)}")

    if not make or not model or not year:
        raise HTTPException(
            status_code=400, 
            detail="You must provide either 'vin' OR all three of ('make', 'model', 'year')."
        )

    url = f"https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="NHTSA Recalls API request failed.")
        
        data = response.json()
        results = data.get("results", [])
        
        recalls = []
        for r in results:
            recalls.append({
                "nhtsa_campaign_number": r.get("NHTSACampaignNumber"),
                "component": r.get("Component"),
                "summary": r.get("Summary"),
                "consequence": r.get("Conequence"),
                "remedy": r.get("Remedy"),
                "notes": r.get("Notes")
            })
            
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
    """
    code = code.strip().upper()
    try:
        # Create thread-safe request instance
        db = get_dtc_instance()
        
        dtc_info = None
        if manufacturer:
            dtc_info = db.get_dtc(code, manufacturer.strip().upper())
            
        if not dtc_info:
            dtc_info = db.get_dtc(code)

        # Close connection cleanly if method exists
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