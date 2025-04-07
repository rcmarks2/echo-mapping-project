from flask import Flask, render_template, request, send_file
from openrouteservice import Client
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import pandas as pd
import requests
import os
import time
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

app = Flask(__name__)

# Google Geocoding API Key
google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

# OpenRouteService
client = Client(key=os.environ.get("ORS_API_KEY"))
EIA_API_KEY = "gTCTiZrohnP58W0jSqnrvJECt308as0Ih350wX9Q"

geocode_cache = {}

def get_average_diesel_price():
    try:
        url = f'https://api.eia.gov/series/?api_key={EIA_API_KEY}&series_id=PET.EMD_EPD2D_PTE_NUS_DPG.W'
        response = requests.get(url)
        data = response.json()
        return float(data['series'][0]['data'][0][1])
    except:
        return 3.592

def geocode_city_state(city, state, row_num):
    key = (city.strip().lower(), state.strip().lower())
    if key in geocode_cache:
        print(f"Row {row_num}: Cache hit for {city}, {state}")
        return geocode_cache[key]
    print(f"Row {row_num}: Geocoding {city}, {state}")
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    coord = (location.latitude, location.longitude)
    geocode_cache[key] = coord
    return coord

def calculate_distance(a, b):
    return geodesic(a, b).miles

def check_ev_feasibility(start, end, max_leg=225):
    current = start
    while calculate_distance(current, end) > max_leg:
        midpoint = ((current[0] + end[0]) / 2, (current[1] + end[1]) / 2)
        if calculate_distance(current, midpoint) > max_leg:
            return False
        current = midpoint
    return True

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/batch-result", methods=["POST"])
def batch_result():
    file = request.files["excel"]
    df = pd.read_excel(file)

    required_columns = [
        "Start City", "Start State", "Destination City", "Destination State", "Annual Trips (Minimum 1)"
    ]
    for col in required_columns:
        if col not in df.columns:
            return f"<h2>Missing required column: {col}</h2>"

    output_rows = []
    diesel_price = get_average_diesel_price()

    for index, row in df.iterrows():
        try:
            row_num = index + 2
            print(f"\n--- Processing Row {row_num} ---")

            start_city = row["Start City"]
            start_state = row["Start State"]
            end_city = row["Destination City"]
            end_state = row["Destination State"]

            mpg_raw = row.get("MPG (Will Default To 9)", "")
            mpg = float(mpg_raw) if pd.notna(mpg_raw) and str(mpg_raw).strip() else 9.0
            print(f"MPG Used: {mpg}")

            trips = int(row["Annual Trips (Minimum 1)"])
            if trips < 1:
                print(f"[SKIP] Row {row_num}: Annual trips < 1")
                continue

            start_coord = geocode_city_state(start_city, start_state, row_num)
            end_coord = geocode_city_state(end_city, end_state, row_num)

            miles = calculate_distance(start_coord, end_coord)
            annual_miles = miles * trips

            # Diesel Calculations
            diesel_fuel = trips * (miles / mpg) * diesel_price
            diesel_maint = miles * (17500 / annual_miles)
            diesel_depr = miles * (16600 / 750000)
            diesel_cost = diesel_fuel + diesel_maint + diesel_depr
            diesel_emissions = (annual_miles * 1.617) / 1000

            # EV Calculations
            ev_possible = check_ev_feasibility(start_coord, end_coord)
            if ev_possible:
                ev_fuel = (annual_miles / 20.39) * 2.208
                ev_maint = miles * (10500 / annual_miles)
                ev_depr = annual_miles * (250000 / 750000)
                ev_cost = ev_fuel + ev_maint + ev_depr
                ev_emissions = (annual_miles * 0.2102) / 1000
            else:
                ev_cost = ev_emissions = "N/A"

            output_rows.append({
                "Start City": start_city,
                "Start State": start_state,
                "Destination City": end_city,
                "Destination State": end_state,
                "Diesel Mileage (1 Trip)": round(miles, 1),
                "Annual Trips": trips,
                "Diesel Total Mileage": round(annual_miles, 1),
                "Diesel Total Cost": round(diesel_cost, 2),
                "Diesel Total Emissions": round(diesel_emissions, 2),
                "EV Possible?": "Yes" if ev_possible else "No",
                "EV Mileage (1 Trip)": round(miles, 1) if ev_possible else "N/A",
                "EV Total Mileage": round(annual_miles, 1) if ev_possible else "N/A",
                "EV Total Cost": round(ev_cost, 2) if isinstance(ev_cost, (int, float)) else "N/A",
                "EV Total Emmisions": round(ev_emissions, 2) if isinstance(ev_emissions, (int, float)) else "N/A"
            })

        except Exception as e:
            print(f"[ERROR] Row {index+2}: {e}")
            continue

    result_df = pd.DataFrame(output_rows)
    result_path = "static/route_results_batch.xlsx"
    result_df.to_excel(result_path, index=False)

    # Style the Excel file
    wb = load_workbook(result_path)
    ws = wb.active

    header_font = Font(bold=True, color="FFFFFF", name="Roboto")
    header_fill = PatternFill(start_color="002F6C", end_color="002F6C", fill_type="solid")

    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill

    for row in ws.iter_rows(min_row=2, min_col=1, max_col=ws.max_column):
        for cell in row:
            if cell.column_letter == 'K':  # "EV Possible?" column
                if cell.value == "Yes":
                    cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                elif cell.value == "No":
                    cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    wb.save(result_path)

    # Create formulas.txt
    with open("static/formulas.txt", "w") as f:
        f.write("=== Calculation Formulas & Constants Used ===\n\n")
        f.write("DIESEL TRUCK:\n")
        f.write("- Fuel Cost = annual_trips * (miles / mpg) * diesel_price\n")
        f.write("- Maintenance Cost = miles * (17,500 / annual miles)\n")
        f.write("- Depreciation Cost = miles * (16,600 / 750,000)\n")
        f.write("- Emissions = annual miles * 1.617 (kg CO2/mile)\n")
        f.write("- Total Cost = fuel + maintenance + depreciation\n\n")

        f.write("EV TRUCK:\n")
        f.write("- Fuel Cost = (annual miles / 20.39) * 2.208\n")
        f.write("- Maintenance Cost = miles * (10,500 / annual miles)\n")
        f.write("- Depreciation Cost = annual miles * (250,000 / 750,000)\n")
        f.write("- Emissions = annual miles * 0.2102 (kg CO2/mile)\n")
        f.write("- Total Cost = fuel + maintenance + depreciation\n\n")

        f.write("DEFAULTS USED:\n")
        f.write("- Diesel MPG default: 9.0\n")
        f.write(f"- Diesel price used: ${diesel_price:.3f}/gal\n")

    return render_template("batch_result.html", count=len(output_rows))

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
