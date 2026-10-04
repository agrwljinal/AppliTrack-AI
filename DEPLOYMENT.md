# Render Deployment

## 1. Push the project to GitHub

Ensure `credentials.json`, `.env`, and other secrets are not committed. The repository's `.gitignore` excludes the local credential file and environment file. Commit and push the application code to the GitHub repository and branch you intend to deploy:

```bash
git add main.py api services cli agent requirements.txt render.yaml Procfile DEPLOYMENT.md
git commit -m "Prepare AppliTrack AI for Render"
git push origin main
```

Use your deployment branch name in place of `main` if it differs.

## 2. Create the Render Web Service

1. Sign in to Render and choose **New +** > **Blueprint**.
2. Connect the GitHub repository and select the deployment branch.
3. Render reads `render.yaml` to install dependencies, start both servers, and use `/_stcore/health` for health checks.
4. Create the service and wait for the first deployment to finish.

The service starts two processes in one container: FastAPI on `127.0.0.1:8000` for public API consumers, and Streamlit on `$PORT`, which is the only process Render routes external traffic to. The dashboard reads the Google Sheet and the Gmail connection state in-process, and now builds the Google OAuth consent URL itself, so connecting Gmail needs no reachable FastAPI endpoint.

Google sends the OAuth callback to `https://applitrack-ai.onrender.com/auth/google/callback`, which lands on Streamlit. Streamlit serves the app for that path, and `app.py` reads `code` and `state` from `st.query_params` to finish the handshake, so this URI must stay registered with Google. The callback URI is unchanged from earlier deployments; only the process that handles it moved.

You can also create a Web Service directly from the repository using the same build command (`pip install -r requirements.txt`) and the start command from `Procfile`.

## 3. Set environment variables

In the Render service's **Environment** settings, set:

- `GEMINI_API_KEY`: your Google Gemini API key.
- `GOOGLE_SHEETS_CREDENTIALS_JSON`: the Google service-account JSON, either pasted as JSON or base64-encoded JSON.
- `GOOGLE_CLIENT_ID`: OAuth client ID from a Google Cloud project with the Gmail API enabled.
- `GOOGLE_CLIENT_SECRET`: OAuth client secret for the same client.
- `GOOGLE_REDIRECT_URI`: OAuth redirect URI. Defaults to `https://applitrack-ai.onrender.com/auth/google/callback`; add it verbatim to the OAuth client's authorized redirect URIs. Google redirects here after consent and Streamlit handles it, so do not change it without re-registering the new URI with Google.
- `STREAMLIT_UI_URL`: where the FastAPI `/auth/google/callback` endpoint sends the browser when it is called directly. Defaults to `http://localhost:8501`. The dashboard does not use this path.
- `APPLITRACK_TOKEN_KEY`: optional Fernet key used to encrypt stored Gmail tokens at rest. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Without it, tokens are stored unencrypted and a warning is logged.
- `APPLITRACK_GMAIL_POLL_SECONDS`: background ingestion interval. Defaults to 300 seconds, minimum 60.

For base64, encode the contents of the service-account JSON file without adding line breaks. In PowerShell, for example:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
```

Paste the resulting value into Render as `GOOGLE_SHEETS_CREDENTIALS_JSON`. Keep these values in Render's environment settings; never commit them to GitHub. The service account identified by the JSON must be granted access to each client spreadsheet. The `/webhook/application` endpoint receives each target spreadsheet ID in the request's `sheet_id` field.

After setting the variables, redeploy if needed. The Streamlit dashboard is served at `https://<your-render-app>.onrender.com` and reports its own health at `/_stcore/health`. The FastAPI service listens on port 8000 inside the container.