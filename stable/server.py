import subprocess
import atexit
import time
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import json
from typing import List, Dict, Optional
import logging
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
import os
import csv
from io import StringIO

# --- Geo helpers (uses geopy with Google Geocoding fallback) ---
try:
    from geopy.distance import geodesic
    from geopy.geocoders import Nominatim
except Exception:
    geodesic = None
    Nominatim = None

from functools import lru_cache
import time

''''''
# Minimal IATA coordinate fallback (extend as needed)
IATA_COORDS = {
    "ZRH": (47.458056, 8.548056),   # Zurich
    "JFK": (40.641311, -73.778139),  # New York JFK
    "SIN": (1.364420, 103.991531),   # Singapore Changi
    "DXB": (25.253174, 55.365673),   # Dubai International
    "ICN": (37.460190, 126.440696),  # Incheon

    "CNSHA": (31.2304, 121.4737),     # Shanghai
    "CNZOS": (30.0440, 122.1391),     # Zhoushan
    "CNSZX": (22.5350, 113.9400),     # Shenzhen
    "CNTAO": (36.0831, 120.3859),     # Qingdao
    "CNCAN": (23.1096, 113.3246),     # Guangzhou
    "KRPUS": (35.1036, 129.0400),     # Busan
    "CNTSN": (39.0860, 117.2179),     # Tianjin
    "HKHKG": (22.3080, 114.2000),     # Hong Kong
    "NLRTM": (51.9470, 4.1367),       # Rotterdam

    ## Ports
    "PHMNS": (14.5833, 120.9667),  # Port of Manila, Philippines
    "PKKHI": (24.8100, 66.9700),   # Port of Karachi, Pakistan

}

def _iata_from_unlocode(code: str):
    if not code:
        return None
    code = code.strip().upper().replace(" ", "")
    
    if len(code) == 5 and code[:2].isalpha() and code[2:].isalnum():
        return code[2:]
    
    if len(code) in [3, 4, 5] and code.isalpha():
        return code  
    
    return None

@lru_cache(maxsize=512)
def _geocode_google(query: str):
    """Use Google Geocoding API as fallback"""
    api_key = os.environ.get('GOOGLE_GEOCODING_API_KEY')
    api_key = "AIzaSyDtH_vBlyU3V0CkS4pV9ERWQ8A-R3jyZ2o"
    if not api_key:
        logger.warning("Google Geocoding API key not found in environment variables")
        return None
    
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            'address': query,
            'key': api_key
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            return (location['lat'], location['lng'])
    except Exception as e:
        logger.error(f"Google Geocoding API error: {e}")
    
    return None

@lru_cache(maxsize=512)
def _geocode_query(query: str):
    """Return (lat, lon) for a free-text query using geopy Nominatim, with Google fallback"""
    # Try Nominatim first
    if Nominatim is not None:
        try:
            geocoder = Nominatim(user_agent="logistics-parser/1.0")
            loc = geocoder.geocode(query, timeout=10)
            # Tiny delay to avoid hammering the service if many sectors
            time.sleep(1/3)
            if loc:
                return (loc.latitude, loc.longitude)
        except Exception:
            pass  # Fall through to Google API
    
    # If Nominatim fails or is not available, try Google Geocoding
    return _geocode_google(query)

