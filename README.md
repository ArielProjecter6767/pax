# PAX Messenger

PAX is a cross-platform messenger project with a React Native mobile client, a Windows desktop client (Electron), and a FastAPI + MongoDB backend.

## Repository
- `App.js`, `app.json`, `package.json`, `eas.json`: existing Android/Expo app.
- `backend/`: FastAPI API and WebSocket signaling server.
- `windows/`: Windows desktop client with direct chats, group chats, message context actions, and one-to-one audio/video WebRTC calling.
- `.github/workflows/build-windows.yml`: builds Windows installer and portable executable in GitHub Actions.

## Backend on Render
Create a Web Service connected to this repository:
- Root Directory: `backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Environment variables:
- `MONGODB_URI`: MongoDB Atlas connection string
- `MONGODB_DB=pax`
- `JWT_SECRET`: a long random secret (at least 32 characters)
- `CORS_ORIGINS=*` for initial testing
- `APP_ENV=production`
- `LIBRETRANSLATE_URL`: optional base URL for a LibreTranslate-compatible translation service. Translation returns a configuration error if this is not set.

Never commit real secrets to GitHub.

## Windows build
1. Open the repository on GitHub and select branch `feature/groups-calls-windows`.
2. Open **Actions** and select **Build PAX for Windows**.
3. Click **Run workflow** and run it on `feature/groups-calls-windows`.
4. When it succeeds, open that workflow run and download the `PAX-Windows` artifact. It contains the Windows installer and portable build.

For local building, install Node.js 22 or newer, then run:
```powershell
cd windows
npm install
npm run dist
```
Output is created in `windows/dist/`.

On first launch, the Windows client asks for the public Render API URL if it has not been configured. Use the service's actual HTTPS URL, not a placeholder. The client stores the URL locally.

## Features in the feature branch
- User registration and login using the existing API.
- Direct messages and persistent group chats (up to 100 members).
- Message context menu (right-click or double-click): copy, translate to English, forward, edit your own message, and delete your own message for everyone.
- One-to-one audio/video calls using browser WebRTC and WebSocket signaling. Calls require microphone/camera permissions and a network that permits WebRTC traffic. A TURN server is not configured yet, so some networks may not connect.
- Translation requires a separately configured LibreTranslate-compatible endpoint.

## Important limitations
- These additions are in the `feature/groups-calls-windows` branch and have not been merged into `main`.
- The Windows client has the group/message features and calling UI. The existing Android app UI has not yet been updated to expose these new endpoints.
- Group audio/video calling, push notifications, TURN relay, and production-grade call lifecycle/reliability are not implemented.
- Test the branch and confirm the Render deployment before using it with real users.
