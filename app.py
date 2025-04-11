from flask import Flask, render_template, request, send_file
from openrouteservice import Client
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import csv
import folium
from folium.plugins import BeautifyIcon

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "YOUR_GOOGLE_API_KEY"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)
client = Client(key=os.environ.get("ORS_API_KEY"))
EIA_API_KEY = "gTCTiZrohnP58W0jSqnrvJECt308as0Ih350wX9Q"

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

def load_ev_stations():
    ev_stations = []
    for i in range(1, 8):
        file_path = f"ev_stations/{i}.xlsx"
        if os.path.exists(file_path):
            df = pd.read_excel(file_path)
            for _, row in df.iterrows():
                lat = row.get('Latitude') or row.get('latitude')
                lon = row.get('Longitude') or row.get('longitude')
                if pd.notna(lat) and pd.notna(lon):
                    ev_stations.append((lat, lon))
    return ev_stations

def generate_diesel_map(start_coord, end_coord):
    coords = [start_coord[::-1], end_coord[::-1]]
    route = client.directions(coords, profile='driving-hgv', format='geojson')
    m = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
    folium.GeoJson(route, name="Diesel Route", style_function=lambda x: {"color": "#002f6c", "weight": 5}).add_to(m)
    folium.Marker(start_coord, tooltip="Start", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip="End", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    m.save("static/diesel_map.html")

def generate_ev_map(start_coord, end_coord, max_leg=225):
    all_stations = load_ev_stations()
    used_stations = []
    m = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")

    folium.Marker(start_coord, tooltip="Start", icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip="End", icon=BeautifyIcon(icon_shape='marker')).add_to(m)

    def nearest_station(coord, remaining_miles):
        best = None
        for station in all_stations:
            dist = calculate_distance(coord, station)
            if dist <= remaining_miles and (best is None or dist < calculate_distance(coord, best)):
                best = station
        return best

    current = start_coord
    while calculate_distance(current, end_coord) > max_leg:
        next_stop = nearest_station(current, max_leg)
        if not next_stop:
            break
        used_stations.append(next_stop)
        leg_coords = [current[::-1], next_stop[::-1]]
        route = client.directions(leg_coords, profile='driving-hgv', format='geojson')
        folium.GeoJson(route, style_function=lambda x: {'color': '#002f6c', 'weight': 5}).add_to(m)
        current = next_stop

    if calculate_distance(current, end_coord) <= max_leg:
        leg_coords = [current[::-1], end_coord[::-1]]
        route = client.directions(leg_coords, profile='driving-hgv', format='geojson')
        folium.GeoJson(route, style_function=lambda x: {'color': '#002f6c', 'weight': 5}).add_to(m)
    else:
        print("EV route not possible with available stations.")

    for lat, lon in all_stations:
        color = '#2E8B57' if (lat, lon) in used_stations else '#888888'
        folium.CircleMarker(location=(lat, lon), radius=4, color=color, fill=True, fill_color=color).add_to(m)

    m.save("static/ev_map.html")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/download")
def download():
    return send_file("static/single_route_details.xlsx", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
