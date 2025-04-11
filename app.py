from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import csv
import folium
import time
from folium.plugins import BeautifyIcon
from openpyxl import Workbook

app = Flask(__name__, template_folder="templates", static_folder="static")

# API Keys
google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
graphhopper_key = "23af8292-46eb-4275-900a-99c729d1952c"

geolocator = GoogleV3(api_key=google_api_key, timeout=10)

CACHE_FILE = "geocache.csv"
geocode_cache = {}

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) == 4:
                city, state, lat, lon = row
                geocode_cache[(city.lower(), state.lower())] = (float(lat), float(lon))

def save_geocode_to_cache(city, state, coord):
    with open(CACHE_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([city, state, coord[0], coord[1]])

def geocode_city_state(city, state, row_num=1):
    key = (city.strip().lower(), state.strip().lower())
    if key in geocode_cache:
        return geocode_cache[key]
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    coord = (location.latitude, location.longitude)
    geocode_cache[key] = coord
    save_geocode_to_cache(city, state, coord)
    return coord

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
        raise Exception(f"Routing failed: {data}")

def generate_map(route_coords, start_coord, end_coord, file_path, ev_used_stations=None):
    m = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
    folium.Marker(start_coord, tooltip="Start", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip="End", icon=BeautifyIcon(icon_shape='marker')).add_to(m)

    folium.PolyLine(locations=[(lat, lon) for lon, lat in route_coords],
                    color="#002f6c", weight=5).add_to(m)

    if ev_used_stations is not None:
        all_stations = load_ev_stations()
        for lat, lon in all_stations:
            color = "#2E8B57" if (lat, lon) in ev_used_stations else "#888888"
            folium.CircleMarker(location=(lat, lon), radius=4, color=color,
                                fill=True, fill_color=color).add_to(m)

    m.save(file_path)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def single_result():
    try:
        start = request.form["start"]
        end = request.form["end"]
        mpg = float(request.form.get("mpg") or 9.0)
        trips = int(request.form["annual_trips"])

        start_city, start_state = [x.strip() for x in start.split(",")]
        end_city, end_state = [x.strip() for x in end.split(",")]

        start_coord = geocode_city_state(start_city, start_state)
        end_coord = geocode_city_state(end_city, end_state)
        miles = calculate_distance(start_coord, end_coord)
        annual_miles = miles * trips

        diesel_price = 3.59
        diesel_fuel = trips * (miles / mpg) * diesel_price
        diesel_maint = miles * (17500 / annual_miles)
        diesel_depr = miles * (16600 / 750000)
        diesel_cost = diesel_fuel + diesel_maint + diesel_depr
        diesel_emissions = (annual_miles * 1.617) / 1000

        diesel_route = get_graphhopper_route(start_coord, end_coord)
        generate_map(diesel_route, start_coord, end_coord, "static/diesel_map.html")

        ev_possible = miles <= 225
        ev_cost = ev_emissions = None
        if ev_possible:
            ev_route = get_graphhopper_route(start_coord, end_coord)
            generate_map(ev_route, start_coord, end_coord, "static/ev_map.html")
            ev_fuel = (annual_miles / 20.39) * 2.208
            ev_maint = miles * (10500 / annual_miles)
            ev_depr = annual_miles * (250000 / 750000)
            ev_cost = ev_fuel + ev_maint + ev_depr
            ev_emissions = (annual_miles * 0.2102) / 1000

        df = pd.DataFrame([{
            "Diesel Mileage (1 Trip)": round(miles, 1),
            "Annual Trips": trips,
            "Diesel Total Mileage": round(annual_miles, 1),
            "Diesel Total Cost": round(diesel_cost, 2),
            "Diesel Total Emissions": round(diesel_emissions, 2),
            "EV Possible?": "Yes" if ev_possible else "No",
            "EV Mileage (1 Trip)": round(miles, 1) if ev_possible else "N/A",
            "EV Total Mileage": round(annual_miles, 1) if ev_possible else "N/A",
            "EV Total Cost": round(ev_cost, 2) if ev_possible else "N/A",
            "EV Total Emissions": round(ev_emissions, 2) if ev_possible else "N/A"
        }])
        df.to_excel("static/single_route_details.xlsx", index=False)

        return render_template("result.html",
            diesel_miles=round(miles, 1),
            annual_trips=trips,
            diesel_annual_miles=round(annual_miles, 1),
            diesel_total_cost=round(diesel_cost, 2),
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=not ev_possible,
            ev_miles=round(miles, 1) if ev_possible else None,
            ev_annual_miles=round(annual_miles, 1) if ev_possible else None,
            ev_total_cost=round(ev_cost, 2) if ev_possible else None,
            ev_emissions=round(ev_emissions, 2) if ev_possible else None,
        )
    except Exception as e:
        return f"<h3>Error: {str(e)}</h3>"

@app.route("/batch-result", methods=["POST"])
def batch_result():
    file = request.files["excel"]
    df = pd.read_excel(file)
    results = []
    for i, row in df.iterrows():
        try:
            start_coord = geocode_city_state(row["Start City"], row["Start State"])
            end_coord = geocode_city_state(row["Destination City"], row["Destination State"])
            mpg = float(row.get("MPG (Will Default To 9)", 9))
            trips = int(row["Annual Trips (Minimum 1)"])
            miles = calculate_distance(start_coord, end_coord)
            annual_miles = miles * trips

            fuel = trips * (miles / mpg) * 3.59
            maint = miles * (17500 / annual_miles)
            depr = miles * (16600 / 750000)
            total_cost = fuel + maint + depr
            emissions = (annual_miles * 1.617) / 1000

            results.append({
                "Start City": row["Start City"],
                "Start State": row["Start State"],
                "Destination City": row["Destination City"],
                "Destination State": row["Destination State"],
                "Diesel Mileage (1 Trip)": round(miles, 1),
                "Annual Trips": trips,
                "Diesel Total Mileage": round(annual_miles, 1),
                "Diesel Total Cost": round(total_cost, 2),
                "Diesel Total Emissions": round(emissions, 2),
                "EV Possible?": "Yes" if miles <= 225 else "No"
            })
            time.sleep(1)  # Prevent rate-limiting
        except Exception as e:
            print(f"Error in row {i}: {e}")
            continue

    result_df = pd.DataFrame(results)
    result_df.to_excel("static/route_results_batch.xlsx", index=False)
    return render_template("batch_result.html", count=len(results))

@app.route("/download")
def download():
    return send_file("static/single_route_details.xlsx", as_attachment=True)

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
