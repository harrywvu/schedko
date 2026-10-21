# Repository Guidelines

## Project Structure & Module Organization

SchedKo has a React/Vite frontend in `client/` and a FastAPI backend in `server/`. UI components live in `client/src/components/`, schedule helpers in `client/src/utils/`, mock data in `client/src/data/`, and static assets in `client/public/`. Backend routes are in `server/routes.py`; extraction, parsing, database access, and configuration have separate modules. There is no test directory.

## Build, Test, and Development Commands

- `npm ci`: install the root workspace and client dependencies.
- `npm run dev`: start the Vite frontend at `http://localhost:5173`.
- `npm run lint`: run ESLint on client JavaScript and JSX.
- `npm run build`: create the production frontend in `client/dist/`.
- `npm run preview`: serve the built frontend locally.
- From `server/`, run `python -m pip install -r requirements.txt`, then `uvicorn main:app --reload` to start the API at `http://localhost:8000`. Use a virtual environment for Python dependencies.

Run the frontend and API in separate terminals. The API needs PostgreSQL at startup.

## Coding Style & Naming Conventions

Use two-space indentation in JavaScript/JSX and four spaces in Python. Follow nearby semicolon style. Name React components and files in `PascalCase` (for example, `Results.jsx`); use `camelCase` for frontend helpers and `snake_case` for Python functions and modules. Keep API contracts synchronized across `server/` and the client. ESLint checks frontend style; no Python formatter or linter is configured.

## Testing Guidelines

No automated test command, framework, or coverage threshold is configured. Before opening a PR, run `npm run lint` and `npm run build`, then manually check the affected flow with both services running. For upload or processing changes, verify a new PDF, a previously uploaded PDF, progress updates, and schedule results. Add focused tests when introducing a test framework; use descriptive `test_*.py` or `*.test.jsx` names.

## Commit & Pull Request Guidelines

Recent commits commonly use prefixes such as `feat:`, `fix:`, and `chore:`, sometimes with a scope like `fix(upload):`. Use a short imperative summary following that pattern. PRs should describe the behavior changed, list verification steps, link a related issue when one exists, and include screenshots for visible UI changes.

## Security & Configuration

Keep `DATABASE_URL` and `GEMINI_API_KEY` in an untracked `.env` file for the server; set `CORS_ORIGINS` when the frontend origin differs from the local default. The client reads `VITE_API_URL`, defaulting to `http://localhost:8000`. Never commit credentials or real exam PDFs.
