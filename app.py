from flask import Flask, render_template, jsonify, send_file, request, redirect, url_for, session

import firebase_admin
from firebase_admin import credentials, db

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import datetime
import time

import joblib
import os

# -----------------------------
# Firebase Initialization
# -----------------------------

cred = credentials.Certificate("firebase-key.json")

firebase_admin.initialize_app(cred, {
    "databaseURL": "https://ai-smart-icu-monitoring-default-rtdb.asia-southeast1.firebasedatabase.app/"
})

# -----------------------------
# Load AI Model
# -----------------------------

model = joblib.load(os.path.join("ai_model", "icu_model.pkl"))

print("AI MODEL CLASSES:", model.classes_)

app = Flask(__name__)
app.secret_key = "smarticu_secret_key"

last_saved_time = 0
SAVE_INTERVAL = 30

# -----------------------------
# Home Page
# -----------------------------

@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        # Default Login
        if username == "admin" and password == "1234":

            session["logged_in"] = True
            return redirect(url_for("home"))

        return "Invalid Username or Password"

    return render_template("login.html")



@app.route("/home")
def home():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template("home.html")



@app.route("/dashboard")
def dashboard():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template("index.html")

    # -----------------------------
# Hospital Dashboard
# -----------------------------
@app.route("/hospital")
def hospital():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template("hospital_dashboard.html")

@app.route("/patient")
def patient():
    return render_template("patient_details.html")

# ------------------------------
# Patient History Page
# ------------------------------

@app.route("/history")
def history():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    ref = db.reference("PatientHistory")

    data = ref.get()

    if data is None:
        data = {}

    history_list = []

    for key, value in data.items():

        history_list.append({
            "time": key,
            "HeartRate": value.get("HeartRate", ""),
            "SpO2": value.get("SpO2", ""),
            "Temperature": value.get("Temperature", ""),
            "Prediction": value.get("Prediction", "")
        })

    history_list.reverse()

    return render_template(
        "history.html",
        history=history_list
    )

    ref = db.reference("PatientHistory")

    data = ref.get()

    if data is None:
        data = {}

    history_list = []

    for key, value in data.items():

        history_list.append({
            "time": key,
            "HeartRate": value.get("HeartRate", ""),
            "SpO2": value.get("SpO2", ""),
            "Temperature": value.get("Temperature", ""),
            "Prediction": value.get("Prediction", "")
        })

    history_list.reverse()

    return render_template(
        "history.html",
        history=history_list
    )
# -----------------------------
# Live Data API
# -----------------------------

@app.route("/data")
def get_data():

    global last_saved_time

    # =====================================================
    # Read data from Firebase
    # =====================================================

    ref = db.reference("Patient1")
    data = ref.get()

    if not data:
        data = {}

    # Read ECG buffer
    ecg_ref = db.reference("Patient1/ECGBuffer")
    ecg_buffer = ecg_ref.get()

    if ecg_buffer is not None:
        data["ECGBuffer"] = ecg_buffer
    else:
        data["ECGBuffer"] = []

    # =====================================================
    # Get sensor values
    # =====================================================

    hr = data.get("HeartRate", 0)
    spo2 = data.get("SpO2", 0)
    temp = data.get("Temperature", 0)
    ecg = data.get("ECG", 0)

    # Convert values safely

    try:
        hr = float(hr)
    except:
        hr = 0

    try:
        spo2 = float(spo2)
    except:
        spo2 = 0

    try:
        temp = float(temp)
    except:
        temp = 0

    try:
        ecg = float(ecg)
    except:
        ecg = 0

    # =====================================================
    # SENSOR VALIDATION
    # =====================================================

    hr_valid = (
        hr >= 40 and
        hr <= 180
    )

    spo2_valid = (
        spo2 >= 70 and
        spo2 <= 100
    )

    temp_valid = (
        temp >= 30 and
        temp <= 45
    )

    # ECG lead status

    ecg_leads_off = (
        ecg == 0 or
        ecg == 4095
    )

    # =====================================================
    # CHECK WHETHER ALL REQUIRED PATIENT DATA IS VALID
    # =====================================================

    all_sensors_valid = (
        hr_valid and
        spo2_valid and
        temp_valid
    )

    # =====================================================
    # AI PREDICTION
    # =====================================================

    if all_sensors_valid:

        prediction = model.predict(
            [[hr, spo2, temp]]
        )[0]

        if prediction == "Critical":

            status = (
                "Immediate Medical Attention Required"
            )

        elif prediction == "Warning":

            status = (
                "Patient Needs Observation"
            )

        else:

            status = (
                "Patient Stable"
            )

    else:

        prediction = "Sensor Data Incomplete"

        status = (
            "Waiting for valid sensor readings"
        )

    # =====================================================
    # SENSOR STATUS INFORMATION
    # =====================================================

    sensor_status = {

        "HeartRate": (
            "OK" if hr_valid
            else "Invalid / No reliable reading"
        ),

        "SpO2": (
            "OK" if spo2_valid
            else "Finger not detected / Invalid reading"
        ),

        "Temperature": (
            "OK" if temp_valid
            else "Invalid / Ambient temperature"
        ),

        "ECG": (
            "Leads Connected"
            if not ecg_leads_off
            else "Leads Off"
        )
    }

    # =====================================================
    # Update returned data
    # =====================================================

    data["HeartRate"] = hr
    data["SpO2"] = spo2
    data["Temperature"] = temp
    data["ECG"] = ecg
    data["Prediction"] = prediction
    data["Status"] = status
    data["SensorStatus"] = sensor_status

    # =====================================================
    # Save history ONLY when patient data is valid
    # =====================================================

    current = time.time()

    if (
        all_sensors_valid and
        current - last_saved_time >= SAVE_INTERVAL
    ):

        history_ref = db.reference(
            "PatientHistory"
        )

        current_time = datetime.now().strftime(
            "%d-%m-%Y %H:%M:%S"
        )

        history_ref.child(current_time).set({

            "HeartRate": hr,
            "SpO2": spo2,
            "Temperature": temp,
            "ECG": ecg,
            "Prediction": prediction

        })

        last_saved_time = current

    # =====================================================
    # Return data to dashboard
    # =====================================================

    return jsonify(data)

