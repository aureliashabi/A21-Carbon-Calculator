import streamlit as st
import requests
import re
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
import io
from io import BytesIO
import json
import pandas as pd
from excel_to_records import read_manifest_to_records
from insights import make_insights_from_comparison

# ---------------------
# Initialization
# ---------------------

st.set_page_config(page_title="Alliance 21 - Carbon Calculator", layout="wide")
st.title("Alliance 21 - Carbon Calculator")

# Hide default Streamlit elements
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stStatusWidget {display: none;}
    .stDeployButton {display: none;}
    header {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ---------------------
# Helper Functions
# ---------------------
def _format_date(raw_date):
    """Convert date from '2025-07-03' to '3/7/2025' format"""
    if not raw_date or str(raw_date).lower() == 'nan':
        return ""
    
    try:
        # Handle pandas Timestamp and string dates
        if hasattr(raw_date, 'strftime'):
            # It's a date object
            return raw_date.strftime("%d/%m/%Y")
        else:
            # It's a string, try to parse
            date_str = str(raw_date).split()[0]  # Take only date part
            from datetime import datetime
            if '-' in date_str:
                # Assume YYYY-MM-DD format
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                return dt.strftime("%d/%m/%Y")
            else:
                # Already in correct format or unknown
                return date_str
    except Exception as e:
        print(f"Date formatting error: {e}")
        return str(raw_date).split()[0] if raw_date else ""

def _clean_value(value):
    """Convert 'nan' to empty string and handle other pandas missing values"""
    if not value or str(value).lower() in ['nan', 'nat', 'none', 'null']:
        return ""
    return str(value)

def _compose_prefill_lines(records: list[dict]) -> str:
    lines = []
    for i, r in enumerate(records, 1):
        # Extract basic info
        ref = r.get('reference', '')
        origin = r.get('origin', '')
        destination = r.get('destination', '')
        notes = r.get('notes', '').strip()
        
        # Clean notes - remove existing quotes and clean whitespace
        notes = notes.replace('"', '').replace("'", "")
        # Replace multiple whitespace but preserve the multi-line structure for addresses
        import re
        # Only replace multiple spaces/tabs with single space, keep newlines
        notes = re.sub(r'[ \t]+', ' ', notes)
        
        # Build the base columns
        parts = [
            str(i),      # Sequence number
            ref,         # Reference
            "NO PICKUP", # Pickup From  
            origin,      # Origin
            destination, # Destination
            f'"{notes}"' # Delivery To (in quotes)
        ]
        
        # For AIR shipments, add sector information
        if ref.startswith("A"):
            segs = r.get("segments", [])
            for seg in segs:
                # Fix date format from "2025-07-03" to "3/7/2025"
                raw_date = seg.get("flight_date", "")
                formatted_date = _format_date(raw_date)
                
                # Get other fields and replace "nan" with empty string
                flight_number = _clean_value(seg.get("flight_number", ""))
                segment_from = _clean_value(seg.get("from", ""))
                segment_to = _clean_value(seg.get("to", ""))
                
                parts.extend([
                    formatted_date,
                    flight_number,
                    segment_from,
                    segment_to
                ])
        
        # Join with tabs - use empty strings instead of "nan"
        line = "\t".join(parts)
        lines.append(line)
    
    return "\n".join(lines)

# ---------------------
# Sidebar - API Status
# ---------------------
st.sidebar.header("🔧 Application Status")

if "api_status" not in st.session_state:
    with st.spinner("LLM Initializing..."):
        try:
            status_resp = requests.get("http://localhost:8000/health", timeout=10)
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                if status_data.get("status") == "healthy":
                    st.session_state.api_status = ("success", f"✅ API Server: Online  \nModel: {status_data.get('model', 'Unknown')}")
                else:
                    st.session_state.api_status = ("warning", f"⚠️ API Server: Issues detected\nStatus: {status_data.get('status', 'unknown')}")
            else:
                st.session_state.api_status = ("error", f"❌ API Server: Error {status_resp.status_code}")
        except requests.exceptions.ConnectionError:
            st.session_state.api_status = ("error", "❌ API Server: Not running\nRefresh the page by pressing F5")
        except Exception as e:
            st.session_state.api_status = ("error", f"❌ API Status Check Failed: {str(e)}\nRefresh the page by pressing F5")

status_type, status_msg = st.session_state.api_status
if status_type == "success":
    st.sidebar.success(status_msg)
elif status_type == "warning":
    st.sidebar.warning(status_msg)
else:
    st.sidebar.error(status_msg)

# ---------------------
# Session state otherwise it will bug, idk why lol
# ---------------------
if "prompt_text" not in st.session_state:
    st.session_state.prompt_text = ""         
if "last_parsed_excel" not in st.session_state:
    st.session_state.last_parsed_excel = None 
if "insights_out" not in st.session_state:
    st.session_state.insights_out = None     

# ---------------------
# Main Application
# ---------------------
col1, col2 = st.columns([3, 1])

with col1:

    # ---------------------
    # ZF Excel Upload Section START
    st.subheader("Upload Excel or Enter Shipment Detail Manually")
    excel_file = st.file_uploader("Drop your Excel here", type=["xlsx","xls"], key="manifest_file")

    # ✅ BUTTON #1: Add Excel (parse & prefill)
    if st.button("📥 Add Excel (parse & prefill)", use_container_width=True, disabled=(excel_file is None)):
        if excel_file is None:
            st.warning("Please choose an Excel file first.")
        else:
            try:
                excel_bytes = excel_file.read()
                parsed = read_manifest_to_records(excel_bytes, sheet=None)
                st.session_state.last_parsed_excel = parsed

                recs = parsed.get("records", [])
                if recs:
                    st.session_state.prompt_text = _compose_prefill_lines(recs)
                    # Don't set parsed_shipments here - let LLM handle the parsing
                    if "parsed_shipments" in st.session_state:
                        del st.session_state.parsed_shipments
                    if "emissions_result" in st.session_state:
                        del st.session_state.emissions_result
                    st.success("✅ Excel parsed and textarea prefilled.")
                else:
                    st.warning("Parsed successfully but found 0 records.")
            except Exception as e:
                st.error(f"Failed to parse Excel: {e}")

    # Show parse summary/preview if available
    if st.session_state.last_parsed_excel:
        parsed = st.session_state.last_parsed_excel
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows parsed", parsed.get("count", 0))

        recs = parsed.get("records", [])
        if recs:
            df_preview = pd.DataFrame([{
                "Ref": r.get("reference"),
                "Origin": r.get("origin"),
                "Destination": r.get("destination"),
                "Sectors": " | ".join(
                    f"{s.get('from','')}->{s.get('to','')}"
                    for s in (r.get("segments") or [])
                    if s.get('from') and s.get('to')
                ),
                "Notes": r.get("notes",""),
            } for r in recs])
            st.dataframe(df_preview, use_container_width=True, hide_index=True)

    # ---------------------
    # ZF Excel Upload Section END

    # Bind the main textarea to session state so prefill works
    user_input = st.text_area(
        "Enter shipment details:",
        height=200,
        placeholder="Paste your logistics data here...",
        key="prompt_text"  
    )
    
    if st.button("🚢 Send to LLM", type="primary", use_container_width=True):
        if not user_input.strip():
            st.warning("Please enter some shipment details first.")
        else:
            # Preprocess multi-line addresses into single lines
            preprocessed_input = re.sub(r'"\s*\n\s*', ' ', user_input)
            preprocessed_input = "\n".join([line for line in preprocessed_input.splitlines() if line.strip()])
            
            with st.spinner("Processing with LLM..."):
                try:
                    resp = requests.post(
                        "http://localhost:8000/extract",
                        json={"text": preprocessed_input},
                        timeout=60
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        st.session_state.parsed_shipments = result.get("parsed_shipments", [])
                        st.session_state.llm_analysis = result.get("analysis", "")
                        st.session_state.llm_error = None
                        st.success("✅ Data parsed successfully!")
                    else:
                        st.error(f"API Error: {resp.status_code} - {resp.text}")
                        st.session_state.llm_error = f"API Error: {resp.status_code}"
                except requests.exceptions.ConnectionError:
                    st.error("❌ Cannot connect to the API server. Make sure the FastAPI server is running on port 8000.")
                    st.session_state.llm_error = "Connection error"
                except requests.exceptions.Timeout:
                    st.error("⏰ Request timed out. The LLM might be taking too long to respond.")
                    st.session_state.llm_error = "Timeout error"
                except Exception as e:
                    st.error(f"Unexpected error: {str(e)}")
                    st.session_state.llm_error = str(e)

# ---------------------
# Insights panel with a button
# ---------------------
# st.subheader("2) Insights (paste your calculator's comparison_table)")

# comp_json_text = st.text_area(
#     "Paste JSON with comparison_table here",
#     placeholder='{"comparison_table":[{"reference":"R1","baseline_mode":"air","baseline_kg":264,"alt_scenario":"alt_road","alt_mode":"road","alt_kg":97,"delta_kg":-167,"delta_pct":-63.3}]}',
#     height=160,
#     key="comp_json"
# )

# # ✅ BUTTON #2: Generate Insights
# if st.button("✨ Generate Insights", use_container_width=True, disabled=(not comp_json_text.strip())):
#     try:
#         payload = json.loads(comp_json_text)
#         comparison_table = payload.get("comparison_table") or payload
#         out = make_insights_from_comparison(comparison_table, top_n=10, min_pct_saving=0.0)
#         st.session_state.insights_out = out
#         st.success("✅ Insights generated.")
#     except Exception as e:
#         st.session_state.insights_out = None
#         st.error(f"Could not generate insights: {e}")

# # Show insights if generated
# if st.session_state.insights_out:
#     out = st.session_state.insights_out
#     ps = out["portfolio_summary"]
#     c1, c2, c3 = st.columns(3)
#     c1.metric("Baseline total (kg CO₂e)", f"{ps['total_baseline_kg']:,.3f}")
#     c2.metric("Best-case total (kg CO₂e)", f"{ps['total_bestcase_kg']:,.3f}")
#     c3.metric("Portfolio Δ", f"{ps['portfolio_delta_kg']:,.3f}", f"{ps['portfolio_delta_pct']:.1f}%")

#     st.markdown("Insights")
#     for line in out["insights_text"]:
#         st.write("•", line)

#     tops = out["top_opportunities"]
#     if isinstance(tops, pd.DataFrame) and not tops.empty:
#         st.markdown("Top opportunities")
#         st.dataframe(tops, use_container_width=True, hide_index=True)

# ---------------------
# Display parsed shipments
# ---------------------
if "parsed_shipments" in st.session_state and st.session_state.parsed_shipments:
    parsed_shipments = st.session_state.parsed_shipments
    
    st.subheader("📦 Parsed Shipments")
    for sh in parsed_shipments:
        with st.expander(f"Shipment: {sh.get('ref_no', 'N/A')}"):
            st.write(f"Origin: {sh.get('origin')} → Destination: {sh.get('destination')}")
            for s in sh.get("sectors", []):
                extra = ""
                if s.get("mode") in ("AIR", "SEA"):
                    if s.get("transport_number"):
                        extra += f" {s['transport_number']}"
                    if s.get("transport_date"):
                        extra += f" on {s['transport_date']}"
                st.write(
                    f"- Sector {s.get('sector')}: {s.get('mode')}{extra} | "
                    f"{s.get('from')} → {s.get('to')} | "
                    f"{(s.get('distance_km') or 0):.1f} km"
                )

    st.markdown("---")
    st.subheader("⚖️ Emission Inputs")

    weight_kg = st.number_input("Shipment weight (kg)", min_value=1, value=400, step=10, key="weight_input")

    colA, colB = st.columns(2)
    with colA:
        road_subtype = st.selectbox(
            "Truck type / load",
            options=["heavy_avg", "heavy_full", "medium", "light"],
            index=0,
            help="Used for TRUCK legs",
            key="road_type_input"
        )
    with colB:
        air_subtype = st.selectbox(
            "Air cargo type",
            options=["belly", "freighter"],
            index=0,
            help="Used for AIR legs. Short vs long-haul is auto-detected by distance.",
            key="air_type_input"
        )

    if st.button("⚡ Calculate Scope 1 Emissions", use_container_width=True, key="calc_button"):
        try:
            calc_resp = requests.post(
                "http://localhost:8000/calculate",
                json={
                    "shipments": parsed_shipments,
                    "weight_kg": float(weight_kg),
                    "road_subtype": road_subtype,
                    "air_subtype": air_subtype
                },
                timeout=60
            )
            if calc_resp.status_code == 200:
                st.session_state.emissions_result = calc_resp.json()
                st.success("✅ Emissions calculated successfully!")
            else:
                st.error(f"API Error: {calc_resp.status_code} - {calc_resp.text}")
        except requests.exceptions.ConnectionError:
            st.error("❌ Cannot connect to the API server on /calculate.")
        except requests.exceptions.Timeout:
            st.error("⏰ /calculate request timed out.")
        except Exception as e:
            st.error(f"Emission calc error: {e}")

def create_emission_pdf(emissions_result):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36, rightMargin=36, topMargin=48, bottomMargin=48  # comfortable margins
    )
    styles = getSampleStyleSheet()
    normal = styles['Normal']
    title = styles['Title']
    h2 = styles['Heading2']

    elements = []

    ## Darius Edit

    image_url = "https://alex.world/wp-content/uploads/2002/10/A21-1.png"
    try:
        response = requests.get(image_url)
        if response.status_code == 200:
            img_data = BytesIO(response.content)
            img = Image(img_data, width=100, height=60)  # adjust size as needed
            elements.append(img)
            elements.append(Spacer(1, 12))
    except Exception as e:
        st.error(f"Could not load image from {image_url}: {e}")
    






    elements.append(Paragraph("Alliance 21 Carbon Emission Calculation", title))
    elements.append(Spacer(1, 16))

    for shipment in emissions_result.get("emission_results", []):
        ref_no = shipment.get("ref_no", "N/A")
        total = shipment.get("total_emissions_kg", 0.0)

        elements.append(Paragraph(f"Shipment: {ref_no}", h2))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Total Scope 1 Emission: {total:.2f} kg CO2", normal))
        elements.append(Spacer(1, 12))

        # Table data (use Paragraph for wrapped text)
        data = [["Sector", "Type", "Location", "Distance", "Rate", "Emission"]]
        for s in shipment.get("by_sector", []):
            loc_para = Paragraph(f"{s.get('from','')} → {s.get('to','')}", normal)
            data.append([
                s.get("sector", ""),
                s.get("mode", ""),
                loc_para,
                f"{(s.get('distance_km') or 0):.1f} km",
                f"{s.get('emission_factor', 0):.2f} kg/t·km",
                f"{s.get('emissions_kg', 0):.2f} kg CO2"
            ])

        # Wider Location column; generous paddings so text doesn’t kiss the grid
        table = Table(
            data,
            colWidths=[45, 60, 250, 65, 70, 70]
        )
        table.setStyle(TableStyle([
            # header
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN',      (0, 0), (-1, 0), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),

            # body
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
            ('ALIGN',  (0, 1), (1, -1), 'CENTER'),   # Sector, Type center
            ('ALIGN',  (3, 1), (-1, -1), 'CENTER'),  # numeric cols center
            ('ALIGN',  (2, 1), (2, -1), 'LEFT'),     # Location left
            ('LEFTPADDING',  (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING',   (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 24))  # space between shipments

    # Set PDF metadata to avoid "(anonymous)"
    def _set_meta(canvas, _doc):
        canvas.setAuthor("Alliance 21")
        canvas.setTitle("Alliance 21 Carbon Emission Calculation")
        canvas.setSubject("Carbon Emission Report")

    doc.build(elements, onFirstPage=_set_meta, onLaterPages=_set_meta)

    pdf = buffer.getvalue()
    buffer.close()
    return pdf


# ---------------------
# Display Emissions Results
# ---------------------
if "emissions_result" in st.session_state:
    emissions_result = st.session_state.emissions_result
    st.subheader("📊 Carbon Calculation Results")
    for shipment in emissions_result.get("emission_results", []):
        with st.expander(f"Shipment: {shipment.get('ref_no', 'N/A')}"):
            st.metric("Total Emissions", f"{shipment.get('total_emissions_kg', 0):.2f} kg CO₂")
            for sector in shipment.get("by_sector", []):
                extra = ""
                if sector.get("mode") in ("AIR", "SEA"):
                    if sector.get("transport_number"):
                        extra += f" {sector['transport_number']}"
                    if sector.get("transport_date"):
                        extra += f" on {sector['transport_date']}"
                st.write(
                    f"**Sector {sector.get('sector')} ({sector.get('mode')}{extra}):** "
                    f"{sector.get('from')} → {sector.get('to')} | "
                    f"{(sector.get('distance_km') or 0):.1f} km | "
                    f"EF {sector.get('emission_factor', 0):.2f} kg/t·km | "
                    f"{sector.get('emissions_kg', 0):.2f} kg CO₂"
                )

    # 📥 PDF download button (added here)
    pdf_bytes = create_emission_pdf(emissions_result)
    st.download_button(
        label="📥 Download Emission Report (PDF)",
        data=pdf_bytes,
        file_name="emission_report.pdf",
        mime="application/pdf"
    )

    #st.subheader("Raw JSON Output")
    #st.json(emissions_result)

    # ---------------------
    # Insights panel with a button
    # ---------------------
    st.subheader("Insights (paste your calculator's comparison_table)")

    comp_json_text = st.text_area(
        "Paste JSON with comparison_table here",
        placeholder='{"comparison_table":[{"reference":"R1","baseline_mode":"air","baseline_kg":264,"alt_scenario":"alt_road","alt_mode":"road","alt_kg":97,"delta_kg":-167,"delta_pct":-63.3}]}',
        height=160,
        key="comp_json"
    )

    # Generate Insights
    if st.button("✨ Generate Insights", use_container_width=True, disabled=(not comp_json_text.strip())):
        try:
            payload = json.loads(comp_json_text)
            comparison_table = payload.get("comparison_table") or payload
            out = make_insights_from_comparison(comparison_table, top_n=10, min_pct_saving=0.0)
            st.session_state.insights_out = out
            st.success("✅ Insights generated.")
        except Exception as e:
            st.session_state.insights_out = None
            st.error(f"Could not generate insights: {e}")

    # Show insights if generated
    if st.session_state.insights_out:
        out = st.session_state.insights_out
        ps = out["portfolio_summary"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Baseline total (kg CO₂e)", f"{ps['total_baseline_kg']:,.3f}")
        c2.metric("Best-case total (kg CO₂e)", f"{ps['total_bestcase_kg']:,.3f}")
        c3.metric("Portfolio Δ", f"{ps['portfolio_delta_kg']:,.3f}", f"{ps['portfolio_delta_pct']:.1f}%")

        st.markdown("Insights")
        for line in out["insights_text"]:
            st.write("•", line)

        tops = out["top_opportunities"]
        if isinstance(tops, pd.DataFrame) and not tops.empty:
            st.markdown("Top opportunities")
            st.dataframe(tops, use_container_width=True, hide_index=True)



# # ---------------------
# # LLM Analysis
# # ---------------------
# if "llm_analysis" in st.session_state and st.session_state.llm_analysis:
#     st.subheader("🤖 LLM Analysis")
#     st.text_area("Analysis Result", st.session_state.llm_analysis, height=300)

# ---------------------
# Sidebar - Clear Data & Footer
# ---------------------
if st.sidebar.button("🗑️ Clear All Data", type="secondary"):
    for key in [
        'parsed_shipments', 'llm_analysis', 'emissions_result', 'llm_error',
        'prompt_text', 'last_parsed_excel', 'insights_out', 'comp_json'
    ]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()

footer_html = """
<div style="
    position: fixed;
    bottom: 0;
    width: 300px;
    padding: 10px;
    background-color: transparent;
    font-size: 0.8em;
    color: gray;
    text-align: left;
">
Alliance 21 Carbon Calculator v1.0<br>
Built by: Adeline, Darius, Sian Yin, Zi Feng
</div>
"""
st.sidebar.markdown(footer_html, unsafe_allow_html=True)

st.sidebar.info("""
**Instructions:**
1. Click **Add Excel (parse & prefill)** to ingest your manifest and prefill the textarea
2. OR paste logistics data in the text area
3. Click 'Send to LLM' to calculate emissions
4. Adjust emission inputs as needed and click 'Calculate Scope 1 Emissions'
5. Paste calculator 'comparison_table' JSON and click **Generate Insights**
""")