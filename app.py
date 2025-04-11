from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import time
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "YOUR_GOOGLE_API_KEY"
ors_key = "5b3ce3597851110001cf6248a92956daf1e74ff1b93a12f8c30baf99"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def calculate_distance(a, b):
    return geodesic(a, b).miles

def get_openroute_path(start, end):
    url = "https://api.openrouteservice.org/v2/directions/driving-hgv"
    headers = {
        "Authorization": ors_key,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [[start[1], start[0]], [end[1], end[0]]]
    }
    print("Requesting ORS with:", body)
    response = requests.post(url, json=body, headers=headers)
    print("Status:", response.status_code)
    print("Response:", response.text)
    if response.status_code == 200:
        return response.json()["features"][0]["geometry"]["coordinates"]
    else:
        print("ORS Error:", response.text)
        return None

def calculate_total_route_mileage(route_coords):
    total_miles = 0
    for i in range(len(route_coords) - 1):
        a = (route_coords[i][1], route_coords[i][0])
        b = (route_coords[i + 1][1], route_coords[i + 1][0])
        total_miles += calculate_distance(a, b)
    return total_miles

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
        start_coord = geocode_city_state(start_city.strip(), start_state.strip())
        end_coord = geocode_city_state(end_city.strip(), end_state.strip())

        diesel_route = get_openroute_path(start_coord, end_coord)
        if not diesel_route:
            raise Exception("Diesel route failed")

        diesel_miles = calculate_total_route_mileage(diesel_route)
        diesel_annual_miles = diesel_miles * trips
        diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_annual_miles) + diesel_miles * (16600 / 750000)
        diesel_emissions = (diesel_annual_miles * 1.617) / 1000

        all_stations = load_ev_stations()
        used_stations, ev_route_coords = [], []
        current = start_coord
        ev_possible = True

        def nearest_station(c, e):
            return min([s for s in all_stations if calculate_distance(c, s) <= 225 and s not in used_stations],
                       key=lambda s: calculate_distance(s, e), default=None)

        while calculate_distance(current, end_coord) > 225:
            next_stop = nearest_station(current, end_coord)
            if not next_stop:
                ev_possible = False
                break
            segment = get_openroute_path(current, next_stop)
            if not segment:
                ev_possible = False
                break
            ev_route_coords.extend(segment)
            used_stations.append(next_stop)
            current = next_stop

        if ev_possible:
            final_segment = get_openroute_path(current, end_coord)
            if final_segment:
                ev_route_coords.extend(final_segment)
            else:
                ev_possible = False

        if ev_possible:
            ev_miles = calculate_total_route_mileage(ev_route_coords)
            ev_annual_miles = ev_miles * trips
            ev_cost = (ev_annual_miles / 20.39) * 2.208 + ev_miles * (10500 / ev_annual_miles) + ev_annual_miles * (250000 / 750000)
            ev_emissions = (ev_annual_miles * 0.2102) / 1000
        else:
            ev_miles = ev_annual_miles = ev_cost = ev_emissions = None

        return render_template("result.html",
            diesel_miles=round(diesel_miles, 1),
            annual_trips=trips,
            diesel_annual_miles=round(diesel_annual_miles, 1),
            diesel_total_cost=round(diesel_cost, 2),
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=not ev_possible,
            ev_miles=round(ev_miles, 1) if ev_miles else None,
            ev_annual_miles=round(ev_annual_miles, 1) if ev_annual_miles else None,
            ev_total_cost=round(ev_cost, 2) if ev_cost else None,
            ev_emissions=round(ev_emissions, 2) if ev_emissions else None
        )
    except Exception as e:
        return f"<h3>Error in single route: {e}</h3>"

