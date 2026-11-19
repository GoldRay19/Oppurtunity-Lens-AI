# OpportunityLens AI

Opportunity discovery and career planning dashboard with a FastAPI backend and React frontend. Deploy as a single service on Render using the included `render.yaml`.

## Run locally

```bash
cd frontend && npm install && npm run build
cd .. && python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

See `.env.example` for optional Firebase Admin and Adaption AI configuration. Without those credentials, the app runs in demo mode; verify opportunity details with the original organizer.