# BeerShop CRM Frontend

React + TypeScript + Vite. Requires Node 22.12+ (Docker and CI use Node 24).
Dependencies are pinned in package.json and package-lock.json; use `npm ci`.

## Build and Run

From this directory:

```bash
npm ci
npm run build
npm test
```

The build writes `../web/crm-dist`. Start the existing FastAPI app from the
repository root; open `/crm`. Unbuilt `/crm` returns 503 instead of silently
falling back to a different UI. `/crm-legacy` serves the preserved vanilla UI.
No database migration is required by 4.4.

For hot reload, run a local FastAPI instance on 127.0.0.1:8018 and `npm run dev`
here; open http://127.0.0.1:5173/crm/. Vite proxies API/assets to that local
server. Never proxy local development to the production database or API.
Port 5173 uses strictPort: choose another with `npm run dev -- --port 5174`
when it is occupied.

Docker performs a separate Node build and copies only generated assets into
the Python runtime. No extra hosted frontend or paid service is needed.

## Ownership

- `App.tsx`: session, navigation, search/filter/pagination, query coordination.
- `api.ts`: same-origin API client, cancellation, 401 handling, typed errors.
- `types.ts`: existing CRM/inventory API response shapes.
- `Lists.tsx`, `Details.tsx`, `Editor.tsx`: read views and explicit mutations.
- `ui.tsx`: native modal dialogs, formatting, query lifecycle.
- `react.css`: React-specific additions to the existing `web/crm.css` design.

The backend remains the authority for money, authorization, stock and order
transitions. Client-side types are not a security boundary. Never put secrets
in `VITE_*`, source code or browser storage. The admin token is memory-only;
logout aborts requests and unmounts the workspace. Do not log response bodies
containing customer data. Managers are assignment labels, not user accounts.

## Verification

`npm test` checks session cancellation, error handling and request constraints.
`npm run build` runs strict TypeScript checking before the production build.
The repository CI also runs Python tests against SQLite and PostgreSQL 16.

For full isolated browser checks, from the repository root:

```bash
npx --prefix frontend playwright install chromium
python tests/run_crm_browser.py
```

The runner creates an empty temporary database, starts a local fixture with
deterministic FX and no provider access, runs both browser suites, and stops
the server. Set `CHROMIUM_PATH` to use a preinstalled Chromium. Browser tests
reject non-local origins and block requests outside their fixture origin.
They must never be run against Render or a production-connected backend.

No real shipment or payment creation, bank automation, personal roles or
fiscalization is introduced by this migration. Existing safety flags stay off.
