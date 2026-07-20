# Clear2Buy — Codex Instructions

## Working Style

- **Do not use git.** No commits, branches, merges, or any git commands. Just edit files directly.
- Work directly on the main branch. Do not create worktrees or feature branches.

## Project

Flask-based procurement policy assistant for municipal governments.

- `app.py` — Flask routes and API
- `procurement_config.py` — typed config loader (singleton `cfg`)
- `data/procurement_config.json` — city-specific configuration
- `static/` — frontend HTML/CSS
- `intake.py` — prompt builder
- `policy_rag.py` — vector store / RAG

## Running

```
.venv\Scripts\python.exe app.py
```

Or: `flask run --port 5000`
