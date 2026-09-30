// ==========================================
// AI SMART ICU MONITORING SYSTEM
// app.js - Part 1
// ==========================================

// Live Clock
function updateClock() {

    const now = new Date();

    document.getElementById("time").innerHTML =
        now.toLocaleString();

}

setInterval(updateClock, 1000);

updateClock();


// ----------------------------
// ECG Chart
// ----------------------------

const ctx = document.getElementById("ecgChart").getContext("2d");

let ecgChart = new Chart(ctx, {

    type: "line",

    data: {

        labels: [],

        datasets: [

            {

                label: "ECG",

                data: [],

                borderColor: "#00ff99",

                borderWidth: 2,

                pointRadius: 0,

                tension: 0.3

            }

        ]

    },

    options: {

        responsive: true,

        animation: false,

        plugins: {

            legend: {

                display: false

            }

        },

        scales: {

            x: {

                display: false

            },

            y: {

                display: false

            }

        }

    }

});


// ----------------------------
// Fetch Live Data
// ----------------------------

async function loadPatientData() {

    try {

        const response = await fetch("/data");
        const data = await response.json();

        // ==========================================
// REAL ECG DATA FROM FIREBASE
// ==========================================

if (Array.isArray(data.ECGBuffer) && data.ECGBuffer.length > 0) {

    const samples = data.ECGBuffer
        .map(Number)
        .filter(value => !isNaN(value));

    if (samples.length > 0) {

        ecgChart.data.labels = samples.map(() => "");

        ecgChart.data.datasets[0].data = samples;

        ecgChart.update("none");
    }
}

        // Temporary test
       

        document.getElementById("patient_name").innerHTML = data.PatientName;
        document.getElementById("patient_age").innerHTML = data.Age + " Years";
        document.getElementById("bed").innerHTML = data.Bed;
        document.getElementById("doctor").innerHTML = data.Doctor;
        document.getElementById("heart_rate").innerHTML = data.HeartRate + " BPM";
        document.getElementById("spo2").innerHTML = data.SpO2 + "%";
        document.getElementById("temperature").innerHTML = data.Temperature + "°C";
        document.getElementById("prediction").innerHTML = data.Prediction;
        document.getElementById("alert").innerHTML = data.Status;

        let alertBox = document.getElementById("criticalAlert");

        if (data.Prediction === "Critical") {

            alertBox.innerHTML = `
                <div class="alert-critical">
                    🚨 CRITICAL PATIENT 🚨<br>
                    Immediate Medical Attention Required
                </div>
            `;

            const alarm = document.getElementById("alarm");

alarm.play().catch(function(error) {
    console.log("Alarm blocked by browser");
});

        }
        else if (data.Prediction === "Warning") {

            alertBox.innerHTML = `
                <div class="alert alert-warning mt-3">
                    ⚠️ WARNING : Patient Needs Observation
                </div>
            `;

            document.getElementById("alarm").pause();
            document.getElementById("alarm").currentTime = 0;

        }
        else {

            alertBox.innerHTML = "";

            document.getElementById("alarm").pause();
            document.getElementById("alarm").currentTime = 0;

        }

    }
    catch (error) {

        console.log(error);

    }

}

setInterval(loadPatientData, 1000);

loadPatientData();