def _coords_for_location(loc_str: str):
    if not loc_str:
        return None

    s = str(loc_str).strip()
    
    # 1) First, try to extract and match any known port/airport codes from the string
    # Look for patterns like "PHMNS seaport", "KRICN airport", etc.
    if "seaport" in s.lower() or "port" in s.lower():
        # Extract the code part (usually the first word before "seaport")
        code_match = re.match(r'^([A-Z]{3,5})\s+seaport', s, re.IGNORECASE)
        if not code_match:
            code_match = re.match(r'^([A-Z]{3,5})\s+port', s, re.IGNORECASE)
        
        if code_match:
            code = code_match.group(1).upper()
            if code in IATA_COORDS:
                return IATA_COORDS[code]
            # Also try common variations
            if len(code) == 5 and code[2:] in IATA_COORDS:  # UN/LOCODE format
                return IATA_COORDS[code[2:]]
    
    # 2) Try UN/LOCODE or IATA directly from the entire string
    iata = _iata_from_unlocode(s)
    if iata:
        if iata in IATA_COORDS:
            return IATA_COORDS[iata]
        hit = _geocode_query(f"{iata} airport")
        if hit:
            return hit

    # 3) Handle strings like 'SGSIN airport' or 'USJFK airport'
    if "airport" in s.lower():
        # take the first token; if it's 5-char UN/LOCODE, map to IATA
        first_tok = s.split()[0].upper()
        if len(first_tok) == 5 and first_tok[:2].isalpha():
            iata2 = first_tok[2:]
            if iata2 in IATA_COORDS:
                return IATA_COORDS[iata2]
            hit = _geocode_query(f"{iata2} airport")
            if hit:
                return hit
        # else, try any 3-5 letter code just before 'airport'
        m = re.search(r'([A-Z]{3,5})\s+airport\b', s.upper())
        if m:
            iata3 = m.group(1)
            if iata3 in IATA_COORDS:
                return IATA_COORDS[iata3]
            hit = _geocode_query(f"{iata3} airport")
            if hit:
                return hit

    # 4) Try the string as a whole if it's a known code
    if s in IATA_COORDS:
        return IATA_COORDS[s]

    # 5) Try raw geocoding of the entire string
    hit = _geocode_query(s)
    if hit:
        return hit

    # 6) Postal code fallback
    sg_postal = re.search(r'\b(\d{6})\b', s)
    if sg_postal:
        hit = _geocode_query(sg_postal.group(1))
        if hit:
            return hit

    us_zip = re.search(r'\b(\d{5}(?:-\d{4})?)\b', s)
    if us_zip:
        hit = _geocode_query(us_zip.group(1))
        if hit:
            return hit

    return None

def _distance_between(a, b):
    ca = _coords_for_location(a)
    cb = _coords_for_location(b)
    
    if not ca:
        logger.warning(f"Could not geocode location: {a}")
        # Try to extract and log what type of location this is
        if "airport" in a.lower():
            logger.info(f"  This appears to be an airport location")
        elif "seaport" in a.lower() or "port" in a.lower():
            logger.info(f"  This appears to be a seaport location")
    
    if not cb:
        logger.warning(f"Could not geocode location: {b}")
    
    if not ca or not cb or geodesic is None:
        return 0.0  # Return 0 instead of None to avoid calculation errors
    
    try:
        return round(geodesic(ca, cb).kilometers, 1)
    except Exception:
        return 0.0
# --- End geo helpers ---

# ---------------- Enrichment ----------------
def enrich_addresses_with_llm(locations: List[str]) -> Dict[str, str]:
    """Ask LLM to normalize airports/seaports/addresses into geocodable form."""
    if not locations:
        return {}
    prompt = (
        "You are a logistics geocoding assistant.\n"
        "Convert each location into a proper airport, seaport, or full address that can be geocoded.\n"
        "Return ONLY valid JSON mapping input -> normalized output.\n\n"
        "Example:\n"
        "{\n"
        "  \"PHMNS seaport\": \"Port of Manila, Philippines\",\n"
        "  \"SGSIN airport\": \"Singapore Changi Airport\"\n"
        "}\n\n"
        "Locations:\n" + "\n".join(locations)
    )
    raw = call_llm(prompt, timeout=40)
    try:
        mapping = json.loads(raw)
        for loc in locations:
            if loc not in mapping:
                mapping[loc] = loc
        return mapping
    except Exception as e:
        logger.warning(f"Failed to parse LLM enrichment JSON: {e}")
        return {loc: loc for loc in locations}

