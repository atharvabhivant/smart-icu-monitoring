from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from secrets import compare_digest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import firebase_admin
import joblib
from dotenv import load_dotenv
from firebase_admin import credentials, db
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from werkzeug.security import check_password_hash


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
APP_ENV = os.getenv("APP_ENV", "development").lower()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(value, minimum)
    return value


FIREBASE_DATABASE_URL = os.getenv(
    "FIREBASE_DATABASE_URL",
    "https://ai-smart-icu-monitoring-default-rtdb.asia-southeast1.firebasedatabase.app/",
)
FIREBASE_CREDENTIALS_PATH = Path(
    os.getenv("FIREBASE_CREDENTIALS_PATH", str(BASE_DIR / "firebase-key.json"))
)
MODEL_PATH = BASE_DIR / "ai_model" / "icu_model.pkl"


def initialize_firebase() -> None:
    if not FIREBASE_CREDENTIALS_PATH.is_file():
        raise RuntimeError(
            "Firebase credentials were not found. Set FIREBASE_CREDENTIALS_PATH "
            "to a local service-account file or a Render secret file."
        )
    if not FIREBASE_DATABASE_URL:
        raise RuntimeError("FIREBASE_DATABASE_URL must be configured.")

    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(
            credentials.Certificate(str(FIREBASE_CREDENTIALS_PATH)),
            {"databaseURL": FIREBASE_DATABASE_URL},
        )


initialize_firebase()

if not MODEL_PATH.is_file():
    raise RuntimeError(f"AI model was not found at {MODEL_PATH}")
model = joblib.load(MODEL_PATH)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "local-development-only-secret"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=env_bool("SESSION_COOKIE_SECURE", APP_ENV == "production"),
    SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
HISTORY_INTERVAL_SECONDS = env_int("HISTORY_INTERVAL_SECONDS", 30, minimum=5)
ALERT_COOLDOWN_SECONDS = env_int("ALERT_COOLDOWN_SECONDS", 900, minimum=60)
ALERT_LEVELS = {"Critical", "Warning"}
_email_config_warning_logged = False

if APP_ENV == "production":
    if not os.getenv("FLASK_SECRET_KEY"):
        raise RuntimeError("FLASK_SECRET_KEY must be configured in production.")
    if not ADMIN_USERNAME or not (ADMIN_PASSWORD or ADMIN_PASSWORD_HASH):
        raise RuntimeError(
            "Configure ADMIN_USERNAME and either ADMIN_PASSWORD or ADMIN_PASSWORD_HASH."
        )

app.logger.setLevel(logging.INFO)


def credentials_are_valid(username: str, password: str) -> bool:
    if not ADMIN_USERNAME or not password:
        return False
    if not compare_digest(username, ADMIN_USERNAME):
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, password)
        except ValueError:
            app.logger.exception("ADMIN_PASSWORD_HASH is invalid.")
            return False
    return compare_digest(password, ADMIN_PASSWORD)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("logged_in"):
            return view(*args, **kwargs)

        if request.path in {"/data", "/hospital_data"} or request.path.startswith(
            "/api/"
        ):
            return jsonify({"error": "authentication required"}), 401
        if request.path == "/download":
            return jsonify({"error": "authentication required"}), 401
        return redirect(url_for("login"))

    return wrapped_view


