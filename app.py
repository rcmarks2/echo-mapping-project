from flask import Flask, render_template, request, send_file
from openrouteservice import Client
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import requests
import folium
import pandas as pd
import os

app = Flask(__name__)
geolocator = Nominatim(user_agent="route_mapper")
client = Client(key=os.environ.get("ORS_API_KEY"))
EIA_API_KEY = "gTCTiZrohnP58W0jSqnrvJECt308as0Ih350wX9Q"

file_paths = [f"{i}.xlsx" for i in range(1, 8)]
combined = pd.concat([pd.read_excel(f) for f in file_paths])
ev_coords = list(zip(combined['Latitude'], combined['Longitude']))

STATE_ABBR = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA',
    'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE', 'Florida': 'FL', 'Georgia': 'GA',
    'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
    'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA',
    'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT',
    'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM',
    'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
    'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'
}

def get_average_diesel_price():
    url = f'https://api.eia.gov/series/?api_key={EIA_API_KEY}&series_id=PET.EMD_EPD2D_PTE_NUS_DPG.W'
    try:
        response = requests.get(url)
        data = response.json()
        price = float(data['series'][0]['data'][0][1])
        return price
    except:
        return 3.592

def geocode_location(location):
    loc = geolocator.geocode(location, timeout=10)
    return (loc.latitude, loc.longitude), loc

def format_label(loc):
    address = loc.raw.get("address", {})
    city = (
        address.get("city") or address.get("town") or address.get("village") or
        address.get("hamlet") or loc.address.split(',')[0]
    )
    state_abbr = STATE_ABBR.get(address.get("state", ""), "")
    return f"{city}, {state_abbr}" if city and state_abbr else loc.address.split(',')[0]

def create_marker(map_obj, coord, label, color='#002f6c', offset=0.6):
    folium.CircleMarker(
        location=coord,
        radius=10,
        color=color,
        fill=True,
        fill_opacity=1
    ).add_to(map_obj)
    folium.Marker(
        location=[coord[0] + offset, coord[1]],
        icon=folium.DivIcon(html=f"<div style='font-family:Roboto;font-weight:bold;font-size:14px;color:#002f6c'>{label}</div>")
    ).add_to(map_obj)

def calculate_distance(a, b):
    return geodesic(a, b).miles