# -----------------------------
# Download PDF
# -----------------------------

@app.route("/download")
def download_report():

    ref = db.reference("Patient1")
    data = ref.get()

    hr = data.get("HeartRate", 0)
    spo2 = data.get("SpO2", 0)
    temp = data.get("Temperature", 0)

    prediction = model.predict([[hr, spo2, temp]])[0]

    if prediction == "Critical":
        status = "Immediate Medical Attention Required"

    elif prediction == "Warning":
        status = "Patient Needs Observation"

    else:
        status = "Patient Stable"

    filename = "Patient_Report.pdf"

    pdf = canvas.Canvas(filename, pagesize=A4)

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(110, 800, "AI SMART ICU MONITORING SYSTEM")

    pdf.setFont("Helvetica", 12)

    y = 760

    pdf.drawString(50, y, f"Patient Name : {data.get('PatientName','')}")
    y -= 25

    pdf.drawString(50, y, f"Age : {data.get('Age','')}")
    y -= 25

    pdf.drawString(50, y, f"Doctor : {data.get('Doctor','')}")
    y -= 25

    pdf.drawString(50, y, f"Bed : {data.get('Bed','')}")
    y -= 40

    pdf.drawString(50, y, f"Heart Rate : {hr} BPM")
    y -= 25

    pdf.drawString(50, y, f"SpO2 : {spo2} %")
    y -= 25

    pdf.drawString(50, y, f"Temperature : {temp} °C")
    y -= 25

    pdf.drawString(50, y, f"Prediction : {prediction}")
    y -= 25

    pdf.drawString(50, y, f"Status : {status}")
    y -= 40

    pdf.drawString(
        50,
        y,
        "Generated On : " +
        datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    )

    pdf.save()

    return send_file(filename, as_attachment=True)

# -----------------------------
# Run Flask
# -----------------------------

# -----------------------------
# Hospital Dashboard Data API
# -----------------------------
@app.route("/hospital_data")
def hospital_data():

    patient_ref = db.reference("Patient1")
    patient = patient_ref.get()

    if not patient:
        patient = {}

    # Patient information
    patient_name = patient.get("PatientName", "No Patient")
    heart_rate = patient.get("HeartRate", 0)
    spo2 = patient.get("SpO2", 0)
    temperature = patient.get("Temperature", 0)

    # Bed information
    bed_id = patient.get("Bed", "ICU-01")

    # Convert sensor values
    try:
        hr = float(heart_rate)
    except:
        hr = 0

    try:
        oxygen = float(spo2)
    except:
        oxygen = 0

    try:
        temp = float(temperature)
    except:
        temp = 0

    # Sensor validation
    hr_valid = 40 <= hr <= 180
    spo2_valid = 70 <= oxygen <= 100
    temp_valid = 30 <= temp <= 45

    all_sensors_valid = (
        hr_valid and
        spo2_valid and
        temp_valid
    )

    # AI prediction
    if all_sensors_valid:

        prediction = model.predict(
            [[hr, oxygen, temp]]
        )[0]

        if prediction == "Critical":
            status = "Critical"

        elif prediction == "Warning":
            status = "Warning"

        else:
            status = "Stable"

    else:

        prediction = "Sensor Data Incomplete"
        status = "Warning"

    # Create hospital bed data
    beds = {

        bed_id: {

            "Name": patient_name,

            "HeartRate": heart_rate,

            "SpO2": spo2,

            "Temperature": temperature,

            "Status": status,

            "Prediction": prediction

        }

    }

    # Hospital statistics
    totalBeds = 10

    availableBeds = totalBeds - 1

    stablePatients = 0
    warningPatients = 0
    criticalPatients = 0

    if status == "Stable":

        stablePatients = 1

    elif status == "Warning":

        warningPatients = 1

    elif status == "Critical":

        criticalPatients = 1

    return jsonify({

        "totalBeds": totalBeds,

        "availableBeds": availableBeds,

        "stablePatients": stablePatients,

        "warningPatients": warningPatients,

        "criticalPatients": criticalPatients,

        "beds": beds

    })

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)