def normalize_shipment_addresses(shipments: List[Dict]):
    all_locs = set()
    for shipment in shipments:
        for s in shipment["sectors"]:
            all_locs.add(s["from"])
            all_locs.add(s["to"])
    mapping = enrich_addresses_with_llm(list(all_locs))
    for shipment in shipments:
        for s in shipment["sectors"]:
            s["from"] = mapping.get(s["from"], s["from"])
            s["to"] = mapping.get(s["to"], s["to"])
            s["distance_km"] = _distance_between(s["from"], s["to"])
    return shipments

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Start LLM
ollama_process = subprocess.Popen(["ollama", "serve"])
atexit.register(lambda: ollama_process.terminate())
time.sleep(5)

class PromptRequest(BaseModel):
    text: str

OLLAMA_MODEL = "llama3.1:8b-instruct-q8_0"  # Using a smaller, faster model

def parse_logistics_data(text: str) -> List[Dict]:
    """Parse the tab-separated logistics data format with multiple sectors (AIR or SEA)."""
    # --- Clean up text (handle multi-line addresses) ---
    lines = text.strip().split('\n')
    cleaned_lines = []
    current_line = ""
    
    for line in lines:
        # Skip header lines
        if any(x in line for x in ['Ref No', 'Pickup From', 'Origin', '1st sector']):
            continue
            
        # If line starts with a digit (new record), push current and start new
        if re.match(r'^\d+\t', line) and current_line:
            cleaned_lines.append(current_line)
            current_line = line
        else:
            current_line += " " + line.strip()
    
    if current_line:
        cleaned_lines.append(current_line)
    
    shipments = []
    
    for line in cleaned_lines:
        try:
            csv_reader = csv.reader(StringIO(line), delimiter='\t', quotechar='"')
            columns = next(csv_reader)
            columns = [col.strip() for col in columns]  # preserve empty placeholders
        except Exception as e:
            logging.error(f"CSV parsing failed for line: {line}, error: {e}")
            continue

        if len(columns) < 6:
            logging.warning(f"Skipping line with insufficient columns: {columns}")
            continue

        try:
            # Basic columns
            ref_no = columns[1]
            pickup_from = columns[2]
            origin = columns[3]
            destination = columns[4]
            delivery_to = columns[5]

            # Detect transport type from ref_no
            if ref_no.startswith("A"):
                transport_type = "AIR"
            elif ref_no.startswith("S"):
                transport_type = "SEA"
            else:
                transport_type = "UNKNOWN"

            sectors: List[Dict] = []
            sector_number = 1

            # --- AIR logic: parse multiple flight legs ---
            if transport_type == "AIR":
                current_index = 6
                while current_index + 3 < len(columns):
                    flight_date = columns[current_index]
                    flight_number = columns[current_index + 1]
                    segment_from = columns[current_index + 2]
                    segment_to = columns[current_index + 3]

                    # Validate date format
                    if not re.match(r'\d{1,2}/\d{1,2}/\d{4}', flight_date):
                        break

                    sectors.append({
                        'sector': sector_number,
                        'mode': 'AIR',
                        'from': segment_from,
                        'to': segment_to,
                        'transport_number': flight_number,
                        'transport_date': flight_date
                    })
                    current_index += 4
                    sector_number += 1

            # --- SEA logic: single sea leg ---
            elif transport_type == "SEA":
                sectors.append({
                    'sector': sector_number,
                    'mode': 'SEA',
                    'from': origin,
                    'to': destination,
                    'transport_number': None,
                    'transport_date': None
                })
                sector_number += 1

            # ---- Add TRUCK pickup & delivery legs ----
            def _is_no_pickup(val):
                return val is None or str(val).strip().upper() in {"", "NO PICKUP", "N/A", "NA", "NONE"}

            if not _is_no_pickup(pickup_from):
                sectors.insert(0, {
                    'sector': 1,
                    'mode': 'TRUCK',
                    'from': pickup_from,
                    'to': f"{origin} airport" if transport_type == "AIR" else f"{origin} seaport",
                    'transport_number': None,
                    'transport_date': None
                })
                # Renumber all
                for idx, s in enumerate(sectors, start=1):
                    s['sector'] = idx

            # Always append final delivery truck leg
            last_sector_no = sectors[-1]['sector'] if sectors else 0
            sectors.append({
                'sector': last_sector_no + 1,
                'mode': 'TRUCK',
                'from': f"{destination} airport" if transport_type == "AIR" else f"{destination} seaport",
                'to': delivery_to,
                'transport_number': None,
                'transport_date': None
            })

            # --- Compute distances ---
            for s in sectors:
                s['distance_km'] = _distance_between(s.get('from'), s.get('to'))

            shipment = {
                'ref_no': ref_no,
                'pickup_from': pickup_from,
                'origin': origin,
                'destination': destination,
                'delivery_to': delivery_to,
                'sectors': sectors,
                'transport_type': transport_type
            }
            shipments.append(shipment)

        except Exception as e:
            logging.error(f"Error parsing columns {columns}: {e}")
            continue
    
    return shipments

