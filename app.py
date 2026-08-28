import os
import requests
from fastapi import FastAPI, HTTPException, Query
from typing import Optional

# Local module imports matching your `modules/` folder layout
from modules.vin.nhtsa_vin_decoder import NHTSAVinDecoder
from modules.vin.wmi_database import WMIDatabase
from modules.dtc.dtc_database import DTCDatabase

app = FastAPI(
    title="NHTSA & Automotive Diagnostics API",
    description="Unified API for VIN decoding, safety recalls, and OBD-II DTC lookups.",
    version="1.0.0"
)

# 1. Initialize VIN Decoder
vin_decoder = NHTSAVinDecoder()

# 2. Initialize DTC Database with explicit absolute path
current_dir = os.path.dirname(os.path.abspath(__file__))
dtc_db_path = os.path.join(current_dir, "modules", "dtc", "dtc_codes.db")

if os.path.exists(dtc_db_path):
    dtc_db = DTCDatabase(dtc_db_path)
else:
    # Fallback to default constructor if the file was placed in another default directory
    dtc_db = DTCDatabase()

# -------------------------------------------------------------------
# Health Probes
# -------------------------------------------------------------------
@app.get("/")
def root_status():
    return {"status": "ok", "service": "nhtsa-diagnostics-api"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# -------------------------------------------------------------------
# 1. VIN Decoder Endpoint
# -------------------------------------------------------------------
@app.get("/api/decode/{vin}")
def decode_vin(vin: str):
    vin = vin.strip().upper()
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="Invalid VIN length. Must be 17 characters.")
    
    try:
        vehicle = vin_decoder.decode(vin)
        return {
            "vin": vin,
            "year": getattr(vehicle, "year", None),
            "make": getattr(vehicle, "make", None),
            "model": getattr(vehicle, "model", None),
            "trim": getattr(vehicle, "trim", None),
            "body_class": getattr(vehicle, "body_class", None),
            "source": "nhtsa_vpic"
        }
    except Exception as e:
        manufacturer = WMIDatabase.get_manufacturer(vin)
        year = WMIDatabase.get_year(vin)
        return {
            "vin": vin,
            "year": year,
            "make": manufacturer,
            "source": "offline_fallback",
            "warning": str(e)
        }

# -------------------------------------------------------------------
# 2. Safety Recalls Endpoint
# -------------------------------------------------------------------
@app.get("/api/recalls")
def get_recalls(
    make: str = Query(..., description="Vehicle Make (e.g., Honda, Tesla)"),
    model: str = Query(..., description="Vehicle Model (e.g., Accord, Model S)"),
    year: int = Query(..., description="Model Year (e.g., 2014, 2023)")
):
    url = f"https://api.nhtsa.gov/recalls/recallsByVin?make={make}&model={model}&modelYear={year}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="NHTSA Recall API error.")
        
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
            "make": make.upper(),
            "model": model.title(),
            "year": year,
            "total_recalls": len(recalls),
            "recalls": recalls
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to NHTSA Recalls API: {str(e)}")

# -------------------------------------------------------------------
# 3. OBD-II DTC Lookup Endpoint
# -------------------------------------------------------------------
@app.get("/api/dtc/{code}")
def get_dtc(code: str, manufacturer: Optional[str] = None):
    code = code.strip().upper()
    try:
        dtc_info = dtc_db.get_dtc(code, manufacturer)
        if not dtc_info:
            raise HTTPException(status_code=404, detail=f"DTC code '{code}' not found.")
        
        return {
            "code": getattr(dtc_info, "code", code),
            "type": getattr(dtc_info, "type_name", None) or getattr(dtc_info, "category", None),
            "description": getattr(dtc_info, "description", None),
            "manufacturer": getattr(dtc_info, "manufacturer", manufacturer or "GENERIC")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))