def to_float(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def evaluate_vitals(heart_rate: float, spo2: float, temperature: float):
    hr_valid = 40 <= heart_rate <= 180
    spo2_valid = 70 <= spo2 <= 100
    temp_valid = 30 <= temperature <= 45
    all_sensors_valid = hr_valid and spo2_valid and temp_valid

    if not all_sensors_valid:
        return (
            "Sensor Data Incomplete",
            "Waiting for valid sensor readings",
            {"HeartRate": hr_valid, "SpO2": spo2_valid, "Temperature": temp_valid},
            False,
        )

    prediction = str(model.predict([[heart_rate, spo2, temperature]])[0])
    status = {
        "Critical": "Immediate Medical Attention Required",
        "Warning": "Patient Needs Observation",
    }.get(prediction, "Patient Stable")
    return (
        prediction,
        status,
        {"HeartRate": True, "SpO2": True, "Temperature": True},
        True,
    )


def read_patient_data() -> dict:
    data = db.reference("Patient1").get() or {}
    if not isinstance(data, dict):
        data = {}

    ecg_buffer = db.reference("Patient1/ECGBuffer").get()
    if isinstance(ecg_buffer, dict):
        ecg_buffer = list(ecg_buffer.values())
    data["ECGBuffer"] = ecg_buffer if isinstance(ecg_buffer, list) else []
    return data


def save_history(
    heart_rate: float,
    spo2: float,
    temperature: float,
    ecg: float,
    prediction: str,
) -> None:
    # A deterministic time bucket makes repeated dashboard polls idempotent and
    # avoids relying on a process-local timer when Gunicorn restarts or scales.
    bucket = int(time.time()) // HISTORY_INTERVAL_SECONDS
    recorded_at = datetime.now(timezone.utc).isoformat()
    db.reference("PatientHistory").child(str(bucket)).set(
        {
            "RecordedAt": recorded_at,
            "HeartRate": heart_rate,
            "SpO2": spo2,
            "Temperature": temperature,
            "ECG": ecg,
            "Prediction": prediction,
        }
    )


def alert_email_settings() -> tuple[list[str], str, str] | None:
    recipients = [
        item.strip()
        for item in os.getenv("ALERT_EMAIL_TO", "").split(",")
        if item.strip()
    ]
    sender = os.getenv("ALERT_FROM_EMAIL", "").strip()
    api_key = os.getenv("EMAIL_PROVIDER_API_KEY", "").strip()
    if not recipients or not sender or not api_key:
        return None
    return recipients, sender, api_key


def send_alert_email(
    prediction: str,
    status: str,
    patient: dict,
    heart_rate: float,
    spo2: float,
    temperature: float,
) -> bool:
    global _email_config_warning_logged
    settings = alert_email_settings()
    if settings is None:
        if not _email_config_warning_logged:
            app.logger.warning(
                "Alert email skipped: configure ALERT_EMAIL_TO, ALERT_FROM_EMAIL, "
                "and EMAIL_PROVIDER_API_KEY."
            )
            _email_config_warning_logged = True
        return False

    recipients, sender, api_key = settings
    patient_name = str(patient.get("PatientName", "Unknown patient"))
    bed = str(patient.get("Bed", "Unknown bed"))
    subject = f"Smart ICU {prediction} alert - {bed}"
    body = "\n".join(
        [
            "Smart ICU Monitoring Alert",
            "",
            f"Alert level: {prediction}",
            f"Status: {status}",
            f"Patient: {patient_name}",
            f"Bed: {bed}",
            f"Heart rate: {heart_rate} BPM",
            f"SpO2: {spo2}%",
            f"Temperature: {temperature} C",
            f"Time (UTC): {datetime.now(timezone.utc).isoformat()}",
            "",
            "Please verify this alert using the clinical monitoring workflow.",
        ]
    )

    # The default payload targets Resend's API. A compatible provider endpoint
    # can be supplied for a provider with the same JSON contract.
    endpoint = os.getenv("EMAIL_PROVIDER_URL", "https://api.resend.com/emails")
    payload = json.dumps(
        {"from": sender, "to": recipients, "subject": subject, "text": body}
    ).encode("utf-8")
    request_object = Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "smart-icu-monitoring",
        },
        method="POST",
    )

    try:
        with urlopen(request_object, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"email provider returned HTTP {response.status}")
        return True
    except (HTTPError, URLError, TimeoutError, RuntimeError):
        app.logger.exception("Alert email delivery failed.")
        return False


