# NHTSA & Automotive Diagnostic API

A unified, production-ready Python API microservice powered by **FastAPI** combining vehicle VIN decoding, safety recall lookups, and OBD-II diagnostic trouble code (DTC) analysis.

This service brings together the three core components of the NHTSA automotive diagnostic toolkit into a single, high-performance containerized service ready for cloud deployment on platforms like Easypanel, Docker, or Kubernetes.

---

## ⚡ Features

- **VIN Decoder (`/api/vin/...`)**: Decodes 17-character VINs using the official NHTSA vPIC API with an integrated offline fallback covering 2,000+ manufacturer WMI codes.
- **Safety Recall Lookup (`/api/recalls/...`)**: Queries safety recall campaigns, defect descriptions, and remedy details via official NHTSA recall databases by Year/Make/Model or VIN.
- **OBD-II DTC Database (`/api/dtc/...`)**: Fast offline querying of powertrain (P), chassis (C), body (B), and network (U) diagnostic trouble codes with generic and manufacturer-specific definitions.
- **Interactive Documentation**: Built-in Swagger/OpenAPI UI for live browser testing.
- **Container Ready**: Dockerfile included with root and `/health` probes.

---

## 📁 Project Structure

```text
nhtsa-data-api/
├── modules/
│   ├── vin/                    # VIN decoding logic & WMI offline database
│   │   ├── nhtsa_vin_decoder.py
│   │   └── wmi_database.py
│   ├── recalls/                # Safety recall lookup logic
│   │   └── recall_lookup.py
│   └── dtc/                    # OBD-II DTC SQLite database & search engine
│       ├── dtc_database.py
│       └── dtc_codes.db
├── app.py                      # FastAPI routes and orchestration
├── Dockerfile                  # Container build definition
├── requirements.txt            # Python dependencies
└── README.md
```

---

## 🚀 Quick Start (Local Development)

### Prerequisites

- Python 3.10+
- Git

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/your-username/nhtsa-data-api.git](https://github.com/your-username/nhtsa-data-api.git)
   cd nhtsa-data-api
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Start the development server:**
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000 --reload
   ```

5. Access the interactive API docs at `http://localhost:8000/docs`.

---

## 🐳 Deployment (Docker & Easypanel)

### Build and Run with Docker

```bash
docker build -t nhtsa-data-api .
docker run -d -p 8000:8000 --name nhtsa-api nhtsa-data-api
```

### Deploying on Easypanel

1. Create a new **App Service** linked to your GitHub repository.
2. Select **Dockerfile** as the build method and set the **Build Path** to `/`.
3. Set the service port to **`8000`**.
4. Attach your custom domain or subdomain in the **Domains** tab to automatically generate an SSL certificate.

---

## 📖 API Reference

### Health Probes
- `GET /` - Basic service check.
- `GET /health` - Health status probe for orchestrators.

---

### 1. VIN Decoder
Decodes vehicle specifications using live NHTSA vPIC APIs with automatic offline fallback.

- **Endpoint:** `GET /api/decode/{vin}`
- **Parameters:** `vin` (17-character vehicle identification number)
- **Example Request:**
  ```bash
  curl -X GET "[https://your-domain.com/api/decode/1HGCM82633A004352](https://your-domain.com/api/decode/1HGCM82633A004352)"
  ```
- **Example Response:**
  ```json
  {
    "vin": "1HGCM82633A004352",
    "year": "2003",
    "make": "HONDA",
    "model": "Accord",
    "trim": "EX",
    "body_class": "Sedan",
    "source": "nhtsa_api"
  }
  ```

---

### 2. Safety Recall Lookup
Queries active and historical safety recall notices.

- **Endpoint:** `GET /api/recalls`
- **Query Parameters:**
  - `make` (string, required) - e.g., `honda`
  - `model` (string, required) - e.g., `accord`
  - `year` (integer, required) - e.g., `2003`
- **Example Request:**
  ```bash
  curl -X GET "[https://your-domain.com/api/recalls?make=honda&model=accord&year=2003](https://your-domain.com/api/recalls?make=honda&model=accord&year=2003)"
  ```
- **Example Response:**
  ```json
  {
    "count": 1,
    "recalls": [
      {
        "nhtsa_campaign_number": "19V182000",
        "component": "AIR BAGS",
        "summary": "Driver frontal air bag inflator may explode...",
        "remedy": "Dealers will replace the air bag inflator free of charge."
      }
    ]
  }
  ```

---

### 3. OBD-II DTC Lookup
Looks up diagnostic trouble codes from the offline database.

- **Endpoint:** `GET /api/dtc/{code}`
- **Parameters:** 
  - `code` (string, path) - e.g., `P0300`
  - `manufacturer` (string, optional query) - e.g., `FORD`
- **Example Request:**
  ```bash
  curl -X GET "[https://your-domain.com/api/dtc/P0300](https://your-domain.com/api/dtc/P0300)"
  ```
- **Example Response:**
  ```json
  {
    "code": "P0300",
    "type": "Powertrain",
    "description": "Random/Multiple Cylinder Misfire Detected",
    "category": "Generic"
  }
  ```

---

## 🛠 Tech Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/)
- **ASGI Server:** [Uvicorn](https://www.uvicorn.org/)
- **HTTP Client:** [Requests](https://requests.readthedocs.io/)
- **Storage Engine:** SQLite (DTC & WMI sets)
- **Base Image:** Python 3.11 Slim

---

## 📜 Credits & Acknowledgments

This API server wraps and consolidates the core automotive diagnostic Python libraries originally developed by **[Wal33D](https://github.com/Wal33D)**:

- [Wal33D/nhtsa-vin-decoder](https://github.com/Wal33D/nhtsa-vin-decoder)
- [Wal33D/nhtsa-recall-lookup](https://github.com/Wal33D/nhtsa-recall-lookup)
- [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database)

Licensed under the [MIT License](LICENSE).