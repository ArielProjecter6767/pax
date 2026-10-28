# PAX Messenger

PAX is a cross-platform messaging app project. The mobile client uses Expo/React Native and the backend uses FastAPI + MongoDB.

## Repository layout
- `App.js`, `app.json`, `package.json`, `eas.json`: Android app
- `backend/`: FastAPI API and WebSocket server
- `.github/workflows/build-android.yml`: optional cloud APK build

## Backend on Render
Create a **Web Service** connected to this repository:
- Root Directory: `backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Environment variables:
- `MONGODB_URI`: MongoDB Atlas connection string
- `MONGODB_DB`: `pax`
- `JWT_SECRET`: a long random secret (at least 32 characters)
- `CORS_ORIGINS`: `*` for initial testing; restrict later
- `APP_ENV`: `production`

Never commit real secrets to GitHub.

## Android APK
The app is configured for an installable APK via Expo EAS Build. Set `EXPO_PUBLIC_API_URL` in the Expo/EAS environment to the Render service URL, for example `https://YOUR-SERVICE.onrender.com` (no trailing slash).
For manual cloud build, use EAS Build with the `preview` profile. For the GitHub Actions workflow, add the repository secret `EXPO_TOKEN`.

## Current scope
The initial version implements account registration/login, user search/list, one-to-one conversations, persistent messages, and WebSocket message delivery. Groups, channels, file upload, push notifications, and voice/video calling require additional implementation and provider configuration before they can be considered production-ready.
