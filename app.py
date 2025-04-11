from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import time
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
ors_key = "5b3ce3597851110001cf62484c21171bb42b5156136eb3b6c86735ceb936e6d856184e15bb72367f"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    print(f"Geocoding: {city}, {state} → {location}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def calculate_distance(a, b):
    return geodesic(a, b).miles

def get_openroute_path(start, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-hgv"
    headers = {
        "Authorization": ors_key,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [[start[1], start[0]], [end[1], end[0]]]
    }
    print("Requesting ORS with:", body)
    response = requests.post(url, json=body, headers=headers)
    print("Status:", response.status_code)
    print("Response:", response.text)
    if response.status_code == 200:
        return response.json()["features"][0]["geometry"]["coordinates"]
    else:
        print("ORS Error:", response.text)
        return None

# remaining endpoints to be appended after this point



from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import folium
from folium.plugins import BeautifyIcon
import time

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
graphhopper_key = "23af8292-46eb-4275-900a-99c729d1952c"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def calculate_distance(a, b):
    return geodesic(a, b).miles

def load_ev_stations():
    ev_stations = []
    for i in range(1, 8):
        path = f"ev_stations/{i}.xlsx"
        if os.path.exists(path):
            df = pd.read_excel(path)
            for _, row in df.iterrows():
                lat = row.get("Latitude") or row.get("latitude")
                lon = row.get("Longitude") or row.get("longitude")
                if pd.notna(lat) and pd.notna(lon):
                    ev_stations.append((lat, lon))
    return ev_stations

def get_graphhopper_route(start, end):
    url = "https://graphhopper.com/api/1/route"
    params = {
        "point": [f"{start[0]},{start[1]}", f"{end[0]},{end[1]}"],
        "vehicle": "truck",
        "locale": "en",
        "points_encoded": "false",
        "key": graphhopper_key
    }
    response = requests.get(url, params=params)
    data = response.json()
    if "paths" in data:
        return data["paths"][0]["points"]["coordinates"]
    else:
        return None

def calculate_total_route_mileage(route_coords):
    total_miles = 0
    for i in range(len(route_coords) - 1):
        a = (route_coords[i][1], route_coords[i][0])
        b = (route_coords[i + 1][1], route_coords[i + 1][0])
        total_miles += calculate_distance(a, b)
    return total_miles

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/batch-result", methods=["POST"])
def batch_result():
    try:
        file = request.files["excel"]
        df = pd.read_excel(file)
        results = []
        all_stations = load_ev_stations()

        for index, row in df.iterrows():
            try:
                start_coord = geocode_city_state(row["Start City"], row["Start State"])
                end_coord = geocode_city_state(row["Destination City"], row["Destination State"])
                mpg = float(row.get("MPG (Will Default To 9)", 9))
                trips = int(row["Annual Trips (Minimum 1)"])

                diesel_route = get_graphhopper_route(start_coord, end_coord)
                if not diesel_route:
                    raise Exception("Diesel route not found")
                diesel_miles = calculate_total_route_mileage(diesel_route)
                diesel_annual_miles = diesel_miles * trips
                diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_annual_miles) + diesel_miles * (16600 / 750000)
                diesel_emissions = (diesel_annual_miles * 1.617) / 1000

                used_stations = []
                ev_route_coords = []
                def nearest_station(current, end, max_leg=225):
                    candidates = [s for s in all_stations if calculate_distance(current, s) <= max_leg and s not in used_stations]
                    return min(candidates, key=lambda s: calculate_distance(s, end)) if candidates else None

                current = start_coord
                ev_possible = True
                while calculate_distance(current, end_coord) > 225:
                    next_stop = nearest_station(current, end_coord)
                    if not next_stop:
                        ev_possible = False
                        break
                    segment = get_graphhopper_route(current, next_stop)
                    if not segment:
                        ev_possible = False
                        break
                    ev_route_coords.extend(segment)
                    used_stations.append(next_stop)
                    current = next_stop

                if ev_possible:
                    final_segment = get_graphhopper_route(current, end_coord)
                    if not final_segment:
                        ev_possible = False
                    else:
                        ev_route_coords.extend(final_segment)

                if ev_possible:
                    ev_miles = calculate_total_route_mileage(ev_route_coords)
                    ev_annual_miles = ev_miles * trips
                    ev_cost = (ev_annual_miles / 20.39) * 2.208 + ev_miles * (10500 / ev_annual_miles) + ev_annual_miles * (250000 / 750000)
                    ev_emissions = (ev_annual_miles * 0.2102) / 1000
                else:
                    ev_miles = ev_annual_miles = ev_cost = ev_emissions = "N/A"

                results.append({
                    "Start City": row["Start City"],
                    "Start State": row["Start State"],
                    "Destination City": row["Destination City"],
                    "Destination State": row["Destination State"],
                    "Diesel Mileage (1 Trip)": round(diesel_miles, 1),
                    "Annual Trips": trips,
                    "Diesel Total Mileage": round(diesel_annual_miles, 1),
                    "Diesel Total Cost": round(diesel_cost, 2),
                    "Diesel Total Emissions": round(diesel_emissions, 2),
                    "EV Possible?": "Yes" if ev_possible else "No",
                    "EV Mileage (1 Trip)": round(ev_miles, 1) if isinstance(ev_miles, float) else "N/A",
                    "EV Total Mileage": round(ev_annual_miles, 1) if isinstance(ev_annual_miles, float) else "N/A",
                    "EV Total Cost": round(ev_cost, 2) if isinstance(ev_cost, float) else "N/A",
                    "EV Total Emissions": round(ev_emissions, 2) if isinstance(ev_emissions, float) else "N/A"
                })

                time.sleep(1)
            except Exception as e:
                print(f"[Row {index+2}] Error: {e}")
                continue

        result_df = pd.DataFrame(results)
        result_df.to_excel("static/route_results_batch.xlsx", index=False)
        return render_template("batch_result.html", count=len(results))
    except Exception as e:
        return f"<h3>Error processing batch file: {str(e)}</h3>"

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)


@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