def build_ev_route(start, end, max_leg=225):
    route = [start]
    current = start
    while calculate_distance(current, end) > max_leg:
        candidates = sorted(ev_coords, key=lambda x: calculate_distance(current, x))
        found = False
        for station in candidates:
            if station in route or calculate_distance(current, station) > max_leg:
                continue
            if calculate_distance(station, end) < calculate_distance(current, end):
                route.append(station)
                current = station
                found = True
                break
        if not found:
            return None
    route.append(end)
    return route

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def result():
    try:
        start_input = request.form["start"]
        end_input = request.form["end"]
        mpg_input = request.form.get("mpg")
        annual_trips_input = request.form.get("annual_trips")

        mpg = float(mpg_input) if mpg_input else 9.0
        annual_trips = int(annual_trips_input) if annual_trips_input else 0

        start_coord, start_raw = geocode_location(start_input)
        end_coord, end_raw = geocode_location(end_input)

        start_label = format_label(start_raw)
        end_label = format_label(end_raw)

        diesel_coords = (start_coord[::-1], end_coord[::-1])
        diesel_route = client.directions(diesel_coords, profile='driving-hgv', format='geojson')
        diesel_distance_km = diesel_route['features'][0]['properties']['segments'][0]['distance']
        diesel_miles = round(diesel_distance_km * 0.000621371, 1)

        diesel_map = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
        folium.GeoJson(diesel_route, style_function=lambda x: {'color': '#002f6c', 'weight': 5}).add_to(diesel_map)
        create_marker(diesel_map, start_coord, start_label)
        create_marker(diesel_map, end_coord, end_label)
        diesel_map.save("static/diesel_map.html")

        ev_map = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
        create_marker(ev_map, start_coord, start_label)
        create_marker(ev_map, end_coord, end_label)

        for lat, lon in ev_coords:
            folium.CircleMarker((lat, lon), radius=6, color='#888888', fill=True).add_to(ev_map)

        if diesel_miles <= 225:
            folium.GeoJson(diesel_route, style_function=lambda x: {'color': '#002f6c', 'weight': 5}).add_to(ev_map)
            ev_miles, ev_unavailable = diesel_miles, False
        else:
            ev_path = build_ev_route(start_coord, end_coord)
            if ev_path:
                total_ev_distance = 0.0
                for i in range(len(ev_path) - 1):
                    segment = [ev_path[i][::-1], ev_path[i + 1][::-1]]
                    route_segment = client.directions(segment, profile='driving-car', format='geojson')
                    folium.GeoJson(route_segment, style_function=lambda x: {'color': '#002f6c', 'weight': 5}).add_to(ev_map)
                    total_ev_distance += route_segment['features'][0]['properties']['segments'][0]['distance']
                ev_miles = round(total_ev_distance * 0.000621371 + len(ev_path)-2, 1)
                ev_unavailable = False
                for charger in ev_path[1:-1]:
                    folium.CircleMarker(charger, radius=8, color='#4CAF50', fill=True).add_to(ev_map)
            else:
                ev_miles, ev_unavailable = None, True

        ev_map.save("static/ev_map.html")

        diesel_price = get_average_diesel_price()
        diesel_annual_miles = diesel_miles * annual_trips
        diesel_fuel_cost = annual_trips * (diesel_miles / mpg) * diesel_price if annual_trips else 0
        diesel_maintenance = diesel_miles * (17500 / (diesel_miles * annual_trips)) if annual_trips else 0
        diesel_depreciation = diesel_miles * (16600 / 750000)
        diesel_total_cost = diesel_fuel_cost + diesel_maintenance + diesel_depreciation
        diesel_emissions = (diesel_annual_miles * 1.617) / 1000

        if not ev_unavailable:
            ev_annual_miles = ev_miles * annual_trips
            ev_fuel_cost = (ev_annual_miles / 20.39) * 2.208
            ev_maintenance = ev_annual_miles * (10500 / ev_annual_miles) if annual_trips else 0
            ev_depreciation = ev_annual_miles * (250000 / 750000)
            ev_total_cost = ev_fuel_cost + ev_maintenance + ev_depreciation
            ev_emissions = (ev_annual_miles * 0.2102) / 1000
        else:
            ev_annual_miles = ev_total_cost = ev_emissions = 0

        with open("static/calculations.txt", "w") as f:
            f.write("=== DIESEL TRUCK ===\n")
            f.write(f"Route Mileage: {diesel_miles} mi\nAnnual Trips: {annual_trips}\nAnnual Mileage: {diesel_annual_miles} mi\n")
            f.write(f"Fuel Cost: ${diesel_fuel_cost:.2f}\nMaintenance: ${diesel_maintenance:.2f}\nDepreciation: ${diesel_depreciation:.2f}\n")
            f.write(f"Total Cost: ${diesel_total_cost:.2f}\nEmissions: {diesel_emissions:.2f} metric tons\n\n")
            f.write("=== EV TRUCK ===\n")
            f.write(f"Route Mileage: {ev_miles} mi\nAnnual Trips: {annual_trips}\nAnnual Mileage: {ev_annual_miles} mi\n")
            f.write(f"Fuel Cost: ${ev_fuel_cost:.2f}\nMaintenance: ${ev_maintenance:.2f}\nDepreciation: ${ev_depreciation:.2f}\n")
            f.write(f"Total Cost: ${ev_total_cost:.2f}\nEmissions: {ev_emissions:.2f} metric tons\n")

        return render_template("result.html",
                               diesel_miles=diesel_miles,
                               ev_miles=ev_miles,
                               ev_unavailable=ev_unavailable,
                               annual_trips=annual_trips,
                               annual_miles_diesel=diesel_annual_miles,
                               total_cost_diesel=round(diesel_total_cost, 2),
                               emissions_diesel=round(diesel_emissions, 2),
                               annual_miles_ev=ev_annual_miles,
                               total_cost_ev=round(ev_total_cost, 2),
                               emissions_ev=round(ev_emissions, 2))

    except Exception as e:
        return f"<h2>Route Error</h2><p>{e}</p>"

@app.route("/download")
def download():
    return send_file("static/calculations.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
