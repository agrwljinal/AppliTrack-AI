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
3. Render reads `render.yaml` to install dependencies, start Uvicorn, and use `/health` for health checks.
4. Create the service and wait for the first deployment to finish.

You can also create a Web Service directly from the repository using the same build command (`pip install -r requirements.txt`) and start command (`uvicorn main:app --host 0.0.0.0 --port $PORT`).

## 3. Set environment variables

In the Render service's **Environment** settings, set:

- `GEMINI_API_KEY`: your Google Gemini API key.
- `GOOGLE_SHEETS_CREDENTIALS_JSON`: the Google service-account JSON, either pasted as JSON or base64-encoded JSON.
- `GOOGLE_CLIENT_ID`: OAuth client ID from a Google Cloud project with the Gmail API enabled.
- `GOOGLE_CLIENT_SECRET`: OAuth client secret for the same client.
- `GOOGLE_REDIRECT_URI`: OAuth redirect URI. Defaults to `https://applitrack-ai.onrender.com/auth/google/callback`; add it verbatim to the OAuth client's authorized redirect URIs.
- `STREAMLIT_UI_URL`: where `/auth/google/callback` sends the browser after connecting. Defaults to `http://localhost:8501`.
- `APPLITRACK_TOKEN_KEY`: optional Fernet key used to encrypt stored Gmail tokens at rest. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Without it, tokens are stored unencrypted and a warning is logged.
- `APPLITRACK_GMAIL_POLL_SECONDS`: background ingestion interval. Defaults to 300 seconds, minimum 60.

For base64, encode the contents of the service-account JSON file without adding line breaks. In PowerShell, for example:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
```

Paste the resulting value into Render as `GOOGLE_SHEETS_CREDENTIALS_JSON`. Keep these values in Render's environment settings; never commit them to GitHub. The service account identified by the JSON must be granted access to each client spreadsheet. The `/webhook/application` endpoint receives each target spreadsheet ID in the request's `sheet_id` field.

After setting the variables, redeploy if needed and verify the service at `https://<your-render-app>.onrender.com/health`. It should return `{"status":"healthy","service":"AppliTrack AI"}`.