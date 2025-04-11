from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import folium
from folium.plugins import BeautifyIcon

app = Flask(__name__, template_folder="templates", static_folder="static")

# API Keys
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

def generate_map(route_coords, start_coord, end_coord, start_label, end_label, file_path):
    m = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
    folium.Marker(start_coord, tooltip=start_label, icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.Marker(end_coord, tooltip=end_label, icon=BeautifyIcon(icon_shape='marker')).add_to(m)
    folium.PolyLine(locations=[(lat, lon) for lon, lat in route_coords],
                    color="#002f6c", weight=5).add_to(m)
    m.save(file_path)

def check_ev_feasibility(start, end, max_leg=225):
    stations = load_ev_stations()
    path = [start]
    current = start

    while calculate_distance(current, end) > max_leg:
        reachable = [s for s in stations if calculate_distance(current, s) <= max_leg and s not in path]
        if not reachable:
            return False
        next_stop = min(reachable, key=lambda s: calculate_distance(s, end))
        path.append(next_stop)
        current = next_stop

    path.append(end)
    return True

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def result():
    try:
        start_input = request.form["start"]
        end_input = request.form["end"]
        mpg = float(request.form.get("mpg") or 9.0)
        trips = int(request.form["annual_trips"])

        start_city, start_state = [s.strip() for s in start_input.split(",")]
        end_city, end_state = [s.strip() for s in end_input.split(",")]
        start_label = f"{start_city}, {start_state}"
        end_label = f"{end_city}, {end_state}"

        start_coord = geocode_city_state(start_city, start_state)
        end_coord = geocode_city_state(end_city, end_state)
        miles = calculate_distance(start_coord, end_coord)
        annual_miles = miles * trips

        # Diesel calculations
        diesel_fuel = trips * (miles / mpg) * 3.59
        diesel_maint = miles * (17500 / annual_miles)
        diesel_depr = miles * (16600 / 750000)
        diesel_cost = diesel_fuel + diesel_maint + diesel_depr
        diesel_emissions = (annual_miles * 1.617) / 1000

        # Generate diesel map
        diesel_route = get_graphhopper_route(start_coord, end_coord)
        generate_map(diesel_route, start_coord, end_coord, start_label, end_label, "static/diesel_map.html")

        # EV Feasibility Check
        ev_possible = check_ev_feasibility(start_coord, end_coord)
        if ev_possible:
            ev_route = get_graphhopper_route(start_coord, end_coord)
            generate_map(ev_route, start_coord, end_coord, start_label, end_label, "static/ev_map.html")
            ev_fuel = (annual_miles / 20.39) * 2.208
            ev_maint = miles * (10500 / annual_miles)
            ev_depr = annual_miles * (250000 / 750000)
            ev_cost = ev_fuel + ev_maint + ev_depr
            ev_emissions = (annual_miles * 0.2102) / 1000
        else:
            ev_cost = ev_emissions = None

        return render_template("result.html",
            diesel_miles=round(miles, 1),
            annual_trips=trips,
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
