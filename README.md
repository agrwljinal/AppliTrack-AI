# 🎯 AppliTrack AI

> Stop maintaining a spreadsheet by hand.

AppliTrack AI connects to your Gmail with **read-only** access, watches for job
application emails, and writes each one to your own Google Sheet as a structured
row — company, role, status, and date. No scraping, no manual copy-paste, no
per-platform configuration.

It is **platform-agnostic by design**. There is no list of supported job boards in
the parsing logic, so it works with Unstop, LinkedIn, Indeed, Glassdoor, Lever,
Greenhouse, Workday, an employer running their own applicant tracking system, or
any platform that ships next month.

---

## 🌐 Live Demo

<!-- ============================================================
     EDIT BELOW: paste your deployed URL into DEPLOY_URL below.
     ============================================================ -->

**Open the app:** `DEPLOY_URL`

<!-- ============================================================
     To edit:
       1. Replace DEPLOY_URL with your deployed URL, e.g.
          https://applitrack-ai.onrender.com
       2. Or replace the whole line above with a markdown link:
          [Open the app](https://your-deployed-url)
     Note: GOOGLE_REDIRECT_URI must match this URL exactly, or
     Gmail connect fails with redirect_uri_mismatch. See
     "Environment variables" below.
     ============================================================ -->

---

## ✨ What it does

| | |
|---|---|
| 📥 **Reads your inbox** | Connects via Google OAuth with `gmail.readonly` scope. AppliTrack can never send, delete, or modify mail. |
| 🔎 **Finds application emails by intent** | Searches subject lines for what a confirmation *says*, not for who sent it. |
| 🧩 **Extracts the details** | Pulls Company, Role, Application Date, and Status out of unstructured text. |
| 🤖 **Classifies with Gemini** | Sends the email to `gemini-3.8-flash`; the local parser backfills anything the model leaves blank. Model ID is set by `MODEL_NAME` in `services/ai_classifier.py`. |
| 📊 **Writes to your Sheet** | Appends new rows, and updates in place when a status changes on an existing application. |
| 🔁 **Runs on a timer or on demand** | A background poller syncs every 5 minutes; **Sync Now** in the dashboard forces an immediate refresh. |
| 🔌 **Also exposes an API** | Push applications in over a webhook, or trigger syncs from your own tooling. |

### Status values

| Status | Meaning |
|---|---|
| `Applied` | The application was submitted or received. This is the default for acknowledgements. |
| `Selected` | An offer, acceptance, or hire. |
| `Rejected` | A refusal or withdrawal. |
| `Pending` | The email does not say how the application fared. |

---

## 🧠 How the parsing works

This is the part that makes AppliTrack work everywhere, so it is worth
understanding.

**1. Search by intent, not by sender.** The Gmail query looks at the subject line:

```
{subject:submitted subject:received subject:"thank you for applying"
 subject:"application confirmation"} -unsubscribe -"recommended opportunities"
-"job alert"
```

There is no `from:` filter and no hardcoded job-board domain. Note the `{a b}`
syntax: Gmail's API has no `OR` keyword and no parentheses grouping — braces are
its OR operator. The query also has no `is:unread`, on purpose: reading a
confirmation in Gmail must not stop it being logged. Duplicates are prevented
separately, per account, by a processed-message list.

**2. Extract generically.** `services/parser.py` matches ordinary English
phrasing rather than platform templates — *"we received your application at…"*,
*"your application to Globex"*, *"Role: Backend Engineer"*. It prefers precision
over recall and returns nothing rather than guessing, because a confidently
wrong company name is worse than an empty one.

Two rules deserve a note:

- **A platform is never an employer.** `noreply@emails.unstop.com` and
  `noreply@greenhouse.io` are deliberately rejected as company names, so
  "LinkedIn" can never end up in your Company column.
- **The sender display name is the fallback.** When neither subject nor body
  names an employer, `Acme Careers <jobs@acme.com>` yields `Acme`.

**3. Handle the awkward formats explicitly.** Unstop's subject is the one shape
generic rules cannot split:

```
Your application for Software Engineer at Acme Corp successfully submitted
```

The company is *after* the role, so a naive `application for …` rule captures
the role and files it as the employer. Unstop gets a dedicated rule ahead of the
generic ones.

**4. Classify.** `services/ai_classifier.py` sends the subject, sender, and body
to Gemini and validates the reply against a Pydantic schema. If no API key is
configured, or the call fails, it falls back to the local parser. Any field the
model leaves blank is filled from the parser; a field the model did identify is
never overwritten.

---

## 📋 Google Sheet schema

Created automatically on first write, in a tab named `Applications`:

| `company_name` | `role` | `platform_used` | `status` | `date_updated` |
|---|---|---|---|---|
| Acme Corp | Software Engineer | Unstop | Applied | 2026-09-28 |

Matching is on the first three columns. When a later email reports a new status
for the same company + role + platform, the existing row is **updated in place**
rather than duplicated.

---

## 🚀 Quick start (local)

```bash
git clone https://github.com/agrwljinal/AppliTrack-AI.git
cd AppliTrack-AI
python -m venv .venv
.venv\Scripts\activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run both processes in separate terminals:

```bash
# Terminal 1 — dashboard on http://localhost:8501
streamlit run app.py

# Terminal 2 — API on http://127.0.0.1:8000 (docs at /docs)
uvicorn main:app --host 127.0.0.1 --port 8000
```

Then set the environment variables listed below. A `.env` file in the project
root is read automatically by `python-dotenv`.

---

## 🔑 Setup

You need three things from Google Cloud. Full deployment steps are in
**[DEPLOYMENT.md](DEPLOYMENT.md)**.

### 1. A service account, for writing to Sheets

1. Create a service account in a Google Cloud project and download its JSON key.
2. Enable the **Google Sheets API**.
3. Share each target spreadsheet with the service account's `client_email` as
   **Editor**.
4. Provide the key as `GOOGLE_SHEETS_CREDENTIALS_JSON` — pasted JSON,
   base64-encoded JSON, or a file path all work.

### 2. An OAuth client, for reading Gmail

1. Enable the **Gmail API**.
2. Create an **OAuth client ID** of type *Web application*.
3. Add your redirect URI **exactly** as `GOOGLE_REDIRECT_URI`. Google rejects the
   exchange with `redirect_uri_mismatch` if they differ by even a trailing slash.
4. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

### 3. A Gemini API key

Set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey).
The default model is `gemini-3.8-flash`; change `MODEL_NAME` in
`services/ai_classifier.py` to use another. Without a key the pipeline still
runs, using the local parser only.

---

## ⚙️ Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Recommended | Enables AI classification. Without it, the local parser handles everything. |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Yes | Service-account key as JSON, base64 JSON, or a file path. Falls back to `./credentials.json`. |
| `GOOGLE_CLIENT_ID` | Yes | OAuth client ID for Gmail. |
| `GOOGLE_CLIENT_SECRET` | Yes | OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | Yes | Must match a registered redirect URI verbatim. Defaults to `https://applitrack-ai.onrender.com`. |
| `GOOGLE_SHEET_ID` | Optional | Default spreadsheet when a request omits `sheet_id`. |
| `APPLITRACK_TOKEN_KEY` | Recommended | Fernet key encrypting stored Gmail tokens at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Without it, tokens are stored unencrypted and a warning is logged. |
| `APPLITRACK_GMAIL_QUERY` | Optional | Overrides the Gmail search query shown above. |
| `APPLITRACK_GMAIL_POLL_SECONDS` | Optional | Background sync interval. Default `300`, minimum `60`. |
| `APPLITRACK_API_KEY` | Optional | When set, API endpoints require a matching `client_api_key`. When unset, they are open. |
| `STREAMLIT_UI_URL` | Optional | Where `/auth/google/callback` sends the browser. Default `http://localhost:8501`. |

> 🔐 Never commit `.env`, `credentials.json`, `gmail_tokens.json`, or
> `applitrack_config.json`. All are already in `.gitignore`.

---

## 🔌 API

Interactive docs at `/docs`. All endpoints accept an optional `client_api_key`
query parameter, enforced only when `APPLITRACK_API_KEY` is set.

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service banner. |
| `GET` | `/health` | Liveness check. |
| `GET` | `/auth/google/login?sheet_id=` | Redirects (307) to Google's consent screen. |
| `GET` | `/auth/google/callback?code=&state=` | OAuth redirect target. |
| `GET` | `/auth/google/status?sheet_id=` | Reports configured / connected / account email. |
| `POST` | `/sync/gmail?sheet_id=` | Runs Gmail ingestion now. Omit `sheet_id` to sync every account. |
| `GET` | `/applications?sheet_id=&limit=` | Most recent rows from the sheet (max 100). |
| `POST` | `/webhook/application` | Submit an application directly. |

### Submitting an application

```bash
curl --request POST \
  --url "DEPLOY_URL/webhook/application" \
  --header "Content-Type: application/json" \
  --data '{
    "sheet_id": "YOUR_GOOGLE_SHEET_ID",
    "applicant_data": {
      "company": "Acme",
      "role": "Software Engineer",
      "platform_used": "Typeform",
      "resume_text": "Applicant resume or application details"
    }
  }'
```

Send `applicant_data` or a non-empty `resume_text`; one is required. The response
echoes the parsed application.

#### Typeform via Make.com

Create a scenario with a **Typeform: Watch Responses** trigger followed by
**HTTP: Make a request**:

- Method: `POST`
- URL: `DEPLOY_URL/webhook/application`
- Header: `Content-Type: application/json`
- Body type: `Raw`, `application/json`

Replace the `{{...}}` placeholders by mapping the matching answer tokens:

```json
{
  "sheet_id": "YOUR_GOOGLE_SHEET_ID",
  "applicant_data": {
    "company": "{{1.company}}",
    "role": "{{1.role}}",
    "platform_used": "Typeform",
    "resume_text": "{{1.resume_text}}"
  }
}
```

---

## 🖥️ CLI status

The Typer CLI exists but is **a scaffold, not a working tracker**.
`agent/orchestrator.py` implements `scan_portal()` and `scan_email_updates()` as
stubs that log `not implemented yet` and return nothing, so `python main.py run`
currently finds zero updates regardless of configuration. Treat the Gmail path as
the product.

---

## 🧪 Tests

77 tests, no network access required:

```bash
python -m unittest tests.test_parser
python -m unittest tests.test_app_oauth_flow
python -m unittest tests.test_google_status_route
python -m unittest tests.test_oauth_login_route
python -m unittest tests.test_webhook
```

`pytest` is not currently usable in this environment — a stray `py.py` shadows
the real `py` package that `pytest` imports — so use `unittest`.

Parser tests are written from generic English phrasing. A platform name appears
only to prove that a platform is *not* mistaken for an employer.

---

## 🗂️ Project structure

```
app.py                     Streamlit dashboard (sheet setup, Gmail connect, Sync Now)
main.py                    FastAPI app, background poller startup, Typer CLI entry
services/
  gmail_service.py         Google OAuth, Gmail ingestion, token store, polling
  parser.py                Platform-agnostic extraction of company/role/date/status
  ai_classifier.py         Gemini classification with local-parser fallbacks
  sheet_service.py         Google Sheets read/write, JobApplication schema
api/routes.py              REST endpoints
agent/orchestrator.py     CLI cycle scaffold (scanners not implemented)
cli/onboarding.py          Interactive platform + privacy preferences
tests/                     unittest suites
render.yaml / Procfile     Render deployment (two processes, one container)
```

---

## 🔒 Privacy and security

- **Read-only Gmail.** The OAuth scope is `gmail.readonly`. AppliTrack cannot
  send, delete, or modify mail.
- **Your data stays in your sheet.** Nothing is written to any AppliTrack
  database; there isn't one. Rows go only to the spreadsheets you grant access.
- **Tokens encrypted at rest** when `APPLITRACK_TOKEN_KEY` is set.
- **Signed OAuth state** carries the target spreadsheet, HMAC-signed with the
  client secret and expiring after 10 minutes, so a tampered callback cannot
  redirect an account to someone else's sheet.
- **Email text is untrusted input** sent to Gemini. It is treated as data to
  parse, never as instructions to follow.

---

## 📄 License

<!-- TODO: add a LICENSE file and name the license here. -->

No license has been chosen yet. Add a `LICENSE` file and name the license here
before publishing.