def call_llm(prompt: str, timeout: int = 30) -> str:
    """Call the LLM with proper error handling and timeout"""
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=timeout
        )
        
        if response.status_code == 200:
            return response.json().get("response", "")
        else:
            logger.error(f"LLM API error: {response.status_code} - {response.text}")
            return ""
            
    except requests.exceptions.Timeout:
        logger.error("LLM request timed out")
        return "TIMEOUT"
    except Exception as e:
        logger.error(f"Error calling LLM: {e}")
        return ""

def generate_fallback_analysis(shipment: Dict) -> str:
    """Generate a fallback analysis when LLM times out"""
    analysis = f"Reference: {shipment['ref_no']}\n"
    analysis += "Complete Route Analysis:\n\n"
    
    # Initial pickup
    if shipment['pickup_from']:
        analysis += f"Initial Pickup (Truck): {shipment['pickup_from']} → {shipment['origin']} airport\n\n"
    else:
        analysis += f"Initial Pickup (Truck): [Location in {shipment['origin'][2:]} city] → {shipment['origin']} airport\n\n"
    
    # Flight legs
    for i, sector in enumerate(shipment['sectors']):
        analysis += f"Flight Leg {i+1} (Air): {sector['from']} → {sector['to']} via Flight {sector['transport_number']}\n\n"
    
    # Final delivery
    analysis += f"Final Delivery (Truck): {shipment['destination']} airport → {shipment['delivery_to']}\n\n"
    
    
    # Additional notes
    analysis += "Additional Notes:\n"
    analysis += "- This analysis was generated automatically due to LLM timeout\n"
    analysis += "- Please verify flight details with the carrier\n"
    
    return analysis

# === Scope 1 Emission Factors (Tank-to-Wheel) ===
# === Extended Emission Factors for Multi-modal ===
EF_TABLE = {
    "road": {  # kg CO2 per tonne-km
        "heavy_full": 0.05,
        "heavy_avg": 0.08,
        "medium": 0.20,
        "light": 0.40
    },
    "air": {   # kg CO2 per tonne-km
        "freighter_long": 0.50,
        "belly_long": 0.77,
        "freighter_short": 1.20,
        "belly_short": 0.98
    },
    "sea": {   # kg CO2 per tonne-km
        "container": 0.015,
        "bulk_carrier": 0.010,
        "tanker": 0.012,
        "general_cargo": 0.020
    }
}
SHORT_HAUL_MAX_KM = 1500  # threshold for short-haul air

