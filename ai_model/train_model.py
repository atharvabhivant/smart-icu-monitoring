import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import joblib

# Load Dataset
data = pd.read_csv("dataset.csv")

# Input Features
X = data[["HeartRate", "SpO2", "Temperature"]]

# Output Labels
y = data["Condition"]

# Create Model
model = RandomForestClassifier(
    n_estimators=100,
    random_state=42
)

# Train Model
model.fit(X, y)

# Save Model
joblib.dump(model, "icu_model.pkl")

print("===================================")
print("AI Model Trained Successfully")
print("Model Saved as icu_model.pkl")
print("===================================")