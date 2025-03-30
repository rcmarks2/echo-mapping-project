import os
from flask import Flask, render_template, request
import pandas as pd
import folium
from folium.plugins import DualMap
from geopy.geocoders import Nominatim
from openrouteservice import Client
from math import radians, cos, sin, asin, sqrt

app = Flask(__name__)
import os
client = Client(key=os.environ.get("ORS_API_KEY"))

# Haversine formula for distance in miles
def haversine(coord1, coord2):
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    R = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return R * 2 * asin(sqrt(a))

def geocode_location(place):
    geolocator = Nominatim(user_agent="ev_mapper")
    location = geolocator.geocode(place, timeout=10)
    return location.latitude, location.longitude, location.address

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/result", methods=["POST"])
def result():
    start = request.form["start_city"]
    end = request.form["end_city"]

    # Read and combine internal EV Excel files
    file_paths = [f"{i}.xlsx" for i in range(1, 8)]
    combined = pd.concat([pd.read_excel(f) for f in file_paths])
    combined.columns = ["Latitude", "Longitude"]

    # Get coordinates
    start_lat, start_lon, _ = geocode_location(start)
    end_lat, end_lon, _ = geocode_location(end)
    start_coord = (start_lat, start_lon)
    end_coord = (end_lat, end_lon)

    # ----- Diesel Route -----
    diesel_route = client.directions(
        coordinates=[(start_lon, start_lat), (end_lon, end_lat)],
        profile='driving-hgv',
        format='geojson'
    )
    diesel_coords = [(pt[1], pt[0]) for pt in diesel_route['features'][0]['geometry']['coordinates']]
    diesel_miles = round(diesel_route['features'][0]['properties']['segments'][0]['distance'] * 0.000621371, 1)

    # ----- EV Route -----
    ev_stations = list(combined.itertuples(index=False, name=None))
    ev_path = [start_coord]
    current = start_coord
    remaining = ev_stations.copy()
    max_leg = 225
    used_chargers = []

    while haversine(current, end_coord) > max_leg:
        in_range = [s for s in remaining if haversine(current, s) <= max_leg]
        if not in_range:
            ev_valid = False
            break
        next_stop = min(in_range, key=lambda s: haversine(s, end_coord))
        ev_path.append(next_stop)
        used_chargers.append(next_stop)
        remaining.remove(next_stop)
        current = next_stop
    else:
        ev_path.append(end_coord)
        ev_valid = True

    # ----- Map Drawing -----
    m = DualMap(location=[(start_lat + end_lat)/2, (start_lon + end_lon)/2], zoom_start=6, tiles="CartoDB positron")

    # Diesel map
    folium.PolyLine(diesel_coords, color='#FFD700', weight=5).add_to(m.m1)
    for label, coord in zip([start, end], [start_coord, end_coord]):
        folium.CircleMarker(coord, radius=12, color='#FFD700', fill=True).add_to(m.m1)
        folium.Marker(coord, icon=folium.DivIcon(html=f'<div style="font-weight:bold;color:black;">{label}</div>')).add_to(m.m1)
    mid = diesel_coords[len(diesel_coords)//2]
    folium.Marker(mid, icon=folium.DivIcon(html=f'<div style="font-weight:bold;color:black;">{diesel_miles} mi<br>Diesel Route</div>')).add_to(m.m1)

    # EV map
    for lat, lon in ev_stations:
        folium.CircleMarker((lat, lon), radius=4, color='#7B9BAE', fill=True).add_to(m.m2)
    for label, coord in zip([start, end], [start_coord, end_coord]):
        folium.CircleMarker(coord, radius=12, color='#FFD700', fill=True).add_to(m.m2)
        folium.Marker(coord, icon=folium.DivIcon(html=f'<div style="font-weight:bold;color:black;">{label}</div>')).add_to(m.m2)

    if ev_valid:
        total_miles = 0
        for i in range(len(ev_path) - 1):
            seg = client.directions(
                coordinates=[(ev_path[i][1], ev_path[i][0]), (ev_path[i+1][1], ev_path[i+1][0])],
                profile="driving-car",
                format="geojson"
            )
            coords = [(pt[1], pt[0]) for pt in seg['features'][0]['geometry']['coordinates']]
            folium.PolyLine(coords, color="#FFD700", weight=5).add_to(m.m2)
            total_miles += seg['features'][0]['properties']['segments'][0]['distance'] * 0.000621371

        total_miles += len(used_chargers)  # +1 mile per charger
        total_miles = round(total_miles, 1)

        for i, coord in enumerate(ev_path):
            if coord in used_chargers:
                folium.CircleMarker(coord, radius=7, color='red', fill=True).add_to(m.m2)
                folium.Marker(coord, icon=folium.DivIcon(html=f'<div style="font-weight:bold;color:red;">Charger {i}</div>')).add_to(m.m2)

        mid = coords[len(coords)//2]
        folium.Marker((mid[0]+0.7, mid[1]), icon=folium.DivIcon(html=f'<div style="font-weight:bold;color:black;">{total_miles} mi<br>EV Route</div>')).add_to(m.m2)
    else:
        folium.Marker([(start_lat + end_lat)/2, (start_lon + end_lon)/2],
                      icon=folium.DivIcon(html='<div style="font-weight:bold;color:red;">EV Truck Not Feasible</div>')).add_to(m.m2)

    m.save("static/map.html")
    return render_template("result.html")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

