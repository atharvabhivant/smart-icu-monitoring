# Smart ICU Monitoring deployment

This application is a Flask dashboard backed by Firebase Realtime Database and
a scikit-learn model for vital-sign classification.

## Local setup

1. Use Python 3.13 and install dependencies with `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` and configure local values.
3. Keep the Firebase service-account file at `firebase-key.json` locally. Do
   not commit it.
4. Start locally with:

   ```text
   gunicorn --bind 127.0.0.1:5000 app:app
   ```

## Render setup

The root `render.yaml` defines one Render Web Service. Firebase remains the
external datastore; no Render database is required.

After creating the Blueprint, configure these values in Render:

- `FIREBASE_CREDENTIALS_BASE64`: base64-encoded contents of the Firebase
  service-account JSON file, stored as a secret environment variable.
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH`
- `ALERT_EMAIL_TO`
- `ALERT_FROM_EMAIL`
- `EMAIL_PROVIDER_API_KEY`

The default email payload targets the Resend API. Warning and Critical alerts
are deduplicated with cooldown state stored in Firebase.

## Safety note

This is a monitoring prototype and is not a replacement for clinical judgment,
validated medical devices, or hospital alerting procedures.
