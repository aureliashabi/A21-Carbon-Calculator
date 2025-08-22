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
                        st.subheader("📊 Carbon Calculation Results")
                        result = resp.json()
                        
                        # Display the results in a user-friendly way
                        if 'data' in result and 'shipments' in result['data']:
                            for shipment in result['data']['shipments']:
                                with st.expander(f"Shipment: {shipment.get('ref_no', 'N/A')}"):
                                    st.metric("Total Emissions", f"{shipment.get('total_emissions_kg', 0):.2f} kg CO₂")
                                    st.metric("Total Distance", f"{shipment.get('total_distance_km', 0):.0f} km")
                                    
                                    if 'sectors' in shipment:
                                        for sector in shipment['sectors']:
                                            st.write(f"**Sector {sector.get('sector_number', 'N/A')}:** "
                                                    f"{sector.get('from', 'N/A')} → {sector.get('to', 'N/A')} "
                                                    f"({sector.get('distance_km', 0):.0f} km, "
                                                    f"{sector.get('emissions_kg', 0):.2f} kg CO₂)")
                        
                        st.subheader("Raw JSON Output")
                        st.json(result)
                    else:
                        st.error(f"API Error: {resp.status_code} - {resp.text}")
                except requests.exceptions.ConnectionError:
                    st.error("❌ Cannot connect to the API server. Make sure the FastAPI server is running on port 8000.")
                except requests.exceptions.Timeout:
                    st.error("⏰ Request timed out. The LLM might be taking too long to respond.")
                except Exception as e:
                    st.error(f"Unexpected error: {str(e)}")

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
""")