def get_emission_factor(mode: str, subtype: str, distance_km: float) -> float:
    """Select EF by mode, subtype, and distance band."""
    if mode == "TRUCK":
        return EF_TABLE["road"].get(subtype, EF_TABLE["road"]["heavy_avg"])
    elif mode == "AIR":
        if distance_km is None:
            distance_km = 0
        if distance_km <= SHORT_HAUL_MAX_KM:
            return EF_TABLE["air"]["freighter_short"] if subtype == "freighter" else EF_TABLE["air"]["belly_short"]
        else:
            return EF_TABLE["air"]["freighter_long"] if subtype == "freighter" else EF_TABLE["air"]["belly_long"]
    elif mode == "SEA":
        return EF_TABLE["sea"].get(subtype, EF_TABLE["sea"]["container"])
    return 0.0

def calculate_shipment_emissions(
    shipment: Dict,
    weight_kg: float,
    road_subtype: str = "heavy_avg",
    air_subtype: str = "belly",
    sea_subtype: str = "container"
) -> Dict:
    """Calculate Scope 1 emissions for a multi-modal shipment."""
    weight_t = (weight_kg or 0) / 1000.0
    total = 0.0
    results = []
    for s in shipment.get("sectors", []):
        dist = s.get("distance_km") or 0.0
        mode = s.get("mode")
        if mode == "TRUCK":
            subtype = road_subtype
        elif mode == "AIR":
            subtype = air_subtype
        elif mode == "SEA":
            subtype = sea_subtype
        else:
            subtype = "default"
            
        ef = get_emission_factor(mode, subtype, dist)
        emissions = weight_t * dist * ef
        total += emissions
        results.append({**s, "emission_factor": ef, "emissions_kg": emissions})
    return {
        "ref_no": shipment.get("ref_no"),
        "total_emissions_kg": total,
        "by_sector": results
    }

# ----- Pydantic models for endpoints -----
class EmissionRequest(BaseModel):
    shipments: List[Dict]
    weight_kg: float
    road_subtype: Optional[str] = "heavy_avg"
    air_subtype: Optional[str] = "belly"
    sea_subtype: Optional[str] = "container"

