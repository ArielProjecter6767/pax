# Android APK build

1. Create an Expo account at https://expo.dev and create an access token.
2. In GitHub, open Settings → Secrets and variables → Actions.
3. Add secret `EXPO_TOKEN` with the Expo access token.
4. Add variable `EXPO_PUBLIC_API_URL` with your Render API URL (for example `https://pax-api.onrender.com`, without a trailing slash).
5. First configure the Expo project locally or in an Expo cloud session: `npx eas-cli init`. Copy the generated project ID into `app.json` under `expo.extra.eas.projectId` and commit it.
6. Open Actions → Build PAX Android APK → Run workflow. EAS provides the build page and APK artifact/download when the build finishes.

The app config initially contains a placeholder EAS project ID. The workflow cannot complete until it is replaced with a real ID from your Expo project.
