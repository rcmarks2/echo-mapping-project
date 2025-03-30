from flask import Flask, render_template, request
from openrouteservice import Client
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import folium
import pandas as pd
import os
import time

app = Flask(__name__)
geolocator = Nominatim(user_agent="route_mapper")
client = Client(key=os.environ.get("ORS_API_KEY"))

# Load EV charger station coordinates
file_paths = [f"{i}.xlsx" for i in range(1, 8)]
combined = pd.concat([pd.read_excel(f) for f in file_paths])
ev_coords = list(zip(combined['Latitude'], combined['Longitude']))

# Reverse geocoding for charger coordinates
def reverse_geocode(lat, lon):
    try:
        location = geolocator.reverse((lat, lon), timeout=5)
        return location.address if location else None
    except Exception:
        return None

# Geocode user inputs with retry logic
def geocode_location(location, max_retries=3):
    for attempt in range(max_retries):
        try:
            loc = geolocator.geocode(location, timeout=5)
            if not loc:
                raise ValueError(f"Could not geocode location: {location}")
            return (loc.latitude, loc.longitude), loc
        except (GeocoderTimedOut, Exception):
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise ValueError(f"Geocoding failed after {max_retries} attempts for: {location}")

# Marker utility
def create_marker(map_obj, coord, label, color='gold'):
    folium.CircleMarker(
        location=coord,
        radius=10,
        color=color,
        fill=True,
        fill_opacity=1
    ).add_to(map_obj)
    folium.map.Marker(
        location=[coord[0] + 1, coord[1]],
        icon=folium.DivIcon(html=f"<div style='font-weight:bold;color:black'>{label}</div>")
    ).add_to(map_obj)

# Distance in miles
def calculate_distance(a, b):
    from geopy.distance import geodesic
    return geodesic(a, b).miles

# Build path through EV charging stations every 225 miles
def build_ev_route_with_addresses(start, end, max_distance=225):
    route = [start]
    current = start

    while calculate_distance(current, end) > max_distance:
        candidates = sorted(ev_coords, key=lambda x: calculate_distance(current, x))
        found = False
        for lat, lon in candidates:
            if (lat, lon) in route or calculate_distance(current, (lat, lon)) > max_distance:
                continue
            if calculate_distance((lat, lon), end) < calculate_distance(current, end):
                route.append((lat, lon))
                current = (lat, lon)
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
        start_loc = request.form["start"]
        end_loc = request.form["end"]

        start_coord, start_raw = geocode_location(start_loc)
        end_coord, end_raw = geocode_location(end_loc)

        def format_label(raw):
            city = raw.address.split(',')[0]
            state = raw.raw.get('address', {}).get('state', '')[:2]
            return f"{city}, {state}" if state else city

        start_label = format_label(start_raw)
        end_label = format_label(end_raw)

        # Diesel route
        coords = (start_coord[::-1], end_coord[::-1])
        diesel_route = client.directions(coords, profile='driving-hgv', format='geojson')
        diesel_distance = round(diesel_route['features'][0]['properties']['segments'][0]['distance'] * 0.000621371, 1)

        diesel_map = folium.Map(location=start_coord, zoom_start=5, tiles="CartoDB positron")
        folium.GeoJson(diesel_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(diesel_map)
        create_marker(diesel_map, start_coord, start_label)
        create_marker(diesel_map, end_coord, end_label)
        mid_lat = (start_coord[0] + end_coord[0]) / 2
        mid_lon = (start_coord[1] + end_coord[1]) / 2
        folium.map.Marker(
            location=[mid_lat + 1, mid_lon],
            icon=folium.DivIcon(html=f"<div style='font-weight:bold;color:black;text-align:center'>{diesel_distance} mi<br>Diesel Route</div>")
        ).add_to(diesel_map)

        # EV map
        ev_map = folium.Map(location=start_coord, zoom_start=5, tiles="CartoDB positron")
        create_marker(ev_map, start_coord, start_label)
        create_marker(ev_map, end_coord, end_label)

        for lat, lon in ev_coords:
            folium.CircleMarker(
                location=(lat, lon),
                radius=6,
                color='slategray',
                fill=True,
                fill_opacity=0.8
            ).add_to(ev_map)

        ev_path = build_ev_route_with_addresses(start_coord, end_coord)

        if ev_path:
            used_ev_stations = ev_path[1:-1]
            ev_coords_reversed = [(c[1], c[0]) for c in ev_path]  # lon, lat format

            # Force exact routing through each EV charger coordinate
            ev_route = client.directions(ev_coords_reversed, profile='driving-hgv', format='geojson')
            ev_distance = sum(segment['distance'] for segment in ev_route['features'][0]['properties']['segments'])
            ev_distance_miles = round(ev_distance * 0.000621371 + len(used_ev_stations), 1)

            folium.GeoJson(ev_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(ev_map)

            for station in used_ev_stations:
                folium.CircleMarker(
                    location=station,
                    radius=8,
                    color='red',
                    fill=True,
                    fill_opacity=1
                ).add_to(ev_map)

            folium.map.Marker(
                location=[mid_lat - 2, mid_lon],
                icon=folium.DivIcon(html=f"<div style='font-weight:bold;color:black;text-align:center'>{ev_distance_miles} mi<br>EV Route</div>")
            ).add_to(ev_map)
        else:
            folium.map.Marker(
                location=[mid_lat - 2, mid_lon],
                icon=folium.DivIcon(html="<div style='font-weight:bold;color:red;font-size:18px;'>EV Truck<br>Not Feasible</div>")
            ).add_to(ev_map)

        diesel_map.save("static/diesel_map.html")
        ev_map.save("static/ev_map.html")

        return render_template("result.html")

    except Exception as e:
        return f"<h1>Route Error</h1><p>{e}</p>", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