@app.post("/extract")
def extract_info(req: PromptRequest):
    # Parse the structured logistics data
    parsed_data = parse_logistics_data(req.text)
    parsed_data = normalize_shipment_addresses(parsed_data)

    logger.info(f"Parsed {len(parsed_data)} shipments")
    
    if not parsed_data:
        return {
            "error": "No valid shipment data found",
            "parsed_shipments": [],
            "notes": "Could not parse any shipment data from input"
        }
    
    # Prepare data for LLM processing
    llm_input = "Multi-modal Logistics Shipment Data for Transport Analysis:\n\n"
    
    for i, shipment in enumerate(parsed_data):
        llm_input += f"SHIPMENT {i+1}:\n"
        llm_input += f"Reference: {shipment['ref_no']} (Primary Mode: {shipment['transport_type']})\n"
        llm_input += f"Pickup From: {shipment['pickup_from']}\n"
        llm_input += f"Origin: {shipment['origin']}, Destination: {shipment['destination']}\n"
        llm_input += f"Delivery To: {shipment['delivery_to']}\n"
        
        if shipment['sectors']:
            llm_input += "TRANSPORT SECTORS:\n"
            for sector in shipment['sectors']:
                if sector['mode'] == 'TRUCK':
                    llm_input += f"  - TRUCK: {sector['from']} → {sector['to']}\n"
                else:
                    llm_input += (
                        f"  - {sector['mode']} "
                        f"{sector.get('transport_number','') or ''}: "
                        f"{sector['from']} → {sector['to']} "
                        f"{'on ' + sector['transport_date'] if sector.get('transport_date') else ''}\n"
                    )

        
        llm_input += "\n"
    
    # Updated prompt for multi-modal analysis
    system_prompt = """You are a multi-modal logistics transport analyzer. Analyze the shipment data and provide a complete route breakdown.

INSTRUCTIONS:
1. Identify ALL transport legs including TRUCK transport for pickup/delivery, AIR transport, and SEA transport segments
2. Use the exact format specified below
3. For TRUCK transport: Identify if it's pickup from origin address to origin port/airport or delivery from destination port/airport to final address
4. For AIR transport: Note the flight numbers and airports
5. For SEA transport: Note the vessel/ship numbers and seaports
6. Dynamically infer the correct mode (AIR/SEA/TRUCK) for each segment based on the context
7. For references starting with 'A', primary mode is AIR; with 'S', primary mode is SEA

Return your response in the following EXACT format for each shipment:

Reference: [reference number]
Complete Route Analysis:

Initial Pickup (Truck): [origin location] → [origin port/airport]

[For each transport leg]
Transport Leg [number] ([Mode]): [from location] → [to location] via [transport number]

Final Delivery (Truck): [destination port/airport] → [final destination address]

Additional Notes:
- Primary transport mode: [AIR/SEA]
- [Any additional relevant information about ports, airports, or multi-modal transitions]

IMPORTANT: 
- Follow this exact format with the exact headings
- Include ALL legs including land transport for pickup and delivery
- Be specific about transport numbers and facility types (airport/port)
- Use the exact codes from the data (e.g., SGSIN, PHMNS, AUMEL, etc.)
- Dynamically identify seaports (e.g., Manila South Harbor as PHMNS) and airports
- Do not include dates in the response unless specifically asked
- Keep your response concise and to the point"""

    full_prompt = f"{system_prompt}\n\n{llm_input}"
    
    logger.info("Calling LLM for multi-modal transport analysis...")
    raw_output = call_llm(full_prompt, timeout=40)
    
    if not raw_output:
        logger.warning("LLM returned empty response")
        return {
            "error": "LLM returned empty response",
            "parsed_shipments": parsed_data
        }
    
    if raw_output == "TIMEOUT":
        logger.warning("LLM timed out, generating fallback analysis")
        fallback_analyses = []
        for shipment in parsed_data:
            fallback_analyses.append(generate_fallback_analysis(shipment))
        
        return {
            "analysis": "\n---\n".join(fallback_analyses),
            "parsed_shipments": parsed_data,
            "notes": "LLM timed out, using fallback analysis"
        }
    
    logger.info(f"LLM response received (length: {len(raw_output)})")
    
    return {
        "analysis": raw_output,
        "parsed_shipments": parsed_data
    }

@app.get("/health")
def health_check():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = response.json().get("models", [])
        model_available = any(model["name"] == OLLAMA_MODEL for model in models)
        return {
            "status": "healthy" if model_available else "model_not_loaded",
            "model": OLLAMA_MODEL,
            "available_models": [m["name"] for m in models]
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/debug-parse")
def debug_parse(req: PromptRequest):
    """Debug endpoint to see how data is parsed without LLM"""
    parsed_data = parse_logistics_data(req.text)
    
    # Also show raw parsing for debugging
    lines = req.text.strip().split('\n')
    raw_parsed = []
    for line in lines:
        if line.strip() and not any(x in line for x in ['Ref No', 'Pickup From', 'Origin']):
            parts = line.split('\t')
            raw_parsed.append({
                "raw_line": line,
                "tab_parts": parts,
                "part_count": len(parts)
            })
    
    return {
        "parsed_shipments": parsed_data,
        "raw_parsing_debug": raw_parsed,
        "input_sample": req.text[:200] + "..." if len(req.text) > 200 else req.text
    }

@app.post("/calculate")
def calculate_emissions(req: EmissionRequest):
    """Calculate emissions for multi-modal shipments"""
    results = []
    for shipment in req.shipments:
        emission_result = calculate_shipment_emissions(
            shipment=shipment,
            weight_kg=req.weight_kg,
            road_subtype=req.road_subtype,
            air_subtype=req.air_subtype,
            sea_subtype=req.sea_subtype
        )
        results.append(emission_result)
    
    return {
        "emission_results": results,
        "parameters": {
            "weight_kg": req.weight_kg,
            "road_subtype": req.road_subtype,
            "air_subtype": req.air_subtype,
            "sea_subtype": req.sea_subtype
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)