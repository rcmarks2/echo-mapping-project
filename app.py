from flask import Flask, render_template, request
from openrouteservice import Client
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import folium
import pandas as pd
import os

app = Flask(__name__)
geolocator = Nominatim(user_agent="route_mapper")
client = Client(key=os.environ.get("ORS_API_KEY"))

# Load EV stations
file_paths = [f"{i}.xlsx" for i in range(1, 8)]
combined = pd.concat([pd.read_excel(f) for f in file_paths])
ev_coords = list(zip(combined['Latitude'], combined['Longitude']))

def geocode_location(location):
    loc = geolocator.geocode(location, timeout=10)
    return (loc.latitude, loc.longitude), loc

def format_label(loc):
    address = loc.raw.get("address", {})
    city = address.get("city") or address.get("town") or address.get("village") or address.get("hamlet") or loc.address.split(',')[0]
    state = address.get("state", "")
    state_abbr = state[:2].upper() if state else ""
    return f"{city}, {state_abbr}"

def create_marker(map_obj, coord, label, color='gold'):
    folium.CircleMarker(
        location=coord,
        radius=10,
        color=color,
        fill=True,
        fill_opacity=1
    ).add_to(map_obj)
    folium.Marker(
        location=[coord[0] + 0.4, coord[1]],
        icon=folium.DivIcon(html=f"<div style='font-weight:bold;font-size:14px;color:black;text-align:center'>{label}</div>")
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

        start_coord, start_raw = geocode_location(start_input)
        end_coord, end_raw = geocode_location(end_input)
        start_label = format_label(start_raw)
        end_label = format_label(end_raw)
        mid_lat = (start_coord[0] + end_coord[0]) / 2
        mid_lon = (start_coord[1] + end_coord[1]) / 2

        # Diesel Route
        diesel_coords = (start_coord[::-1], end_coord[::-1])
        diesel_route = client.directions(diesel_coords, profile='driving-hgv', format='geojson')
        diesel_distance = round(diesel_route['features'][0]['properties']['segments'][0]['distance'] * 0.000621371, 1)

        diesel_map = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
        folium.GeoJson(diesel_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(diesel_map)
        create_marker(diesel_map, start_coord, start_label)
        create_marker(diesel_map, end_coord, end_label)
        folium.Marker(
            location=[mid_lat + 1, mid_lon],
            icon=folium.DivIcon(html=f"<div style='font-weight:bold;font-size:16px;color:black;text-align:center'>{diesel_distance} mi<br>Diesel Route</div>")
        ).add_to(diesel_map)
        diesel_map.save("static/diesel_map.html")

        # EV Route Map
        ev_map = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
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

        ev_path = build_ev_route(start_coord, end_coord)
        if ev_path:
            used_ev_stations = ev_path[1:-1]
            total_ev_distance = 0

            # Plot main EV route (start → evs → end)
            for i in range(len(ev_path) - 1):
                seg = [ev_path[i][::-1], ev_path[i + 1][::-1]]
                segment_route = client.directions(seg, profile='driving-car', format='geojson')
                folium.GeoJson(segment_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(ev_map)
                dist = segment_route['features'][0]['properties']['segments'][0]['distance']
                total_ev_distance += dist

            # Highlight red chargers
            for charger in used_ev_stations:
                folium.CircleMarker(
                    location=charger,
                    radius=8,
                    color='red',
                    fill=True,
                    fill_opacity=1
                ).add_to(ev_map)

            # Add up all detour distances
            detour_extra = 0
            for charger in used_ev_stations:
                route_to_ev = client.directions([start_coord[::-1], charger[::-1]], profile='driving-car', format='geojson')
                dist = route_to_ev['features'][0]['properties']['segments'][0]['distance']
                detour_extra += dist

            baseline_ev_route = client.directions([start_coord[::-1], end_coord[::-1]], profile='driving-car', format='geojson')
            baseline_dist = baseline_ev_route['features'][0]['properties']['segments'][0]['distance']
            detour_added_miles = round((detour_extra - baseline_dist) * 0.000621371, 1)

            final_ev_distance = round(total_ev_distance * 0.000621371 + detour_added_miles, 1)

            folium.Marker(
                location=[mid_lat - 1, mid_lon],
                icon=folium.DivIcon(html=f"<div style='font-weight:bold;font-size:16px;color:black;text-align:center'>{final_ev_distance} mi<br>EV Route</div>")
            ).add_to(ev_map)
        else:
            folium.map.Marker(
                location=[mid_lat, mid_lon],
                icon=folium.DivIcon(html="<div style='font-weight:bold;color:red;font-size:18px;'>EV Truck<br>Not Feasible</div>")
            ).add_to(ev_map)

        ev_map.save("static/ev_map.html")
        return render_template("result.html")

    except Exception as e:
        return f"<h1>Route Error</h1><p>{e}</p>"

# For Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
