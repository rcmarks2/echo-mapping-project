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

file_paths = [f"{i}.xlsx" for i in range(1, 8)]
combined = pd.concat([pd.read_excel(f) for f in file_paths])
ev_coords = list(zip(combined['Latitude'], combined['Longitude']))

STATE_ABBR = {
    # [Include the same dictionary as earlier (omitted here for brevity)]
    'Illinois': 'IL', # etc...
}

def geocode_location(location):
    loc = geolocator.geocode(location, timeout=10)
    return (loc.latitude, loc.longitude), loc

def format_label(loc):
    address = loc.raw.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or loc.address.split(',')[0]
    )
    state_abbr = STATE_ABBR.get(address.get("state", ""), "")
    return f"{city}, {state_abbr}" if city and state_abbr else loc.address.split(',')[0]

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
        icon=folium.DivIcon(html=f"<div style='font-weight:bold;font-size:14px;color:black'>{label}</div>")
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

        # Retrieve MPG from user input or use default (9 mpg)
        mpg_input = request.form.get("mpg")
        mpg = float(mpg_input) if mpg_input else 9.0

        start_coord, start_raw = geocode_location(start_input)
        end_coord, end_raw = geocode_location(end_input)

        start_label = format_label(start_raw)
        end_label = format_label(end_raw)

        # Diesel route
        diesel_coords = (start_coord[::-1], end_coord[::-1])
        diesel_route = client.directions(diesel_coords, profile='driving-hgv', format='geojson')
        diesel_distance_km = diesel_route['features'][0]['properties']['segments'][0]['distance']
        diesel_distance = round(diesel_distance_km * 0.000621371, 1)

        diesel_map = folium.Map(location=start_coord, zoom_start=6, tiles="CartoDB positron")
        folium.GeoJson(diesel_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(diesel_map)
        create_marker(diesel_map, start_coord, start_label)
        create_marker(diesel_map, end_coord, end_label)
        diesel_map.save("static/diesel_map.html")

        # EV route
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

        if diesel_distance <= 225:
            folium.GeoJson(diesel_route, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(ev_map)
            ev_miles = diesel_distance
            ev_unavailable = False
        else:
            ev_path = build_ev_route(start_coord, end_coord)
            if ev_path:
                used_ev_stations = ev_path[1:-1]
                total_ev_distance = 0.0
                for i in range(len(ev_path) - 1):
                    segment = [ev_path[i][::-1], ev_path[i + 1][::-1]]
                    route_segment = client.directions(segment, profile='driving-car', format='geojson')
                    folium.GeoJson(route_segment, style_function=lambda x: {'color': 'gold', 'weight': 5}).add_to(ev_map)
                    total_ev_distance += route_segment['features'][0]['properties']['segments'][0]['distance']
                ev_miles = round(total_ev_distance * 0.000621371 + len(used_ev_stations), 1)
                for charger in used_ev_stations:
                    folium.CircleMarker(charger, radius=8, color='red', fill=True, fill_opacity=1).add_to(ev_map)
                ev_unavailable = False
            else:
                ev_miles = None
                ev_unavailable = True

        ev_map.save("static/ev_map.html")

        return render_template("result.html",
                               diesel_miles=diesel_distance,
                               ev_miles=ev_miles,
                               ev_unavailable=ev_unavailable,
                               mpg=mpg)

    except Exception as e:
        return f"<h2>Route Error</h2><p>{e}</p>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
