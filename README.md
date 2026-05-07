# Brauser_use_Agent

This project implements a full-stack automation flow where clicking a button on a simple webpage triggers a Trigger.dev job, which calls a Python FastAPI server running a `browser-use` agent to automate Google Sheets entry via a real browser UI.

## Components

1. **Python Agent & API Server (`agent/`)**: Uses `browser-use`, `langchain-nvidia-ai-endpoints`, and `playwright` to automate browser actions. Served via FastAPI.
2. **Trigger.dev Task (`trigger/`)**: A background task that calls the Python Agent.
3. **Frontend (`frontend/`)**: A simple HTML interface to start the run.

## Setup Instructions

### 1. Python Agent
1. Navigate to the `agent` directory.
2. Create a virtual environment: `python -m venv .venv` and activate it.
3. Install dependencies: `pip install -r requirements.txt`
4. Install Playwright browsers: `playwright install chromium`
5. Copy `.env.example` to `.env` and fill in your NVIDIA API key, Google credentials, and target Sheet ID.
6. Run the FastAPI server locally: `uvicorn server:app --reload --port 8002` (or `python server.py`)

Optional (recommended for stable Google login):
- **Reuse a logged-in browser profile**
  - Set `BROWSER_USER_DATA_DIR` to a folder you control (e.g. `C:\temp\browser-use-profile`).
  - Optionally set `BROWSER_PROFILE_DIRECTORY` (default `Default`).
- **Attach to a single browser window (same window, new tabs)**
  - Launch Chrome/Edge with remote debugging and set `BROWSER_CDP_URL=http://127.0.0.1:9222` in `.env`.

### 2. Trigger.dev Task
1. Navigate to the `trigger` directory.
2. Run `npm install` to install dependencies.
3. Authenticate with Trigger.dev CLI: `npx trigger.dev login`
4. Run locally: `npm run dev`
5. In production, ensure `AGENT_API_URL` environment variable points to your deployed Python Agent.

### 3. Frontend
1. Open `http://127.0.0.1:8002/` in your browser (served by FastAPI).
2. Update the `TRIGGER_API_KEY` in the script section (Note: in a production Vercel app, you would use an API route or serverless function to securely hold this key).

### Custom tasks (web only)

The agent can run **custom web tasks** (forms, docs, web-based notepads) via `POST /run-task`.
Browser-use automates the **browser** only, so native apps like local Notepad are not supported.
Use a web-based note app instead if you need a “notepad” flow.

### Workflow JSON runs

You can execute a structured workflow by POSTing the workflow JSON to `POST /run-workflow`.
The frontend now includes a **Workflow JSON** option that posts directly to this endpoint.
Use the provided example JSON to automate Google search or adapt it to your own steps.

## Deployment

- **Python Agent**: Deploy on a service that supports headless/headful browsers (e.g., Render, Railway, or a standard VPS). Vercel is *not* recommended for the agent due to lack of browser support in Serverless Functions.
- **Trigger.dev**: Deploy via Trigger.dev cloud.
- **Frontend**: Host the `index.html` on Vercel, Netlify, or any static hosting platform.

## End-to-end checklist (GitHub → Trigger.dev → Vercel)

1. **Create a new GitHub repo** and push this project.
2. **Trigger.dev**
   - Create a Trigger.dev account.
   - Create a new project and connect it to your GitHub repo.
   - Add secrets: `AGENT_API_URL`, `GOOGLE_EMAIL`, `GOOGLE_PASSWORD`, `SHEET_ID`.
   - Deploy the Trigger.dev task from the `trigger/` folder.
3. **Backend hosting (agent)**
   - Deploy the FastAPI server on Render/Railway/VPS.
   - Ensure Playwright browsers are installed and available.
   - Set env vars: `NVIDIA_API_KEY`, `GOOGLE_EMAIL`, `GOOGLE_PASSWORD`, `SHEET_ID`.
4. **Vercel frontend**
   - Deploy the `frontend/` folder (static HTML).
   - Update the Backend API URL in the UI to your deployed agent.
   - Update Trigger API key usage as needed (prefer a backend proxy for production).
