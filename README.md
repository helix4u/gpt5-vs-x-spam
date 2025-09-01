gpt5 vs x spam — end‑to‑end scraper, classifier, and blocker for X profiles with a tiny UI and CLI.

Quick start (Windows)

- From repo root: .\start.bat
  - Creates venv if missing, installs deps and Playwright browsers, starts API on 127.0.0.1:8000, starts the frontend on 127.0.0.1:5500, and opens the UI.

Manual setup (all platforms)

1) Backend
- cd backend
- python -m venv .venv
- Windows: .venv\Scripts\activate
- macOS/Linux: source .venv/bin/activate
- pip install -r requirements.txt
- Copy .env.example to .env and adjust settings (see Config below).
- Install Playwright browsers:
  - Windows: backend\install_playwright.bat
  - macOS/Linux: bash backend/install_playwright.sh
- Run the API (no reload): python run_api.py
- Health check: http://127.0.0.1:8000/health

2) Frontend
- cd frontend
- python -m http.server 5500
- Open http://127.0.0.1:5500/

UI features

- Search tab
  - Query, Max results, “classify” toggle
  - Vendor presets: Custom, LM Studio, Ollama, OpenAI, OpenRouter (fills API base)
  - API base, Model ID, API key fields (persisted locally)
  - Live progress: status updates while scraping/classifying
  - Profiles stream in with avatars; classifications overlay as they arrive (label, confidence, reasons)
  - Queue block checkboxes + “select likely impersonators” + “run block on selected”

- History tab
  - Day picker + type filter (all, block, classification)
  - Paged listing by date from backend/data/results.jsonl
  - For blocks: handle, time, ok/failed
  - For classifications: handle, time, label, confidence, reasons

CLI

- cd backend (activate venv), then:
- python -m app.cli search "maye musk" --max-results 40 --out maye.json
- python -m app.cli classify_file maye.json
- python -m app.cli block @handle1 @handle2

API endpoints

- GET /health → { ok: true }
- GET /api/search?query=...&max_results=...&classify=true|false → SearchResponse
- GET /api/search_stream?… → Server‑Sent Events
  - events: status, profiles, classification, done
- POST /api/block → [ BlockResult ]
- GET /api/history/days → recent days + counts
- GET /api/history/items?day=YYYY-MM-DD&typ=all|block|classification&limit=&offset=

Scraper & blocking

- Scraper opens an interactive Chromium (HEADLESS=false by default), lets you log into X once, then uses a persistent user data dir (data/pw_user) so your session is kept.
- Search results auto‑scroll to load more until your requested max.
- Blocking runs fast with short timeouts and minimal jitter while respecting a global window; it handles “temporarily restricted” interstitials and uses multiple selectors with JS fallbacks for the overflow menu, Block item, and confirmation.

Config (backend/.env)

- Data: DATA_DIR, CACHE_DIR, DATASET_PATH, RESULTS_PATH
- LLM: LLM_PROVIDER=local|openai, LLM_API_BASE, LLM_MODEL, OPENAI_API_KEY
- Actions: ACTIONS_PER_15MIN, MIN_ACTION_JITTER_MS, MAX_ACTION_JITTER_MS
- Browser: HEADLESS=false|true, USER_AGENT, CHROMIUM_ARGS (JSON list), USER_DATA_DIR, SLOW_MO_MS

Dataset & results

- Profiles → data/dataset.jsonl (type: "profile")
- Classifications → data/results.jsonl (type: "classification", saved_at)
- Block outcomes → data/results.jsonl (type: "block", saved_at)
- Per-handle cache → data/cache

Utilities

- install.bat (repo root): one‑shot installer for Windows
- backend\install_playwright.bat|.sh: installs Chromium for Playwright
- backend\run_api.bat: Windows launcher for the API
- backend\run_api.py: API runner (no reload)
- backend\clear_data.bat|.sh: purge dataset, results, and cache

Tips

- Python 3.11+ recommended. Windows works fine with the current sync Playwright setup.
- If X UI changes, update selectors in backend/app/scraper_sync.py and backend/app/actions.py.
- Use restraint with blocking; you are responsible for ToS compliance.

Security & compliance

- Automating interactions with x.com may be restricted by their terms and by law. Use only on accounts you are authorized to manage. This code is provided for research/moderation tooling; you are responsible for compliance.

legal and safety...

automating interactions with x.com may be restricted by their terms of service and local laws. use only on accounts you own or have permission to manage. this code is provided for research and moderation assistance; you are responsible for compliance.

<img width="1470" height="1129" alt="image" src="https://github.com/user-attachments/assets/e3d680ce-e4e2-4159-8028-2bd5b4fc82d7" />

--

<img width="1470" height="1129" alt="image" src="https://github.com/user-attachments/assets/758e456e-1010-4bbd-86d7-780db7bfa4f3" />


