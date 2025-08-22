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

# Minimal IATA coordinate fallback (extend as needed)
IATA_COORDS = {
    "ZRH": (47.458056, 8.548056),   # Zurich
    "JFK": (40.641311, -73.778139),  # New York JFK
    "SIN": (1.364420, 103.991531),   # Singapore Changi
    "DXB": (25.253174, 55.365673),   # Dubai International
    "ICN": (37.460190, 126.440696),  # Incheon
}

def _iata_from_unlocode(code: str):
    if not code:
        return None
    code = code.strip().upper().replace(" ", "")
    if len(code) == 5 and code[:2].isalpha() and code[2:].isalnum():
        return code[2:]
    return None

@lru_cache(maxsize=512)
def _geocode_google(query: str):
    """Use Google Geocoding API as fallback"""
    api_key = os.environ.get('GOOGLE_GEOCODING_API_KEY')
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
    # Best-effort coordinate resolver with UN/LOCODE/IATA and postal-code fallback.
    if not loc_str:
        return None

    s = str(loc_str).strip()

    # 1) Try UN/LOCODE or IATA directly
    iata = _iata_from_unlocode(s)
    if iata:
        if iata in IATA_COORDS:
            return IATA_COORDS[iata]
        hit = _geocode_query(f"{iata} airport")
        if hit:
            return hit

    # 2) Handle strings like 'SGSIN airport' or 'USJFK airport'
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
        # else, try any 3-letter code just before 'airport'
        m3 = re.search(r'([A-Z]{3})\s+airport\b', s.upper())
        if m3:
            iata3 = m3.group(1)
            if iata3 in IATA_COORDS:
                return IATA_COORDS[iata3]
            hit = _geocode_query(f"{iata3} airport")
            if hit:
                return hit

    # 3) Try raw geocoding of the entire string
    hit = _geocode_query(s)
    if hit:
        return hit

    # 4) Postal code fallback (SG 6 digits; US 5 or ZIP+4)
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
    if not ca or not cb or geodesic is None:
        return None
    try:
        return round(geodesic(ca, cb).kilometers, 1)
    except Exception:
        return None
