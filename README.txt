#### ALLIANCE 21 CARBON CALCULATOR v1.0 ####

### NOTES ###
This is not an on-prem offline solution. Internet access is required due to utilizing Google's Geocoding API. However, fallbacks can be put in place but has yet to be fully developed.

There are two parts to this application, the app.py and server.py. 
- The app.py utilises Streamlit for front-end display on localhost
- The server.py utilises the OLLAMA llama3.1:8b-instruct-q8_0 model that infers shipping data and breaks it down into json format before sending it to a carbon calculator where the carbon emission will be calculated and returned.
- The run.bat is a bat file that runs both the above script, however it is hard coded to look for "A21 CarbonCalc" folder on DESKTOP only. 

#### Environment Setup ####

(Ollama & LLM)

- Install Ollama from https://ollama.com/download

- Open CMD and run the following commands:
ollama pull llama3.1:8b-instruct-q8_0
ollama run llama3.1:8b-instruct-q8_0

- Test that the LLM works and is installed

(Dependencies)

- pip install streamlit requests geopy pandas fastapi uvicorn googlemaps reportlab openpyxl python-multipart
- python -m pip install streamlit requests geopy pandas fastapi uvicorn googlemaps reportlab  openpyxl python-multipart

(Cloning from Github)

- git clone https://github.com/aureliashabi/A21-Carbon-Calculator.git

Alternatively, download as ZIP folder and extract to desktop.

** IMPORTANT ** 
Ensure that the folder is saved in Desktop and RENAMED to "A21 CarbonCalc", this is a requirement by run.bat and WIP to be more dynamic.


(Google Geolocation API)

- https://console.cloud.google.com/
- Create a project and enable the Geocoding API and Distance Matrix API
- Create an API key
- CMD and run
setx GOOGLE_GEOCODING_API_KEY "your_api_key_here"

ALTERNATIVELY, for development purposes, one can set : api_key = "your_api_key_here" in the server.py script.

#### Sample data for LLM as follows ####

2	AESG250700063946		SGSIN	AEDXB	"AL WASL WAREHOUSE AL-QUOZ INDU. AREA -3
DUBAI 124359 UNITED ARAB EMIRATES"	2/7/2025	EK353	SGSIN	AEDXB								

7	AESG250700063953	SINGAPORE 408705	SGSIN	USJFK	"44 WALL STREET,
NEW YORK NY 10005 UNITED STATES"	1/7/2025	LX177	SGSIN	CHZRH	2/7/2025	LX016	CHZRH	USJFK				

23	AESG250700063981		SGSIN	USJFK	"
44 WALL STREET,
NEW YORK NY 10005 UNITED STATES"	2/7/2025	LX177	SGSIN	CHZRH	3/7/2025	LX016	CHZRH	USJFK				

29	AISG250700063987		AUMEL	SGSIN	"9 AIRLINE ROAD #01-15
CARGO AGENTS BUILDING D
SINGAPORE 819827"	3/7/2025	SQ0218	AUMEL	SGSIN								



