import streamlit as st
import requests


# ---------------------
# Initialization
# ---------------------

st.title("Alliance 21 - Carbon Calculator")
st.set_page_config(page_title="Alliance 21 - Carbon Calculator", layout="wide")

# Hide the running indicator
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

# Display the cached status
status_type, status_msg = st.session_state.api_status
if status_type == "success":
    st.sidebar.success(status_msg)
elif status_type == "warning":
    st.sidebar.warning(status_msg)
else:
    st.sidebar.error(status_msg)


# ---------------------
# Main Application
# ---------------------

col1, col2 = st.columns([3, 1])

with col1:
    user_input = st.text_area("Enter shipment details:", height=200, placeholder="Paste your logistics data here...")
    
    if st.button("🚢 Send to LLM", type="primary", use_container_width=True):
        if not user_input.strip():
            st.warning("Please enter some shipment details first.")
        else:
            with st.spinner("Processing with LLM..."):
                try:
                    resp = requests.post(
                        "http://localhost:8000/extract",
                        json={"text": user_input},
                        timeout=60
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        # Store parsed shipments in session state
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

# Display parsed shipments if they exist in session state
if "parsed_shipments" in st.session_state and st.session_state.parsed_shipments:
    parsed_shipments = st.session_state.parsed_shipments
    
    st.subheader("📦 Parsed Shipments")
    if not parsed_shipments:
        st.info("No shipments parsed. Check your input format.")
    else:
        for sh in parsed_shipments:
            with st.expander(f"Shipment: {sh.get('ref_no', 'N/A')}"):
                st.write(f"Origin: {sh.get('origin')} → Destination: {sh.get('destination')}")
                if 'sectors' in sh:
                    for s in sh['sectors']:
                        st.write(
                            f"- Sector {s.get('sector')}: {s.get('mode')} | "
                            f"{s.get('from')} → {s.get('to')} | "
                            f"{(s.get('distance_km') or 0):.1f} km"
                        )

        st.markdown("---")
        st.subheader("⚖️ Emission Inputs")

        # Inputs for calculation - these will persist in session state
        weight_kg = st.number_input("Shipment weight (kg)", min_value=1, value=400, step=10, 
                                   key="weight_input")

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
                    emissions_result = calc_resp.json()
                    # Store emissions result in session state
                    st.session_state.emissions_result = emissions_result
                    st.success("✅ Emissions calculated successfully!")
                else:
                    st.error(f"API Error: {calc_resp.status_code} - {calc_resp.text}")
            except requests.exceptions.ConnectionError:
                st.error("❌ Cannot connect to the API server on /calculate.")
            except requests.exceptions.Timeout:
                st.error("⏰ /calculate request timed out.")
            except Exception as e:
                st.error(f"Emission calc error: {e}")

# Display emissions results if they exist in session state
if "emissions_result" in st.session_state:
    emissions_result = st.session_state.emissions_result
    st.subheader("📊 Carbon Calculation Results")
    for shipment in emissions_result.get("results", []):
        with st.expander(f"Shipment: {shipment.get('ref_no', 'N/A')}"):
            st.metric("Total Emissions", f"{shipment.get('total_emissions_kg', 0):.2f} kg CO₂")
            for sector in shipment.get("by_sector", []):
                st.write(
                    f"**Sector {sector.get('sector')} ({sector.get('mode')}):** "
                    f"{sector.get('from')} → {sector.get('to')} | "
                    f"{(sector.get('distance_km') or 0):.1f} km | "
                    f"EF {sector.get('emission_factor', 0):.2f} kg/t·km | "
                    f"{sector.get('emissions_kg', 0):.2f} kg CO₂"
                )

    st.subheader("Raw JSON Output")
    st.json(emissions_result)

# Display LLM analysis if available
if "llm_analysis" in st.session_state and st.session_state.llm_analysis:
    st.subheader("🤖 LLM Analysis")
    st.text_area("Analysis Result", st.session_state.llm_analysis, height=300)

# Clear button to reset everything
if st.sidebar.button("🗑️ Clear All Data", type="secondary"):
    for key in ['parsed_shipments', 'llm_analysis', 'emissions_result', 'llm_error']:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()

# ---------------------
# Footer (stick to bottom, left-aligned)
# ---------------------
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
1. Paste logistics data in the text area
2. Click 'Send to LLM' to calculate emissions
3. Adjust emission inputs as needed
4. Click 'Calculate Scope 1 Emissions'
""")