# --- End geo helpers ---

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
    """Parse the tab-separated logistics data format with multiple sectors"""
    # First, clean up the text by handling multi-line addresses
    lines = text.strip().split('\n')
    cleaned_lines = []
    current_line = ""
    
    for line in lines:
        # Skip header lines
        if any(x in line for x in ['Ref No', 'Pickup From', 'Origin', '1st sector']):
            continue
            
        # If line starts with a digit (new record), add the previous line and start a new one
        if re.match(r'^\d+\t', line) and current_line:
            cleaned_lines.append(current_line)
            current_line = line
        else:
            # Continue building the current line (for multi-line addresses)
            current_line += " " + line.strip()
    
    if current_line:
        cleaned_lines.append(current_line)
    
    shipments = []
    
    for line in cleaned_lines:
        # Use CSV reader to handle tab-separated values with quotes
        try:
            csv_reader = csv.reader(StringIO(line), delimiter='\t', quotechar='"')
            columns = next(csv_reader)
            columns = [col.strip() for col in columns]  # keep empties to preserve positions
        except Exception as e:
            logging.error(f"CSV parsing failed for line: {line}, error: {e}")
            continue

        # Need at least: seq_no, ref_no, pickup, origin, dest, delivery
        if len(columns) < 6:
            logging.warning(f"Skipping line with insufficient columns: {columns}")
            continue
            
        try:
            # Columns
            ref_no = columns[1]
            pickup_from = columns[2]
            origin = columns[3]
            destination = columns[4]
            delivery_to = columns[5]
            
            # Extract AIR flight segments (date, flight no, from, to) repeating
            sectors: List[Dict] = []
            sector_number = 1
            
            current_index = 6
            while current_index + 3 < len(columns):
                flight_date = columns[current_index]
                flight_number = columns[current_index + 1]
                segment_from = columns[current_index + 2]
                segment_to = columns[current_index + 3]
                
                # Validate flight date like M/D/YYYY or MM/DD/YYYY
                if not re.match(r'\d{1,2}/\d{1,2}/\d{4}', flight_date):
                    break
                    
                sectors.append({
                    'sector': sector_number,
                    'flight_date': flight_date,
                    'flight_number': flight_number,
                    'from': segment_from,
                    'to': segment_to,
                    'mode': 'AIR'
                })
                
                current_index += 4
                sector_number += 1

            # ---- Add TRUCK legs as sectors (pickup and final delivery) ----
            def _is_no_pickup(val):
                return val is None or str(val).strip().upper() in {"", "NO PICKUP", "N/A", "NA", "NONE"}

            if not _is_no_pickup(pickup_from):
                # Prepend pickup as sector 1 and shift numbering
                sectors.insert(0, {
                    'sector': 1,
                    'flight_date': None,
                    'flight_number': None,
                    'from': pickup_from,
                    'to': f"{origin} airport",
                    'mode': 'TRUCK'
                })
                for idx in range(1, len(sectors)):
                    sectors[idx]['sector'] = idx + 1
            else:
                # Ensure sequential numbering for AIR-only start
                for idx in range(len(sectors)):
                    sectors[idx]['sector'] = idx + 1

            # Always append final delivery as last TRUCK sector
            last_sector_no = sectors[-1]['sector'] if sectors else 0
            sectors.append({
                'sector': last_sector_no + 1,
                'flight_date': None,
                'flight_number': None,
                'from': f"{destination} airport",
                'to': delivery_to,
                'mode': 'TRUCK'
            })
            # ----------------------------------------------------------------

            
            # --- compute distances for each sector ---
            for s in sectors:
                s['distance_km'] = _distance_between(s.get('from'), s.get('to'))

            shipment = {
                'ref_no': ref_no,
                'pickup_from': pickup_from,
                'origin': origin,
                'destination': destination,
                'delivery_to': delivery_to,
                'sectors': sectors,
                'transport_type': 'AIR' if ref_no.startswith('A') else 'SEA' if ref_no.startswith('S') else 'UNKNOWN'
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
        analysis += f"Flight Leg {i+1} (Air): {sector['from']} → {sector['to']} via Flight {sector['flight_number']}\n\n"
    
    # Final delivery
    analysis += f"Final Delivery (Truck): {shipment['destination']} airport → {shipment['delivery_to']}\n\n"
    
    # Connecting airports
    if len(shipment['sectors']) > 1:
        connecting_airports = [sector['from'] for sector in shipment['sectors'][1:]]
        analysis += f"Connecting Airports: {', '.join(connecting_airports)}\n\n"
    else:
        analysis += "Connecting Airports: None\n\n"
    
    # Additional notes
    analysis += "Additional Notes:\n"
    analysis += "- This analysis was generated automatically due to LLM timeout\n"
    analysis += "- Please verify flight details with the carrier\n"
    
    return analysis

@app.post("/extract")
def extract_info(req: PromptRequest):
    # Parse the structured logistics data
    parsed_data = parse_logistics_data(req.text)
    logger.info(f"Parsed {len(parsed_data)} shipments")
    
    if not parsed_data:
        return {
            "error": "No valid shipment data found",
            "parsed_shipments": [],
            "notes": "Could not parse any shipment data from input"
        }
    
    # Prepare data for LLM processing
    llm_input = "Logistics Shipment Data for Transport Analysis:\n\n"
    
    for i, shipment in enumerate(parsed_data):
        llm_input += f"SHIPMENT {i+1}:\n"
        llm_input += f"Reference: {shipment['ref_no']} (Type: {shipment['transport_type']})\n"
        llm_input += f"Pickup From: {shipment['pickup_from']}\n"
        llm_input += f"Origin Airport: {shipment['origin']}, Destination Airport: {shipment['destination']}\n"
        llm_input += f"Delivery To: {shipment['delivery_to']}\n"
        
        if shipment['sectors']:
            llm_input += "AIR TRANSPORT SECTORS:\n"
            for sector in shipment['sectors']:
                llm_input += f"  - Flight {sector['flight_number']}: {sector['from']} -> {sector['to']} on {sector['flight_date']}\n"
        else:
            llm_input += "  No air transport sectors available\n"
        
        llm_input += "\n"
    
    # Updated prompt for the specific format you want
    system_prompt = """You are a logistics transport analyzer. Analyze the shipment data and provide a complete route breakdown.

INSTRUCTIONS:
1. Identify ALL transport legs including LAND transport for pickup/delivery and AIR transport segments
2. Use the exact format specified below
3. For LAND transport: Identify if it's pickup from origin address to origin airport or delivery from destination airport to final address
4. For AIR transport: Note the flight numbers and airports
5. Identify any connecting airports where cargo changes planes

Return your response in the following EXACT format for each shipment:

Reference: [reference number]
Complete Route Analysis:

Initial Pickup (Truck): [origin location] → [origin airport code] airport

[For each flight leg]
Flight Leg [number] (Air): [from airport code] → [to airport code] via Flight [flight number]

Final Delivery (Truck): [destination airport code] airport → [final destination address]

Connecting Airports: [List any connecting airports, or "None" if direct]

Additional Notes:
[Any additional relevant information]

IMPORTANT: 
- Follow this exact format with the exact headings
- Include ALL legs including land transport for pickup and delivery
- Be specific about flight numbers and airport codes
- Clearly identify any connecting airports
- Use the exact airport codes from the data (e.g., SGSIN, KRICN, etc.)
- Do not include dates in the response unless specifically asked
- Keep your response concise and to the point"""

    full_prompt = f"{system_prompt}\n\n{llm_input}"
    
    logger.info("Calling LLM for transport analysis...")
    raw_output = call_llm(full_prompt, timeout=20)  # Reduced timeout
    
    if not raw_output:
        logger.warning("LLM returned empty response")
        return {
            "error": "LLM returned empty response",
            "parsed_shipments": parsed_data
        }
    
    if raw_output == "TIMEOUT":
        logger.warning("LLM timed out, generating fallback analysis")
        # Generate fallback analysis for each shipment
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)