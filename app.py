from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import csv
import folium
from folium.plugins import BeautifyIcon
import time

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

def check_ev_feasibility(start, end, max_leg=225):
    current = start
    while calculate_distance(current, end) > max_leg:
        midpoint = ((current[0] + end[0]) / 2, (current[1] + end[1]) / 2)
        if calculate_distance(current, midpoint) > max_leg:
            return False
        current = midpoint
    return True

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def result():
    try:
        start = request.form["start"]
        end = request.form["end"]
        mpg = float(request.form.get("mpg") or 9.0)
        trips = int(request.form["annual_trips"])
        start_city, start_state = [s.strip() for s in start.split(",")]
        end_city, end_state = [s.strip() for s in end.split(",")]

        start_coord = geocode_city_state(start_city, start_state)
        end_coord = geocode_city_state(end_city, end_state)
        miles = calculate_distance(start_coord, end_coord)
        annual_miles = miles * trips

        diesel_price = 3.59
        diesel_cost = trips * (miles / mpg) * diesel_price + miles * (17500 / annual_miles) + miles * (16600 / 750000)
        diesel_emissions = (annual_miles * 1.617) / 1000

        ev_possible = check_ev_feasibility(start_coord, end_coord)

        if ev_possible:
            ev_cost = (annual_miles / 20.39) * 2.208 + miles * (10500 / annual_miles) + annual_miles * (250000 / 750000)
            ev_emissions = (annual_miles * 0.2102) / 1000
        else:
            ev_cost = ev_emissions = None

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
    try:
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

                diesel_cost = trips * (miles / mpg) * 3.59 + miles * (17500 / annual_miles) + miles * (16600 / 750000)
                diesel_emissions = (annual_miles * 1.617) / 1000
                ev_possible = check_ev_feasibility(start_coord, end_coord)

                results.append({
                    "Start City": row["Start City"],
                    "Start State": row["Start State"],
                    "Destination City": row["Destination City"],
                    "Destination State": row["Destination State"],
                    "Diesel Mileage (1 Trip)": round(miles, 1),
                    "Annual Trips": trips,
                    "Diesel Total Mileage": round(annual_miles, 1),
                    "Diesel Total Cost": round(diesel_cost, 2),
                    "Diesel Total Emissions": round(diesel_emissions, 2),
                    "EV Possible?": "Yes" if ev_possible else "No"
                })
                time.sleep(0.75)
            except Exception as e:
                print(f"Row {i} error: {e}")
                continue

        result_df = pd.DataFrame(results)
        result_df.to_excel("static/route_results_batch.xlsx", index=False)
        return render_template("batch_result.html", count=len(results))
    except Exception as e:
        return f"<h3>Batch processing error: {str(e)}</h3>"

@app.route("/download")
def download():
    return send_file("static/single_route_details.xlsx", as_attachment=True)

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