def maybe_send_alert(
    patient: dict,
    prediction: str,
    status: str,
    heart_rate: float,
    spo2: float,
    temperature: float,
) -> None:
    state_ref = db.reference("AlertState/Patient1")
    now = time.time()

    try:
        previous_state = state_ref.get() or {}
        if not isinstance(previous_state, dict):
            previous_state = {}

        if prediction not in ALERT_LEVELS:
            if previous_state.get("prediction") != prediction:
                state_ref.set(
                    {
                        "prediction": prediction,
                        "last_sent_at": 0,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            return

        previous_prediction = previous_state.get("prediction")
        last_sent_at = float(previous_state.get("last_sent_at") or 0)
        if (
            previous_prediction == prediction
            and now - last_sent_at < ALERT_COOLDOWN_SECONDS
        ):
            return

        if not send_alert_email(
            prediction, status, patient, heart_rate, spo2, temperature
        ):
            return

        state_ref.set(
            {
                "prediction": prediction,
                "last_sent_at": now,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception:
        # An alert failure must not take down the live monitoring endpoint.
        app.logger.exception("Unable to evaluate or persist alert state.")


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if credentials_are_valid(username, password):
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("home"))
        return "Invalid Username or Password", 401
    return render_template("login.html")


@app.route("/home")
@login_required
def home():
    return render_template("home.html")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html")


@app.route("/hospital")
@login_required
def hospital():
    return render_template("hospital_dashboard.html")


@app.route("/patient")
@login_required
def patient():
    return render_template("patient_details.html")


@app.route("/history")
@login_required
def history():
    try:
        data = db.reference("PatientHistory").get() or {}
        history_list = []
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue
                history_list.append(
                    {
                        "time": value.get("RecordedAt", key),
                        "HeartRate": value.get("HeartRate", ""),
                        "SpO2": value.get("SpO2", ""),
                        "Temperature": value.get("Temperature", ""),
                        "Prediction": value.get("Prediction", ""),
                    }
                )
        history_list.sort(key=lambda item: str(item["time"]), reverse=True)
        return render_template("history.html", history=history_list)
    except Exception:
        app.logger.exception("Unable to load patient history.")
        return "Patient history is temporarily unavailable", 503


@app.route("/data")
@login_required
def get_data():
    try:
        data = read_patient_data()
        heart_rate = to_float(data.get("HeartRate"))
        spo2 = to_float(data.get("SpO2"))
        temperature = to_float(data.get("Temperature"))
        ecg = to_float(data.get("ECG"))
        prediction, status, valid_readings, all_sensors_valid = evaluate_vitals(
            heart_rate, spo2, temperature
        )
        ecg_leads_off = ecg in {0, 4095}

        sensor_status = {
            "HeartRate": "OK"
            if valid_readings["HeartRate"]
            else "Invalid / No reliable reading",
            "SpO2": "OK"
            if valid_readings["SpO2"]
            else "Finger not detected / Invalid reading",
            "Temperature": "OK"
            if valid_readings["Temperature"]
            else "Invalid / Ambient temperature",
            "ECG": "Leads Connected" if not ecg_leads_off else "Leads Off",
        }

        data.update(
            {
                "HeartRate": heart_rate,
                "SpO2": spo2,
                "Temperature": temperature,
                "ECG": ecg,
                "Prediction": prediction,
                "Status": status,
                "SensorStatus": sensor_status,
            }
        )

        if all_sensors_valid:
            try:
                save_history(heart_rate, spo2, temperature, ecg, prediction)
            except Exception:
                app.logger.exception("Unable to save patient history.")

        maybe_send_alert(data, prediction, status, heart_rate, spo2, temperature)

        return jsonify(data)
    except Exception:
        app.logger.exception("Unable to load live patient data.")
        return jsonify({"error": "patient data is temporarily unavailable"}), 503


@app.route("/download")
@login_required
def download_report():
    try:
        data = read_patient_data()
        heart_rate = to_float(data.get("HeartRate"))
        spo2 = to_float(data.get("SpO2"))
        temperature = to_float(data.get("Temperature"))
        prediction, status, _, _ = evaluate_vitals(
            heart_rate, spo2, temperature
        )

        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(110, 800, "AI SMART ICU MONITORING SYSTEM")
        pdf.setFont("Helvetica", 12)

        y = 760
        fields = [
            ("Patient Name", data.get("PatientName", "")),
            ("Age", data.get("Age", "")),
            ("Doctor", data.get("Doctor", "")),
            ("Bed", data.get("Bed", "")),
            ("Heart Rate", f"{heart_rate} BPM"),
            ("SpO2", f"{spo2} %"),
            ("Temperature", f"{temperature} C"),
            ("Prediction", prediction),
            ("Status", status),
        ]
        for label, value in fields:
            pdf.drawString(50, y, f"{label} : {value}")
            y -= 25
        pdf.drawString(
            50,
            y - 15,
            "Generated On (UTC) : "
            + datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M:%S"),
        )
        pdf.save()
        output.seek(0)

        return send_file(
            output,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="Patient_Report.pdf",
        )
    except Exception:
        app.logger.exception("Unable to generate patient report.")
        return "Patient report is temporarily unavailable", 503


@app.route("/hospital_data")
@login_required
def hospital_data():
    try:
        patient = read_patient_data()
        patient_name = patient.get("PatientName", "No Patient")
        bed_id = patient.get("Bed", "ICU-01")
        heart_rate = to_float(patient.get("HeartRate"))
        spo2 = to_float(patient.get("SpO2"))
        temperature = to_float(patient.get("Temperature"))
        prediction, status, _, _ = evaluate_vitals(
            heart_rate, spo2, temperature
        )
        if prediction == "Sensor Data Incomplete":
            status = "Warning"

        maybe_send_alert(
            patient, prediction, status, heart_rate, spo2, temperature
        )

        total_beds = env_int("TOTAL_BEDS", 10, minimum=1)
        beds = {
            str(bed_id): {
                "Name": patient_name,
                "HeartRate": heart_rate,
                "SpO2": spo2,
                "Temperature": temperature,
                "Status": status,
                "Prediction": prediction,
            }
        }

        return jsonify(
            {
                "totalBeds": total_beds,
                "availableBeds": max(total_beds - 1, 0),
                "stablePatients": int(status == "Stable"),
                "warningPatients": int(status == "Warning"),
                "criticalPatients": int(status == "Critical"),
                "beds": beds,
            }
        )
    except Exception:
        app.logger.exception("Unable to load hospital dashboard data.")
        return jsonify({"error": "hospital data is temporarily unavailable"}), 503


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=env_int("PORT", 5000, minimum=1),
        debug=APP_ENV != "production",
    )
