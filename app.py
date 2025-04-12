from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
import pandas as pd
import folium
import requests
import os
from shapely.geometry import Point
from geopy.distance import geodesic
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

# Load EV chargers once at startup
ev_chargers = pd.concat([
    pd.read_excel(f"static/{f}.xlsx") for f in [1,2,3,4,5,6,7]
])
ev_chargers = ev_chargers.dropna(subset=["Latitude", "Longitude"])
ev_charger_coords = [(row["Latitude"], row["Longitude"]) for _, row in ev_chargers.iterrows()]

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def get_diesel_miles(start, end):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_api_key,
        "X-Goog-FieldMask": "routes.distanceMeters"
    }
    body = {
        "origin": {"location": {"latLng": {"latitude": start[0], "longitude": start[1]}}},
        "destination": {"location": {"latLng": {"latitude": end[0], "longitude": end[1]}}},
        "travelMode": "DRIVE"
    }
    response = requests.post(url, json=body, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if "routes" in data and data["routes"]:
            meters = data["routes"][0]["distanceMeters"]
            return meters / 1609.34  # meters to miles
        else:
            raise ValueError("No diesel route found")
    else:
        raise ValueError("Diesel routing API error")

def ev_route_possible(start, end):
    total_miles = geodesic(start, end).miles
    if total_miles <= 225:
        return True, [start, end]

    route = [start]
    current = start
    while geodesic(current, end).miles > 225:
        next_stop = None
        for station in ev_charger_coords:
            if station in route:
                continue
            if geodesic(current, station).miles <= 225 and geodesic(station, end).miles < geodesic(current, end).miles:
                next_stop = station
                break
        if not next_stop:
            return False, []
        route.append(next_stop)
        current = next_stop
    route.append(end)
    return True, route

def generate_map(route_coords, used_chargers, all_chargers, label):
    m = folium.Map(location=route_coords[0], zoom_start=5, tiles="cartodbpositron")
    for coord in all_chargers:
        folium.CircleMarker(location=coord, radius=4, color="gray", fill=True, fill_opacity=0.6).add_to(m)
    for coord in used_chargers:
        folium.CircleMarker(location=coord, radius=6, color="#00cc44", fill=True, fill_opacity=1).add_to(m)
    folium.Marker(route_coords[0], popup="Start", icon=folium.DivIcon(html=f"<b>{label[0]}</b>")).add_to(m)
    folium.Marker(route_coords[-1], popup="End", icon=folium.DivIcon(html=f"<b>{label[1]}</b>")).add_to(m)
    folium.PolyLine(route_coords, color="blue", weight=4).add_to(m)
    return m._repr_html_()

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/result", methods=["POST"])
def result():
    try:
        start_city, start_state = request.form["start"].split(",")
        end_city, end_state = request.form["end"].split(",")
        mpg = float(request.form.get("mpg") or 9.0)
        trips = int(request.form["annual_trips"])
        if trips <= 0:
            raise ValueError("Annual trips must be greater than 0")

        start = geocode_city_state(start_city.strip(), start_state.strip())
        end = geocode_city_state(end_city.strip(), end_state.strip())

        # Diesel calculations
        diesel_miles = get_diesel_miles(start, end)
        diesel_total = diesel_miles * trips
        diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_total) + diesel_total * (16600 / 750000)
        diesel_emissions = (diesel_total * 1.617) / 1000

        diesel_map = generate_map([start, end], [], [], (f"{start_city.strip()}, {start_state.strip()}", f"{end_city.strip()}, {end_state.strip()}"))

        # EV logic
        possible, ev_stops = ev_route_possible(start, end)
        if possible:
            ev_total = geodesic(start, end).miles * trips
            ev_cost = (ev_total / 20.39) * 2.208 + geodesic(start, end).miles * (10500 / ev_total) + ev_total * (250000 / 750000)
            ev_emissions = (ev_total * 0.2102) / 1000
            ev_map = generate_map(ev_stops, ev_stops[1:-1], ev_charger_coords, (f"{start_city.strip()}, {start_state.strip()}", f"{end_city.strip()}, {end_state.strip()}"))
        else:
            ev_map = None
            ev_total = ev_cost = ev_emissions = None

        return render_template("result.html",
            diesel_miles=round(diesel_miles, 1),
            annual_trips=trips,
            diesel_annual_miles=round(diesel_total, 1),
            diesel_total_cost=f"{diesel_cost:,.2f}",
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=not possible,
            ev_miles=round(geodesic(start, end).miles, 1) if possible else None,
            ev_annual_miles=round(ev_total, 1) if possible else None,
            ev_total_cost=f"{ev_cost:,.2f}" if possible else None,
            ev_emissions=round(ev_emissions, 2) if possible else None,
            diesel_map=diesel_map,
            ev_map=ev_map
        )
    except Exception as e:
        return f"<h3>Error in single route: {e}</h3>"

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