@app.route("/batch-result", methods=["POST"])
def batch_result():
    try:
        file = request.files["excel"]
        df = pd.read_excel(file)
        results = []
        all_stations = load_ev_stations()

        for i, row in df.iterrows():
            try:
                start_coord = geocode_city_state(row["Start City"], row["Start State"])
                end_coord = geocode_city_state(row["Destination City"], row["Destination State"])
                mpg = float(row.get("MPG (Will Default To 9)", 9))
                trips = int(row["Annual Trips (Minimum 1)"])
                diesel_route = get_openroute_path(start_coord, end_coord)
                if not diesel_route:
                    raise Exception("Diesel route failed")
                diesel_miles = calculate_total_route_mileage(diesel_route)
                diesel_annual_miles = diesel_miles * trips
                diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_annual_miles) + diesel_miles * (16600 / 750000)
                diesel_emissions = (diesel_annual_miles * 1.617) / 1000

                used_stations, ev_route_coords = [], []
                current = start_coord
                ev_possible = True

                def nearest_station(c, e):
                    return min([s for s in all_stations if calculate_distance(c, s) <= 225 and s not in used_stations],
                               key=lambda s: calculate_distance(s, e), default=None)

                while calculate_distance(current, end_coord) > 225:
                    next_stop = nearest_station(current, end_coord)
                    if not next_stop:
                        ev_possible = False
                        break
                    segment = get_openroute_path(current, next_stop)
                    if not segment:
                        ev_possible = False
                        break
                    ev_route_coords.extend(segment)
                    used_stations.append(next_stop)
                    current = next_stop

                if ev_possible:
                    final_segment = get_openroute_path(current, end_coord)
                    if final_segment:
                        ev_route_coords.extend(final_segment)
                    else:
                        ev_possible = False

                if ev_possible:
                    ev_miles = calculate_total_route_mileage(ev_route_coords)
                    ev_annual_miles = ev_miles * trips
                    ev_cost = (ev_annual_miles / 20.39) * 2.208 + ev_miles * (10500 / ev_annual_miles) + ev_annual_miles * (250000 / 750000)
                    ev_emissions = (ev_annual_miles * 0.2102) / 1000
                else:
                    ev_miles = ev_annual_miles = ev_cost = ev_emissions = "N/A"

                results.append({
                    "Start City": row["Start City"],
                    "Start State": row["Start State"],
                    "Destination City": row["Destination City"],
                    "Destination State": row["Destination State"],
                    "Diesel Mileage (1 Trip)": round(diesel_miles, 1),
                    "Annual Trips": trips,
                    "Diesel Total Mileage": round(diesel_annual_miles, 1),
                    "Diesel Total Cost": round(diesel_cost, 2),
                    "Diesel Total Emissions": round(diesel_emissions, 2),
                    "EV Possible?": "Yes" if ev_possible else "No",
                    "EV Mileage (1 Trip)": round(ev_miles, 1) if isinstance(ev_miles, float) else "N/A",
                    "EV Total Mileage": round(ev_annual_miles, 1) if isinstance(ev_annual_miles, float) else "N/A",
                    "EV Total Cost": round(ev_cost, 2) if isinstance(ev_cost, float) else "N/A",
                    "EV Total Emissions": round(ev_emissions, 2) if isinstance(ev_emissions, float) else "N/A"
                })
            except Exception as err:
                print(f"Error in row {i+2}: {err}")
                continue

        output_path = "static/route_results_batch.xlsx"
        pd.DataFrame(results).to_excel(output_path, index=False)

        wb = load_workbook(output_path)
        ws = wb.active
        green_fill = PatternFill(start_color="92D050", end_color="92D050", fill_type="solid")
        red_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")

        for row in range(2, ws.max_row + 1):
            cell = ws[f"J{row}"]
            if cell.value == "Yes":
                cell.fill = green_fill
            elif cell.value == "No":
                cell.fill = red_fill

        for col in ["H", "K", "M"]:
            for row in range(2, ws.max_row + 1):
                cell = ws[f"{col}{row}"]
                if isinstance(cell.value, float):
                    cell.number_format = '"$"#,##0.00'

        wb.save(output_path)
        return render_template("batch_result.html", count=len(results))

    except Exception as e:
        return f"<h3>Error processing batch: {e}</h3>"

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
