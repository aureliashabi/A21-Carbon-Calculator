import streamlit as st
import requests
import subprocess
import os
import sys
import time

st.title("Local Carbon Planner")

# Function to restart the application
def restart_application():
    try:
        # Try standard Desktop first
        project_dir_1 = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'A21 CarbonCalc')
        project_dir_2 = os.path.join(os.environ['USERPROFILE'], 'OneDrive', 'Desktop', 'A21 CarbonCalc')
        
        run_cmd_path = None
        
        # Check which path exists
        if os.path.exists(project_dir_1):
            run_cmd_path = os.path.join(project_dir_1, 'run.cmd')
        elif os.path.exists(project_dir_2):
            run_cmd_path = os.path.join(project_dir_2, 'run.cmd')
        else:
            st.error("Could not find A21 CarbonCalc directory on Desktop or OneDrive Desktop")
            return
        
        # Check if run.cmd exists
        if os.path.exists(run_cmd_path):
            # Change to the project directory
            os.chdir(os.path.dirname(run_cmd_path))
            
            # Run the run.cmd file
            subprocess.Popen(['run.cmd'], shell=True)
            
            st.success("🔄 Application restart initiated!")
            st.info("New windows will open for the API server and application.")
            st.info("You can close this browser tab once the new application loads.")
            
            # Add a small delay to show the message
            time.sleep(2)
            
        else:
            st.error(f"run.cmd file not found at: {run_cmd_path}")
            
    except Exception as e:
        st.error(f"Error restarting application: {str(e)}")

# Main application content
user_input = st.text_area("Enter shipment details:", height=200, placeholder="Paste your logistics data here...")

col1, col2 = st.columns([3, 1])

with col1:
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
                    st.info("Try clicking the 'Restart Application' button to start the server.")
                except requests.exceptions.Timeout:
                    st.error("⏰ Request timed out. The LLM might be taking too long to respond.")
                except Exception as e:
                    st.error(f"Unexpected error: {str(e)}")

with col2:
    if st.button("🔄 Restart App", type="secondary", use_container_width=True, help="Restart both API server and application"):
        restart_application()

# Display status information
st.sidebar.header("🔧 Application Status")
st.sidebar.info("""
**Instructions:**
1. Paste logistics data in the text area
2. Click 'Send to LLM' to calculate emissions
3. Use 'Restart App' if services need restarting
""")

# Check API status
try:
    status_resp = requests.get("http://localhost:8000/health", timeout=5)
    if status_resp.status_code == 200:
        status_data = status_resp.json()
        if status_data.get("status") == "healthy":
            st.sidebar.success("✅ API Server: Online")
            st.sidebar.info(f"Model: {status_data.get('model', 'Unknown')}")
            
            # Show available models if present
            if 'available_models' in status_data:
                st.sidebar.write("Available models:")
                for model in status_data['available_models']:
                    st.sidebar.write(f"• {model}")
        else:
            st.sidebar.warning("⚠️ API Server: Issues detected")
            st.sidebar.write(f"Status: {status_data.get('status', 'unknown')}")
    else:
        st.sidebar.error(f"❌ API Server: Error {status_resp.status_code}")
except requests.exceptions.ConnectionError:
    st.sidebar.error("❌ API Server: Not running")
    st.sidebar.info("Click 'Restart App' to start the server")
except Exception as e:
    st.sidebar.error(f"❌ API Status Check Failed: {str(e)}")

# Debug section
with st.sidebar.expander("🔍 Debug Tools"):
    st.write("**Connection Test:**")
    if st.button("Test API Connection"):
        try:
            test_resp = requests.get("http://localhost:8000/health", timeout=5)
            st.write(f"Status Code: {test_resp.status_code}")
            st.json(test_resp.json())
        except Exception as e:
            st.error(f"Connection test failed: {str(e)}")
    
    st.write("**File Check:**")
    if st.button("Check run.cmd locations"):
        paths_to_check = [
            os.path.join(os.environ['USERPROFILE'], 'Desktop', 'A21 CarbonCalc', 'run.cmd'),
            os.path.join(os.environ['USERPROFILE'], 'OneDrive', 'Desktop', 'A21 CarbonCalc', 'run.cmd')
        ]
        
        for path in paths_to_check:
            exists = os.path.exists(path)
            status = "✅ Found" if exists else "❌ Not found"
            st.write(f"{status}: {path}")

# Sample data for testing
with st.sidebar.expander("📋 Sample Data"):
    sample_data = """1	AESG250700063944		SGSIN	KRICN	"CENTUM SKYBIZ, 97 CENTUM JUNGANG-RO, HAEUNDAE-GU,
BUSAN
48058 KOREA, REPUBLIC OF"	3/7/2025	SQ600	SGSIN	KRICN"""
    
    if st.button("Load Sample Data"):
        st.session_state.sample_loaded = True
        st.rerun()

if hasattr(st.session_state, 'sample_loaded') and st.session_state.sample_loaded:
    user_input = sample_data
    st.session_state.sample_loaded = False

# Footer
st.sidebar.markdown("---")
st.sidebar.caption("Carbon Planner v1.0 • Built with Streamlit & FastAPI")




# import streamlit as st
# import requests

# st.title("Local Carbon Planner")

# user_input = st.text_area("Enter shipment details:", height=150)

# if st.button("Send to LLM"):
#     with st.spinner("Waiting for LLM..."):
#         resp = requests.post(
#             "http://localhost:8000/extract",
#             json={"text": user_input}
#         )
#         if resp.status_code == 200:
#             st.subheader("Model Output")
#             st.json(resp.json())
#         else:
#             st.error(f"Error: {resp.status_code}")
