from flask import Flask, render_template, request, send_file
from openrouteservice import Client, convert
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import csv
import folium
from folium.plugins import BeautifyIcon
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
import concurrent.futures

app = Flask(__name__)

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)
client = Client(key=os.environ.get("ORS_API_KEY"))
EIA_API_KEY = "gTCTiZrohnP58W0jSqnrvJECt308as0Ih350wX9Q"

CACHE_FILE = "geocache.csv"
geocode_cache = {}

# Load geocode cache
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

def get_average_diesel_price():
    try:
        url = f"https://api.eia.gov/series/?api_key={EIA_API_KEY}&series_id=PET.EMD_EPD2D_PTE_NUS_DPG.W"
        response = requests.get(url)
        data = response.json()
        return float(data["series"][0]["data"][0][1])
    except:
        return 3.592

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

def check_ev_feasibility(start, end, max_leg=225):
    current = start
    while calculate_distance(current, end) > max_leg:
        midpoint = ((current[0] + end[0]) / 2, (current[1] + end[1]) / 2)
        if calculate_distance(current, midpoint) > max_leg:
            return False
        current = midpoint
    return True

def generate_diesel_map(start_coord, end_coord):
    coords = [start_coord[::-1], end_coord[::-1]]
    route = client.directions(coords, profile='driving-hgv', format='geojson')
    m = folium.Map(location=start_coord, zoom_start=6)
    folium.GeoJson(route, name="Diesel Route").add_to(m)
    folium.Marker(start_coord, tooltip="Start", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip="End", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    m.save("static/diesel_map.html")

def generate_ev_map(start_coord, end_coord, max_leg=225):
    m = folium.Map(location=start_coord, zoom_start=6)
    folium.Marker(start_coord, tooltip="Start", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip="End", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    current = start_coord
    ev_coords = [start_coord]
    while calculate_distance(current, end_coord) > max_leg:
        midpoint = ((current[0] + end_coord[0]) / 2, (current[1] + end_coord[1]) / 2)
        ev_coords.append(midpoint)
        current = midpoint
    ev_coords.append(end_coord)
    for coord in ev_coords[1:-1]:
        folium.CircleMarker(coord, radius=7, color="red", fill=True, fill_color="red").add_to(m)
    for i in range(len(ev_coords) - 1):
        coords = [ev_coords[i][::-1], ev_coords[i + 1][::-1]]
        route = client.directions(coords, profile='driving-hgv', format='geojson')
        folium.GeoJson(route, name=f"EV Leg {i+1}").add_to(m)
    m.save("static/ev_map.html")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def single_result():
    try:
        start = request.form["start"]
        end = request.form["end"]
        mpg_input = request.form.get("mpg", "").strip()
        mpg = float(mpg_input) if mpg_input else 9.0
        annual_trips = int(request.form["annual_trips"])
        if annual_trips < 1:
            raise ValueError("Annual trips must be at least 1")

        start_city, start_state = [s.strip() for s in start.split(",")]
        end_city, end_state = [s.strip() for s in end.split(",")]

        start_coord = geocode_city_state(start_city, start_state)
        end_coord = geocode_city_state(end_city, end_state)

        miles = calculate_distance(start_coord, end_coord)
        annual_miles = miles * annual_trips
        diesel_price = get_average_diesel_price()

        diesel_fuel = annual_trips * (miles / mpg) * diesel_price
        diesel_maint = miles * (17500 / annual_miles)
        diesel_depr = miles * (16600 / 750000)
        diesel_cost = diesel_fuel + diesel_maint + diesel_depr
        diesel_emissions = (annual_miles * 1.617) / 1000
        ev_possible = check_ev_feasibility(start_coord, end_coord)

        generate_diesel_map(start_coord, end_coord)
        if ev_possible:
            generate_ev_map(start_coord, end_coord)
            ev_fuel = (annual_miles / 20.39) * 2.208
            ev_maint = miles * (10500 / annual_miles)
            ev_depr = annual_miles * (250000 / 750000)
            ev_cost = ev_fuel + ev_maint + ev_depr
            ev_emissions = (annual_miles * 0.2102) / 1000
        else:
            ev_cost = ev_emissions = None

        df = pd.DataFrame([{
            "Diesel Mileage (1 Trip)": round(miles, 1),
            "Annual Trips": annual_trips,
            "Diesel Total Mileage": round(annual_miles, 1),
            "Diesel Total Cost": round(diesel_cost, 2),
            "Diesel Total Emissions": round(diesel_emissions, 2),
            "EV Possible?": "Yes" if ev_possible else "No",
            "EV Mileage (1 Trip)": round(miles, 1) if ev_possible else "N/A",
            "EV Total Mileage": round(annual_miles, 1) if ev_possible else "N/A",
            "EV Total Cost": round(ev_cost, 2) if ev_cost else "N/A",
            "EV Total Emissions": round(ev_emissions, 2) if ev_emissions else "N/A"
        }])
        df.to_excel("static/single_route_details.xlsx", index=False)

        return render_template(
            "result.html",
            diesel_miles=round(miles, 1),
            annual_trips=annual_trips,
            diesel_annual_miles=round(annual_miles, 1),
            diesel_total_cost=round(diesel_cost, 2),
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=not ev_possible,
            ev_miles=round(miles, 1) if ev_possible else None,
            ev_annual_miles=round(annual_miles, 1) if ev_possible else None,
            ev_total_cost=round(ev_cost, 2) if ev_cost else None,
            ev_emissions=round(ev_emissions, 2) if ev_emissions else None,
        )
    except Exception as e:
        return f"<h3>Error: {str(e)}</h3>"

@app.route("/download")
def download():
    return send_file("static/single_route_details.xlsx", as_attachment=True)

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

@app.route("/batch-result", methods=["POST"])
def batch_result():
    # (You already had this section working — to keep this message clean, let me know if you'd like me to paste the batch logic again)
    pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
