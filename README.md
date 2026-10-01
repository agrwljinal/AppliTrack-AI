# 🎯 AppliTrack AI

> An autonomous, privacy-focused AI agent designed to eliminate manual job application tracking. 

AppliTrack AI monitors your job submissions across platforms like **LinkedIn, Unstop, Indeed, Glassdoor, and Wellfound**. Using **Google Gemini 2.5 Flash**, it parses real-time updates from portal dashboards and incoming notification emails, automatically keeping a single, unified **Google Sheet** updated with your latest application statuses (*Selected*, *Rejected*, or *Pending*).

---

### ✨ Key Features

- 🌐 **Multi-Platform Support:** Track applications across LinkedIn, Unstop, Indeed, Glassdoor, and Wellfound simultaneously.
- 🔒 **Privacy-First Design:** Optional email parsing toggle—users explicitly choose whether or not to allow inbox access.
- 🧠 **AI Status Classification:** Powered by Gemini 2.5 Flash for accurate status extraction from unstructured emails and portal text.
- 📊 **Automated Live Sync:** Real-time updates pushed directly to your personal Google Sheet.
- 💻 **Terminal-Native Workflow:** Simple interactive CLI interface built with `Typer` and `Rich`.

---

### 🚀 Quick Start

1. **Clone the repository:**
   ```bash
    git clone https://github.com/YOUR_USERNAME/applitrack-ai.git
   cd applitrack-ai
    pip install -r requirements.txt
    ```

### Webhook Onboarding

Send each application to your deployed webhook with the target client's Google Sheet ID. The Google service account configured on the server must have access to that sheet.

```bash
curl --request POST \
   --url "https://<your-render-app>.onrender.com/webhook/application" \
   --header "Content-Type: application/json" \
   --data '{
      "sheet_id": "YOUR_CLIENT_GOOGLE_SHEET_ID",
      "applicant_data": {
         "company": "Acme",
         "role": "Software Engineer",
         "platform_used": "Typeform",
         "resume_text": "Applicant resume or application details"
      }
   }'
```

#### Typeform with Make.com

Create a Make.com scenario with a **Typeform: Watch Responses** trigger followed by **HTTP: Make a request**:

- Method: `POST`
- URL: `https://<your-render-app>.onrender.com/webhook/application`
- Header: `Content-Type: application/json`
- Body type: `Raw` with `application/json`

Use this body shape and replace the `{{...}}` placeholders by mapping the corresponding answer tokens from the Typeform trigger:

```json
{
   "sheet_id": "YOUR_CLIENT_GOOGLE_SHEET_ID",
   "applicant_data": {
      "company": "{{1.company}}",
      "role": "{{1.role}}",
      "platform_used": "Typeform",
      "resume_text": "{{1.resume_text}}"
   }
}
```