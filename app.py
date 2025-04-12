from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
import pandas as pd
import folium
import requests
import os
from geopy.distance import geodesic
from openpyxl import load_workbook
import polyline

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

@app.route("/")
def index():
    return render_template("index.html")

# Load EV chargers
ev_chargers = pd.concat([
    pd.read_excel(f"static/{f}.xlsx") for f in [1, 2, 3, 4, 5, 6, 7]
])
ev_chargers = ev_chargers.dropna(subset=["Latitude", "Longitude"])
ev_charger_coords = [(row["Latitude"], row["Longitude"]) for _, row in ev_chargers.iterrows()]

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def get_routed_segment(start, end, return_distance=False):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_api_key,
        "X-Goog-FieldMask": "routes.polyline.encodedPolyline,routes.distanceMeters"
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
            encoded = data["routes"][0]["polyline"]["encodedPolyline"]
            coords = polyline.decode(encoded)
            if return_distance:
                meters = data["routes"][0]["distanceMeters"]
                return coords, meters / 1609.34
            return coords
    return [] if not return_distance else ([], 0)

def build_ev_path(start, end):
    if geodesic(start, end).miles <= 225:
        return True
    current = start
    while geodesic(current, end).miles > 225:
        next_stop = None
        for station in ev_charger_coords:
            if geodesic(current, station).miles <= 225 and geodesic(station, end).miles < geodesic(current, end).miles:
                next_stop = station
                break
        if not next_stop:
            return False
        current = next_stop
    return True

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
        diesel_coords, diesel_miles = get_routed_segment(start, end, return_distance=True)
        diesel_total = diesel_miles * trips
        diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_total) + diesel_total * (16600 / 750000)
        diesel_emissions = (diesel_total * 1.617) / 1000
        diesel_map = generate_map(diesel_coords, [], [], (f"{start_city.strip()}, {start_state.strip()}", f"{end_city.strip()}, {end_state.strip()}"))
        ev_possible, ev_stops = build_ev_path(start, end), []
        if ev_possible:
            routed_coords = []
            total_ev_miles = 0
            ev_stops = [start]
            current = start
            while geodesic(current, end).miles > 225:
                for station in ev_charger_coords:
                    if geodesic(current, station).miles <= 225 and geodesic(station, end).miles < geodesic(current, end).miles:
                        ev_stops.append(station)
                        current = station
                        break
            ev_stops.append(end)
            for i in range(len(ev_stops) - 1):
                leg_coords, leg_miles = get_routed_segment(ev_stops[i], ev_stops[i + 1], return_distance=True)
                routed_coords.extend(leg_coords)
                total_ev_miles += leg_miles
            ev_total = total_ev_miles * trips
            ev_cost = (ev_total / 20.39) * 2.208 + total_ev_miles * (10500 / ev_total) + ev_total * (250000 / 750000)
            ev_emissions = (ev_total * 0.2102) / 1000
            ev_map = generate_map(routed_coords, ev_stops[1:-1], ev_charger_coords, (f"{start_city.strip()}, {start_state.strip()}", f"{end_city.strip()}, {end_state.strip()}"))
        else:
            ev_map = None
            ev_total = ev_cost = ev_emissions = None

        return render_template("result.html",
            diesel_miles=round(diesel_miles, 1),
            annual_trips=trips,
            diesel_annual_miles=round(diesel_total, 1),
            diesel_total_cost=f"{diesel_cost:,.2f}",
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=not ev_possible,
            ev_miles=round(total_ev_miles, 1) if ev_possible else None,
            ev_annual_miles=round(ev_total, 1) if ev_possible else None,
            ev_total_cost=f"{ev_cost:,.2f}" if ev_possible else None,
            ev_emissions=round(ev_emissions, 2) if ev_possible else None,
            diesel_map=diesel_map,
            ev_map=ev_map
        )
    except Exception as e:
        return f"<h3>Error in single route: {e}</h3>"

@app.route("/batch-result", methods=["POST"])
def batch_result():
    try:
        uploaded_file = request.files['excel']
        if uploaded_file.filename == '':
            return '<h3>No file selected</h3>'
        df = pd.read_excel(uploaded_file)
        wb = load_workbook('static/fullbatchresult.xlsx')
        ws = wb.active

        for i in range(len(df)):
            try:
                row = df.iloc[i]
                start_city = str(row["Start City"]).strip()
                start_state = str(row["Start State"]).strip()
                dest_city = str(row["Destination City"]).strip()
                dest_state = str(row["Destination State"]).strip()
                trips = max(int(row.get("Annual Trips (Minimum 1)", 1)), 1)
                mpg = float(row.get("MPG (Optional Will Default to 9)", 9) or 9)

                start = geocode_city_state(start_city, start_state)
                end = geocode_city_state(dest_city, dest_state)
                _, diesel_miles = get_routed_segment(start, end, return_distance=True)
                ev_possible = "Yes" if build_ev_path(start, end) else "No"
                ev_miles = round(diesel_miles, 1) if ev_possible == "Yes" else "N/A"

                # Write columns A–H only
                ws.cell(row=i + 3, column=1).value = start_city
                ws.cell(row=i + 3, column=2).value = start_state
                ws.cell(row=i + 3, column=3).value = dest_city
                ws.cell(row=i + 3, column=4).value = dest_state
                ws.cell(row=i + 3, column=5).value = round(diesel_miles, 1)
                ws.cell(row=i + 3, column=6).value = trips
                ws.cell(row=i + 3, column=7).value = ev_possible
                ws.cell(row=i + 3, column=8).value = ev_miles
            except Exception as err:
                ws.cell(row=i + 3, column=1).value = f"Error: {str(err)}"

        wb.save('static/fullbatchresult.xlsx')
        return render_template('batch_result.html', excel_download='/download-batch-excel', txt_download='/download-formulas')
    except Exception as e:
        return f'<h3>Error in batch processing: {e}</h3>'

@app.route("/download-batch-excel")
def download_batch_excel():
    return send_file("static/fullbatchresult.xlsx", as_attachment=True)

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
