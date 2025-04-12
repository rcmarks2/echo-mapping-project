from flask import Flask, render_template, request, send_file
from geopy.geocoders import GoogleV3
import pandas as pd
import requests
import os
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

app = Flask(__name__, template_folder="templates", static_folder="static")

google_api_key = "AIzaSyCIPvsZMeb_NtkuElOooPCE46fB-bJEULg"
geolocator = GoogleV3(api_key=google_api_key, timeout=10)

def geocode_city_state(city, state):
    location = geolocator.geocode(f"{city}, {state}")
    if not location:
        raise ValueError(f"Could not geocode {city}, {state}")
    return (location.latitude, location.longitude)

def get_google_miles(start, end):
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
            return meters / 1609.34  # convert to miles
        else:
            raise ValueError(f"No route found between {start} and {end}")
    else:
        raise ValueError(f"Google Routes API error: {response.status_code}")

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

        start_coord = geocode_city_state(start_city.strip(), start_state.strip())
        end_coord = geocode_city_state(end_city.strip(), end_state.strip())

        diesel_miles = get_google_miles(start_coord, end_coord)
        diesel_annual_miles = diesel_miles * trips
        diesel_cost = trips * (diesel_miles / mpg) * 3.59 + diesel_miles * (17500 / diesel_annual_miles) + diesel_annual_miles * (16600 / 750000)
        diesel_emissions = (diesel_annual_miles * 1.617) / 1000

        return render_template("result.html",
            diesel_miles=round(diesel_miles, 1),
            annual_trips=trips,
            diesel_annual_miles=round(diesel_annual_miles, 1),
            diesel_total_cost=round(diesel_cost, 2),
            diesel_emissions=round(diesel_emissions, 2),
            ev_unavailable=True,
            ev_miles=None,
            ev_annual_miles=None,
            ev_total_cost=None,
            ev_emissions=None
        )
    except Exception as e:
        return f"<h3>Error in single route: {e}</h3>"

@app.route("/batch-result", methods=["POST"])
def batch_result():
    try:
        file = request.files["excel"]
        df = pd.read_excel(file)

        wb = Workbook()
        ws = wb.active
        ws.title = "Route Results"

        headers = [
            "Start City", "Start State", "Destination City", "Destination State",
            "Diesel Mileage (1 Trip)", "Annual Trips", "Diesel Total Mileage",
            "Diesel Total Cost", "Diesel Total Emissions", "EV Possible?",
            "EV Mileage (1 Trip)", "EV Total Mileage", "EV Total Cost", "EV Total Emissions"
        ]
        ws.append(headers)

        for index, row in df.iterrows():
            try:
                start_coord = geocode_city_state(row["Start City"], row["Start State"])
                end_coord = geocode_city_state(row["Destination City"], row["Destination State"])
                trips = int(row["Annual Trips"])
                if trips <= 0:
                    continue

                diesel_miles = get_google_miles(start_coord, end_coord)
                if diesel_miles <= 0:
                    continue

                diesel_total_miles = diesel_miles * trips
                diesel_cost = trips * (diesel_miles / 9) * 3.59 + diesel_miles * (17500 / diesel_total_miles) + diesel_total_miles * (16600 / 750000)
                diesel_emissions = (diesel_total_miles * 1.617) / 1000

                ev_possible = "Yes" if diesel_miles <= 225 else "No"
                if ev_possible == "Yes":
                    ev_total_miles = diesel_total_miles
                    ev_cost = (ev_total_miles / 20.39) * 2.208 + diesel_miles * (10500 / ev_total_miles) + ev_total_miles * (250000 / 750000)
                    ev_emissions = (ev_total_miles * 0.2102) / 1000
                else:
                    ev_total_miles = ev_cost = ev_emissions = "N/A"

                ws.append([
                    row["Start City"], row["Start State"],
                    row["Destination City"], row["Destination State"],
                    round(diesel_miles, 1), trips, round(diesel_total_miles, 1),
                    f"${diesel_cost:,.2f}", round(diesel_emissions, 2), ev_possible,
                    round(diesel_miles, 1) if ev_possible == "Yes" else "N/A",
                    round(ev_total_miles, 1) if ev_possible == "Yes" else "N/A",
                    f"${ev_cost:,.2f}" if ev_possible == "Yes" else "N/A",
                    round(ev_emissions, 2) if ev_possible == "Yes" else "N/A"
                ])
            except Exception as row_error:
                print(f"Skipping row {index + 2} due to error: {row_error}")
                continue

        header_fill = PatternFill(start_color="003366", end_color="003366", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(col)].width = 18

        for row in range(2, ws.max_row + 1):
            cell = ws.cell(row=row, column=10)
            if cell.value == "Yes":
                cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            elif cell.value == "No":
                cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

        wb.save("static/route_results_batch.xlsx")
        return render_template("batch_result.html", count=ws.max_row - 1)
    except Exception as e:
        return f"<h3>Error processing batch file: {e}</h3>"

@app.route("/download-batch")
def download_batch():
    return send_file("static/route_results_batch.xlsx", as_attachment=True)

@app.route("/download-formulas")
def download_formulas():
    return send_file("static/formulas.txt", as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
