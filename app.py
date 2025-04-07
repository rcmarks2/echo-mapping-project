from flask import Flask, render_template, request, send_file
from openrouteservice import Client
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import pandas as pd
import requests
import os

app = Flask(__name__)
geolocator = Nominatim(user_agent="route_mapper")
client = Client(key=os.environ.get("ORS_API_KEY"))
EIA_API_KEY = "gTCTiZrohnP58W0jSqnrvJECt308as0Ih350wX9Q"

def get_average_diesel_price():
    try:
        url = f'https://api.eia.gov/series/?api_key={EIA_API_KEY}&series_id=PET.EMD_EPD2D_PTE_NUS_DPG.W'
        response = requests.get(url)
        data = response.json()
        return float(data['series'][0]['data'][0][1])
    except:
        return 3.592  # fallback price

def geocode_city(city_name):
    location = geolocator.geocode(city_name, timeout=10)
    if not location:
        raise ValueError(f"Could not geocode city: {city_name}")
    return (location.latitude, location.longitude)

def calculate_distance(a, b):
    return geodesic(a, b).miles

def check_ev_feasibility(start, end, max_leg=225):
    route = [start]
    current = start
    while calculate_distance(current, end) > max_leg:
        midpoint = ((current[0] + end[0]) / 2, (current[1] + end[1]) / 2)
        if calculate_distance(current, midpoint) > max_leg:
            return False
        current = midpoint
    return True

@app.route("/batch-result", methods=["POST"])
def batch_result():
    file = request.files["excel"]
    df = pd.read_excel(file)

    required_columns = ["Start City", "End City", "Annual Trips"]
    for col in required_columns:
        if col not in df.columns:
            return f"<h2>Missing required column: {col}</h2>"

    output_rows = []
    diesel_price = get_average_diesel_price()

    for index, row in df.iterrows():
        start_city = row["Start City"]
        end_city = row["End City"]
        mpg = float(row.get("MP (Optional Will Default to 9)", 9.0)) if pd.notna(row.get("MP (Optional Will Default to 9)", 9.0)) else 9.0
        trips = int(row["Annual Trips"])

        if trips < 1:
            return f"<h2>Error: Row {index+2} has Annual Trips less than 1.</h2>"

        try:
            start_coord = geocode_city(start_city)
            end_coord = geocode_city(end_city)
            miles = calculate_distance(start_coord, end_coord)
            annual_miles = miles * trips

            # Diesel Calculations
            diesel_fuel_cost = trips * (miles / mpg) * diesel_price
            diesel_maintenance = miles * (17500 / annual_miles)
            diesel_depreciation = miles * (16600 / 750000)
            diesel_total_cost = diesel_fuel_cost + diesel_maintenance + diesel_depreciation
            diesel_emissions = (annual_miles * 1.617) / 1000

            # EV Calculations
            ev_feasible = check_ev_feasibility(start_coord, end_coord)
            if ev_feasible:
                ev_fuel_cost = (annual_miles / 20.39) * 2.208
                ev_maintenance = miles * (10500 / annual_miles)
                ev_depreciation = annual_miles * (250000 / 750000)
                ev_total_cost = ev_fuel_cost + ev_maintenance + ev_depreciation
                ev_emissions = (annual_miles * 0.2102) / 1000
            else:
                ev_total_cost = ev_emissions = "N/A"

            output_rows.append({
                "Start City": start_city,
                "End City": end_city,
                "MPG Used": mpg,
                "Diesel Miles": round(miles, 1),
                "Annual Trips": trips,
                "Annual Miles": round(annual_miles, 1),
                "EV Feasible": "Yes" if ev_feasible else "No",
                "Diesel Cost ($)": round(diesel_total_cost, 2),
                "Diesel Emissions (MT)": round(diesel_emissions, 2),
                "EV Cost ($)": round(ev_total_cost, 2) if ev_feasible else "N/A",
                "EV Emissions (MT)": round(ev_emissions, 2) if ev_feasible else "N/A"
            })

        except Exception as e:
            return f"<h2>Error processing row {index+2}: {str(e)}</h2>"

    result_df = pd.DataFrame(output_rows)
    result_path = "static/route_results_batch.xlsx"
    result_df.to_excel(result_path, index=False)

    return render_template("batch_result.html", count=len(output_rows))

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
