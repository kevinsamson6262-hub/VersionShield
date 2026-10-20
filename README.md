# VersionShield — advanced compatibility analysis MVP

VersionShield compares an old client, its old backend, and a proposed new backend. The upload workflow combines **best-effort static source scanning** with **OpenAPI 3 / basic Swagger 2 contract comparison** when both backend ZIPs contain recognizable JSON/YAML API specifications. It does not execute uploaded source code or make live requests to your services.

## Stack
- React + TypeScript + Vite + Tailwind CSS + Framer Motion
- Python FastAPI
- Optional Firestore persistence through Firebase Admin SDK

## Run on Windows

### Backend terminal
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```
Open `http://127.0.0.1:8000/docs` to see the API docs.

### Frontend terminal
```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```
Open `http://localhost:5173`.

## Use source analysis
1. Create a ZIP of the old frontend source. Include source files; exclude `node_modules`, `.git`, build folders, `.venv`, secrets, `.env` files and real customer data.
2. Create a ZIP of the old backend source.
3. Create a ZIP of the proposed new backend source.
4. Select all three ZIPs in the dashboard and click **Analyze compatibility**.
5. Review the heuristic risk score, severity-ranked findings, removed fields/routes, and recommendations. For deeper contract checks, include an OpenAPI/Swagger JSON or YAML specification in both backend ZIPs (for example `openapi.json` or `openapi.yaml`).

The API endpoint is `POST /api/analyze-source` with multipart fields `old_frontend`, `old_backend`, `new_backend`. The endpoint accepts ZIPs up to 20 MB each, scans supported source file types, and does not extract to disk or execute code.

## Firebase
The backend supports Firestore if `FIREBASE_CREDENTIALS` points to a local service-account JSON key. Keep the key outside Git, never add it to ZIPs sent to others, and never commit it. If not configured, the app falls back to in-memory demo storage (which resets when the backend restarts).

In `backend/.env`, configure:
```env
FIREBASE_CREDENTIALS=C:\\secure\\path\\firebase-service-account.json
FIRESTORE_COLLECTION_PREFIX=versionshield
FRONTEND_ORIGIN=http://localhost:5173
```

## Existing manual schema workflow
The original endpoints remain available: `/api/versions`, `/api/check`, `/api/reports`, `/api/demo/seed` and `/api/health`.

## Advanced checks currently supported
When both backend ZIPs contain OpenAPI/Swagger contracts, VersionShield compares declared operations and detects likely removed operations, request/response fields, response status codes, newly required request parameters/fields, basic schema type changes, and removed operation-level security declarations. It also retains the source-pattern scan and surfaces severity-ranked findings with suggested actions.

## Critical limitations
- The displayed risk score is a heuristic prioritization score, **not** a probability or validated compatibility percentage.
- OpenAPI analysis compares declared contracts only; it does not verify actual server responses, database migrations, auth behavior, latency, side effects or real devices.
- Static scanning is heuristic. It may miss aliases, serializers, generated clients, GraphQL, dynamic access and indirect calls. Matching a field name does not prove that a client depends on it.
- This cannot guarantee compatibility. Confirm findings with automated contract/integration tests and staged rollout before deployment.
- Upload only source code you are authorized to share. Uploaded source is read in memory and never executed, but the resulting report may include names of fields/routes.

## Added in the production-hardening track
- The report now explicitly labels which stages ran: source scan, OpenAPI contract analysis, and runtime tests. Runtime tests are honestly marked `not_run` because this release does not execute untrusted backends.
- OpenAPI comparison additionally checks optional parameters becoming required, optional request bodies becoming required, request/response media-type removals, parameter type changes, enum narrowing, format changes, nullability narrowing, array item type changes, and operation deprecation.
- Uploads are read with a 20 MB cap before the analyzer processes them; ZIP symbolic-link entries are skipped.
- The UI can export the complete report as JSON.
- `tools/versionshield_cli.py` provides a local/CI command-line entry point. Example:

```powershell
python tools/versionshield_cli.py --old-frontend frontend-old.zip --old-backend backend-old.zip --new-backend backend-new.zip --output report.json --fail-on high
```

Exit codes: `0` = no findings at or above the configured threshold; `1` = analysis/input error; `2` = threshold met. A zero exit code means only that this analyzer did not find configured high/critical findings; it does not certify compatibility.

An example manual GitHub Actions workflow is in `.github/workflows/versionshield-example.yml`. It expects ZIP paths supplied as workflow inputs and does not execute source code from those ZIPs.

## Production-readiness gaps (do not misrepresent these as implemented)
- No runtime request/replay testing, disposable code-execution sandbox, login/tenant isolation, billing, audit trail, or validated compatibility probability is included.
- Before selling to customers, add authentication and per-tenant authorization, request rate limits, persistent job/report lifecycle, retention controls, monitoring, backup/recovery, dependency/security scanning, and independent security review.
- If adding runtime testing later, run the target backend in a network-restricted disposable container with strict CPU/memory/time limits and no secrets; never execute uploaded code in the main API process.

## Mobile-client compatibility workflow (added)

Use the existing three-upload analyzer as follows:

1. **Legacy mobile/web client ZIP** — include source for the app version still installed by users. Supported source extensions include Kotlin (`.kt`), Java (`.java`), Dart (`.dart`), and web source.
2. **Current backend ZIP** — include source and, when available, the current OpenAPI/Swagger JSON or YAML specification.
3. **Proposed backend ZIP** — include the candidate backend source and its matching OpenAPI/Swagger specification.

The report now identifies the likely client platform, detects a limited set of mobile HTTP frameworks and annotated API paths, and shows a compatibility matrix for the legacy client against the current and proposed backend. These are static heuristics plus contract comparison, not a claim that the app was launched or tested on a device.

### Current limits

- APK/AAB binary analysis, Android emulator/device execution, iOS IPA analysis, runtime replay, authentication-flow testing, and live backend integration tests are **not implemented** in this build.
- For best contract coverage, include OpenAPI specs for both backend versions. If unavailable, source scanning is heuristic and can miss indirect API usage, serializers, dynamic endpoints, and runtime behavior.
- Validate findings with consumer-driven contract or integration tests and a staging deployment before making production release decisions.
