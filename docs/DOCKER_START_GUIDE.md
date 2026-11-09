# Step-by-Step Docker Start Guide (`docs/DOCKER_START_GUIDE.md`)

> **Target Environment**: Windows 10 / 11 + PowerShell + Docker Desktop (WSL2 Linux containers).  
> **Project Scope**: Local study project only (not production).  
> Every recommendation in this guide is marked **[MUST]**, **[SHOULD]**, or **[NICE-TO-HAVE]**.

---

## 1. Install and Check Prerequisites

Before starting the project, make sure the following tools are installed and running on your Windows machine:

1. **[MUST] Docker Desktop for Windows** with the **WSL 2 based engine** enabled (`Settings > General > Use the WSL 2 based engine`).
2. **[MUST] Windows Subsystem for Linux 2 (WSL2)** running a Linux kernel.
3. **[MUST] Git for Windows** and **Windows PowerShell**.

Open **PowerShell** and run these verification commands:

```powershell
# 1. Verify Docker client and Docker Desktop Linux Engine are running
docker version

# 2. Verify Docker Compose v2 is installed
docker compose version

# 3. Verify WSL2 backend distributions are running on Version 2
wsl -l -v

# 4. Verify Git is installed
git --version
```

**What to expect**:
- `docker version` prints both a `Client:` section and a `Server: Docker Desktop` section (`OS/Arch: linux/amd64`). If you see `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`, open **Docker Desktop** from the Windows Start Menu and wait until the bottom-left whale icon turns green (`Engine running`).
- `docker compose version` prints `Docker Compose version v2.x` (or `v5.x` in newer Docker Desktop releases).

---

## 2. Get the Project

Ensure the backend repository (`coffee_shop`) and the sibling frontend folder (`coffee_shop_frontend`) sit side-by-side inside the same parent directory (`D:\Study\Year-4\Python\`):

```powershell
# Clone the repository (if not already on your machine) and enter the project folder
cd D:\Study\Year-4\Python
# git clone <repository-url> coffee_shop

cd D:\Study\Year-4\Python\coffee_shop
```

> **[IMPORTANT] Compose File Locations**:
> - `D:\Study\Year-4\Python\coffee_shop\docker-compose.yml` — Primary stack (`coffee-shop-stack`), used when you run standard `docker compose ...` commands. Keeps PostgreSQL port `5432` strictly internal to the Docker network so it never collides with a native Windows PostgreSQL service.
> - `D:\Study\Year-4\Python\coffee_shop\docker-compose.local.yml` — Local helper stack (`coffee-shop-local`), used by the helper scripts in `scripts/*.ps1` (and also exposes PostgreSQL on `127.0.0.1:5433` for GUI tools like DBeaver/pgAdmin).
> - Note: `D:\Study\Year-4\Python\coffee_shop\operation_service` contains `operation_service/Dockerfile` and `operation_service/.env`.

---

## 3. Create the `.env` Files

Each microservice reads its own `.env` file via `env_file` in `docker-compose.yml`, and Docker Compose itself reads the root `.env` file.

### 3.1 Copy `.env.example` Templates

Run these commands from `D:\Study\Year-4\Python\coffee_shop`:

```powershell
# 1. Root Compose environment file
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# 2. Per-service backend environment files
if (-not (Test-Path user_service\.env))      { Copy-Item user_service\.env.example user_service\.env }
if (-not (Test-Path operation_service\.env)) { Copy-Item operation_service\.env.example operation_service\.env }
if (-not (Test-Path report_service\.env))    { Copy-Item report_service\.env.example report_service\.env }

# 3. Optional frontend local environment file (used when running npm run dev/build on host)
if (-not (Test-Path ..\coffee_shop_frontend\.env.local)) {
    Copy-Item ..\coffee_shop_frontend\.env.local.example ..\coffee_shop_frontend\.env.local
}
```

### 3.2 Fill In Required Variable Names (With Safe Placeholders)

> **[MUST] Secret Safety**:
> - **Where Bakong credentials go**: Put `BAKONG_TOKEN` and `BAKONG_ACCOUNT_ID` in **`D:\Study\Year-4\Python\coffee_shop\operation_service\.env`** (and optionally in `D:\Study\Year-4\Python\coffee_shop\.env`).
> - **Never commit or share `.env` files**: `.env` files contain real API tokens, merchant account IDs, and passwords. They are ignored by `.gitignore` and excluded from Docker images by `.dockerignore`.
> - **`JWT_SECRET_KEY` must match**: Set the **exact same** `JWT_SECRET_KEY` value in `user_service\.env`, `operation_service\.env`, and `report_service\.env`.

| `.env` File Path | Variable Names to Fill In | Placeholder Example | Notes |
| :--- | :--- | :--- | :--- |
| **`coffee_shop/.env`** | `DB_USER`<br>`DB_PASSWORD`<br>`JWT_SECRET_KEY`<br>`NEXT_PUBLIC_API_BASE_URL`<br>`PUBLIC_BASE_URL` | `postgres`<br>`<your-db-password>`<br>`<your-32-char-jwt-secret>`<br>`http://localhost:8000`<br>`http://localhost:3000` | Used by Docker Compose variable substitution (`${...}`) for `postgres` and `frontend`. |
| **`coffee_shop/user_service/.env`** | `JWT_SECRET_KEY`<br>`JWT_ALGORITHM`<br>`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `<your-32-char-jwt-secret>`<br>`HS256`<br>`60` | **[MUST]** `JWT_SECRET_KEY` must be identical across all 3 backend `.env` files. |
| **`coffee_shop/operation_service/.env`** | `JWT_SECRET_KEY`<br>`BAKONG_ACCOUNT_ID`<br>`BAKONG_TOKEN`<br>`BAKONG_MERCHANT_NAME`<br>`BAKONG_MERCHANT_CITY`<br>`BAKONG_MAX_AMOUNT_USD`<br>`PAYMENT_EXPIRY_MINUTES`<br>`UPLOAD_DIR`<br>`UPLOAD_MAX_BYTES`<br>`UPLOAD_ALLOWED_TYPES`<br>`UPLOAD_MAX_DIMENSION` | `<your-32-char-jwt-secret>`<br>`<your-merchant@bank>`<br>`<your-bakong-jwt-token>`<br>`Coffee Shop`<br>`Phnom Penh`<br>`1.00`<br>`15`<br>`/app/uploads`<br>`5242880`<br>`image/jpeg,image/png,image/webp`<br>`4000` | **[MUST]** `operation-service` **fails fast at startup** if `BAKONG_TOKEN` or `BAKONG_ACCOUNT_ID` is missing or empty. Product images are stored in `UPLOAD_DIR` (`/app/uploads` mounted to the `uploads_data` Docker named volume). |
| **`coffee_shop/report_service/.env`** | `JWT_SECRET_KEY`<br>`JWT_ALGORITHM`<br>`SERVICE_TOKEN_EXPIRE_MINUTES`<br>`SYNC_PAGE_SIZE` | `<your-32-char-jwt-secret>`<br>`HS256`<br>`5`<br>`100` | Used to verify user tokens and mint short-lived internal sync tokens (`sub: "report_service"`). |
| **`coffee_shop_frontend/.env.local`** | `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | **[MUST]** Never place `BAKONG_TOKEN` or secrets in `NEXT_PUBLIC_*` variables. |

---

## 4. First Start (`docker compose up -d --build`)

From `D:\Study\Year-4\Python\coffee_shop`, build and start all 6 containers:

```powershell
cd D:\Study\Year-4\Python\coffee_shop
docker compose up -d --build
```

*(Alternatively, if you prefer to use the local helper scripts which run `docker-compose.local.yml`, run `powershell -ExecutionPolicy Bypass -File .\scripts\up.ps1`.)*

### What to Expect & How Long It Takes
- **First build**: Takes **2 to 4 minutes** (pulls `postgres:16-alpine`, `kong:3.9`, `python:3.12-slim`, and `node:22-alpine`, installs pip/npm dependencies, and compiles the Next.js standalone build).
- **Subsequent starts**: Takes **10 to 20 seconds** using cached Docker layers.
- **Startup order** (enforced automatically via `depends_on: condition: service_healthy`):
  1. `postgres` starts first and passes `pg_isready`.
  2. `user-service`, `operation-service`, and `report-service` start once `postgres` is healthy.
  3. `kong` starts once all three backend services report `healthy`.
  4. `frontend` starts once `kong` reports `healthy`.

### Check That All 6 Services Are Healthy

```powershell
docker compose ps
```

Wait about 15–30 seconds and run `docker compose ps` again until all 6 containers show **`Up ... (healthy)`**:
- `coffee_postgres` — `Up ... (healthy)`
- `coffee_user_service` — `Up ... (healthy)`
- `coffee_operation_service` — `Up ... (healthy)`
- `coffee_report_service` — `Up ... (healthy)`
- `coffee_kong_gateway` — `Up ... (healthy)`
- `coffee_frontend` — `Up ... (healthy)`

---

## 5. Database Initialization & Migrations

### How Databases and Migrations Run in This Project
1. **Automatic Database Creation (First Boot)**:
   When the `postgres` container initializes a fresh volume for the first time, it automatically executes [`postgres/init-databases.sh`](../postgres/init-databases.sh) (`mounted at /docker-entrypoint-initdb.d/init-databases.sh`), which creates:
   - `user_service_db`
   - `operation_service_db`
   - `report_service_db`
   - `user_service_test_db`, `operation_service_test_db`, `report_service_test_db`
2. **Automatic SQLAlchemy Table Creation on Service Startup**:
   All three FastAPI services (`user_service/app/main.py`, `operation_service/app/main.py`, `report_service/app/main.py`) call `Base.metadata.create_all(bind=engine)` at startup, automatically creating tables if they do not exist yet.
3. **Alembic Migrations for `operation-service`**:
   `operation-service` also maintains idempotent Alembic migrations ([`0001_bakong_khqr_schema.py`](../operation_service/migrations/versions/0001_bakong_khqr_schema.py) for `payments`, `payment_attempts`, `payment_events`, `refunds`, `idempotency_keys`, and `verification_jobs`, and [`0002_product_images.py`](../operation_service/migrations/versions/0002_product_images.py) for the `product_images` table and partial unique index `ix_product_images_one_primary_per_product`).

### Verify PostgreSQL Is Ready and Run Alembic Upgrade

```powershell
# 1. Check PostgreSQL readiness inside the container
docker compose exec postgres pg_isready -U postgres -d postgres

# 2. List the initialized databases
docker compose exec postgres psql -U postgres -d postgres -c "\l"

# 3. Apply / verify Alembic migrations on operation-service
docker compose exec operation-service alembic upgrade head
docker compose exec operation-service alembic current
```

---

## 6. Kong Gateway Bootstrap & Verification

### How Kong Loads Routes and Plugins
- Kong runs in **DB-less declarative mode** (`KONG_DATABASE=off`).
- The declarative configuration file [`kong/kong.yml`](../kong/kong.yml) is mounted read-only into the container at `/kong/declarative/kong.yml` (`KONG_DECLARATIVE_CONFIG: /kong/declarative/kong.yml`).
- When the `kong` container starts, it parses `kong.yml` into memory automatically — no separate bootstrap script or database migration is needed.

### Verify Kong via the Localhost-Only Admin API (`127.0.0.1:8001`)

```powershell
# Check Kong runtime status
curl.exe -s http://127.0.0.1:8001/status | ConvertFrom-Json

# List configured upstream services (count & names)
(Invoke-RestMethod -Uri "http://127.0.0.1:8001/services").data | Select-Object name, host, port, path

# List configured routes (count & paths)
(Invoke-RestMethod -Uri "http://127.0.0.1:8001/routes").data | Select-Object name, paths, methods, strip_path

# List active plugins
(Invoke-RestMethod -Uri "http://127.0.0.1:8001/plugins").data | Select-Object name, enabled
```

---

## 7. Open the App & Test Health Endpoints

### Application URLs
- **Web Frontend (Next.js)**: [http://localhost:3000](http://localhost:3000)
- **API Gateway Proxy (Kong)**: [http://localhost:8000](http://localhost:8000)
- **Kong Admin API (Localhost Only)**: [http://127.0.0.1:8001](http://127.0.0.1:8001)

### Test Health & Public Endpoints

```powershell
# 1. Aggregated gateway health check (routes to operation-service)
curl.exe -i http://localhost:8000/health

# 2. Individual microservice health checks through Kong
curl.exe -s http://localhost:8000/health/user
curl.exe -s http://localhost:8000/health/operation
curl.exe -s http://localhost:8000/health/report

# 3. Public product catalog through Kong
curl.exe -s http://localhost:8000/api/products

# 4. Run the full automated smoke test suite (no real payments created)
powershell -ExecutionPolicy Bypass -File .\scripts\smoke.ps1
```

---

## 8. Daily Use Commands

```powershell
# Start the stack (preserving existing containers and database data)
docker compose up -d

# Stop the stack safely (keeps all PostgreSQL data in the volume)
docker compose down

# View container status and health
docker compose ps

# View logs (all services, or follow one specific service)
docker compose logs --tail 100
docker compose logs -f operation-service
docker compose logs -f kong
docker compose logs -f frontend

# Rebuild a single service without cache (e.g., after code or dependency changes)
docker compose build --no-cache operation-service
docker compose up -d --force-recreate --no-deps operation-service
```

### Recreating a Service After Changing `.env`

> [!IMPORTANT]
> **`docker compose restart <service>` does NOT re-read `.env` files!**  
> When you edit `operation_service/.env` (for example, after renewing `BAKONG_TOKEN` or changing `BAKONG_MAX_AMOUNT_USD`), `restart` reuses the old container environment. You **[MUST]** use `up -d --force-recreate`:

```powershell
# Reload updated .env variables into operation-service
docker compose up -d --force-recreate operation-service
```

> [!IMPORTANT]
> **Rebuilding `frontend` After Changing `NEXT_PUBLIC_*` Variables (`[MUST]`)**:  
> Next.js embeds `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_BASE_URL` into the browser JavaScript bundle at **image build time** (via Docker `build.args`). If you change `NEXT_PUBLIC_API_BASE_URL` or `PUBLIC_BASE_URL` in `.env`, you **[MUST]** rebuild the `frontend` image before recreating the container:
> ```powershell
> docker compose build --no-cache frontend
> docker compose up -d --force-recreate frontend
> ```

---

## 9. Stopping vs Resetting (Destructive Warning)

### Safe Stop (Recommended — Keeps Database Data and Uploaded Images)
```powershell
# Stops and removes containers, but KEEPS the postgres_data and uploads_data volumes
docker compose down
```

### Destructive Reset (Deletes Database Data & Uploaded Product Images!)
> [!CAUTION]
> **DESTRUCTIVE COMMAND WARNING**: Running `docker compose down -v` or `.\scripts\reset-db.ps1` permanently deletes both the `postgres_data` volume and the `uploads_data` volume (`/app/uploads`), which **erases all users, products, orders, payment records, AND uploaded product image files**. Never run `-v` unless you intentionally want a completely blank environment.

```powershell
# DESTRUCTIVE: Deletes containers AND deletes both postgres_data and uploads_data volumes!
# Do NOT run this unless you want to wipe all local database tables and uploaded images.
# docker compose down -v
```

---

## 10. Backup and Restore PostgreSQL Data & Uploaded Images (`uploads_data`)

All backup and restore commands below are verified against the `postgres` and `operation-service` containers in `docker-compose.yml`, the three service databases (`user_service_db`, `operation_service_db`, `report_service_db`), and the `uploads_data` named volume mounted at `/app/uploads`.

### Backup a Database (`pg_dump`)

```powershell
# Create a local backups directory (git-ignored)
New-Item -ItemType Directory -Force -Path .\backups | Out-Null

# Backup operation_service_db (orders, products, product_images metadata, KHQR payments)
docker compose exec -T postgres pg_dump -U postgres -d operation_service_db > .\backups\operation_service_db.sql

# Backup user_service_db (users and roles)
docker compose exec -T postgres pg_dump -U postgres -d user_service_db > .\backups\user_service_db.sql

# Backup all databases at once (pg_dumpall)
docker compose exec -T postgres pg_dumpall -U postgres > .\backups\all_databases.sql
```

### Restore a Database (`psql`)

```powershell
# Restore operation_service_db from a SQL dump file
Get-Content .\backups\operation_service_db.sql | docker compose exec -T postgres psql -U postgres -d operation_service_db

# Restore user_service_db from a SQL dump file
Get-Content .\backups\user_service_db.sql | docker compose exec -T postgres psql -U postgres -d user_service_db
```

### Backup & Restore the `uploads_data` Volume (`/app/uploads`)

Product image files (`<uuid4>.<ext>`) are stored on the Docker named volume `uploads_data` mounted at `/app/uploads` inside `operation-service`.

```powershell
# 1. Backup all uploaded product images from the running operation-service container to .\backups\uploads
New-Item -ItemType Directory -Force -Path .\backups\uploads | Out-Null
docker compose cp operation-service:/app/uploads/. .\backups\uploads

# 2. Restore uploaded product images from .\backups\uploads into operation-service (/app/uploads)
docker compose cp .\backups\uploads/. operation-service:/app/uploads

# 3. Scan for orphan image files on disk (dry-run by default; pass --delete to remove orphans older than 1 hour)
docker compose exec operation-service python -m app.services.media_cleanup
```

---

## 11. Docker Desktop Tips for Windows (WSL2)

1. **[SHOULD] Viewing Containers & Logs in the Docker Desktop GUI**:
   - Open **Docker Desktop** -> click **Containers** in the left sidebar.
   - Expand **`coffee-shop-stack`** (or **`coffee-shop-local`**) to see all 6 containers, their status (`Healthy`), published ports (`3000`, `8000`, `8001`), and CPU/RAM usage.
   - Click any container name (e.g., `coffee_operation_service`) to view live logs in the **Logs** tab, inspect environment/mounts in **Inspect**, or open a shell in **Exec**.
2. **[NICE-TO-HAVE] WSL2 Memory & CPU Recommendations (Do Not Change Unless Needed)**:
   - On Windows, the `vmmemWSL` process can grow to use available host RAM.
   - If your PC has 8–16 GB RAM and feels slow, you can optionally create `$env:USERPROFILE\.wslconfig` with `memory=6GB` and `processors=4` so WSL2 does not starve Windows.
3. **[SHOULD] Checking Disk Space Safely**:
   - Run `docker system df` in PowerShell to inspect how much disk space images, containers, volumes, and build cache use without deleting anything.
   - To remove only dangling/untagged build artifacts from old builds without touching running containers or database volumes, use Docker Desktop's **Builds** tab or remove specific unused images only. Never run `docker volume prune` or `docker system prune --volumes` as that can delete database volumes.

---

## 12. Troubleshooting Table

| Problem / Symptom | Cause | Fix (Verified Commands) |
| :--- | :--- | :--- |
| **Port already in use** (`Bind for 127.0.0.1:5432 failed: port is already allocated` or `:8000` / `:3000`) | A native Windows PostgreSQL service (e.g., `postgres.exe`) or another process is already listening on the port. | 1. `docker-compose.yml` keeps port `5432` internal and `docker-compose.local.yml` maps host port `5433` (`POSTGRES_HOST_PORT:-5433`).<br>2. To find what owns a port: `netstat -ano \| findstr ":8000"` then `Get-Process -Id <PID>`. |
| **Service `unhealthy` or exits immediately (`operation-service`)** | `BAKONG_TOKEN` or `BAKONG_ACCOUNT_ID` is missing/empty in `operation_service/.env`, or `JWT_SECRET_KEY` is missing. | Check logs: `docker compose logs --tail 50 operation-service`. Fill in `BAKONG_TOKEN`, `BAKONG_ACCOUNT_ID`, and `JWT_SECRET_KEY` in `operation_service/.env`, then run `docker compose up -d --force-recreate operation-service`. |
| **`Module not found: Can't resolve 'qrcode.react'`** in frontend build | Stale `node_modules` volume shadowing image dependencies, or building without lockfile. | Pinned `qrcode.react@4.2.0` in `package.json` with `output: 'standalone'`. Rebuild cleanly: `docker compose build --no-cache frontend` and `docker compose up -d --force-recreate frontend`. |
| **`localhost` vs service name inside containers (`Connection refused` to `127.0.0.1:8000` or `:5432`)** | Inside a Docker container, `localhost` (`127.0.0.1`) refers to that container itself, NOT the Windows host or other containers. | Use Docker Compose service names for container-to-container traffic: `DB_HOST=postgres`, `INTERNAL_API_BASE_URL=http://kong:8000`, `USER_SERVICE_BASE_URL=http://user-service:8001`, `OPERATION_SERVICE_BASE_URL=http://operation-service:8002`. Keep `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` for browser requests. |
| **CRLF script errors** (`exec: ...: no such file or directory` or `/bin/bash^M: bad interpreter`) | Git on Windows converted `.sh` scripts (like `postgres/init-databases.sh`) from LF (`\n`) to CRLF (`\r\n`). | `.gitattributes` enforces `*.sh text eol=lf`. Convert the file to LF in VS Code (bottom-right status bar -> `LF`) or run `git checkout -- postgres/init-databases.sh` and rebuild. |
| **Bakong token missing or expired** (`BakongAuthError` / `CRITICAL ALERT: Bakong Developer Token is invalid or expired!`) | NBC Bakong Developer JWT tokens expire after ~30–90 days, or `BAKONG_TOKEN` is invalid. | Renew your token on the NBC Bakong Developer Portal (or via `/v1/renew_token`), update `BAKONG_TOKEN` in `operation_service/.env`, and run `docker compose up -d --force-recreate operation-service`. |
| **Kong returns `404 Not Found`** (`{"message":"no Route matched with those values"}`) | Request path or HTTP method does not match any route in `kong/kong.yml` (e.g., calling `/product` instead of `/api/products`). | Call the gateway prefix `/api/auth`, `/api/products`, `/api/categories`, `/api/orders`, `/api/reports`, or `/health`. If you edited `kong/kong.yml`, reload Kong: `docker compose exec kong kong reload`. |
| **Kong returns `502 Bad Gateway`** (`An invalid response was received from the upstream server`) | Target FastAPI microservice (`user-service`, `operation-service`, or `report-service`) crashed or is still starting. | Run `docker compose ps` and `docker compose logs --tail 50 <service>` to inspect the Python traceback, then `docker compose up -d <service>`. |
| **Database connection refused** (`psycopg2.OperationalError: connection to server at "postgres" ... refused`) | Microservice tried to connect before PostgreSQL finished initializing, or `DB_HOST` is set to `localhost` inside `.env`. | Ensure `DB_HOST=postgres` inside Docker (overridden in `docker-compose.yml` `environment:`), and wait for `postgres` healthcheck (`pg_isready`) to pass. |
| **"Changed `.env` but nothing changed"** | Ran `docker compose restart <service>`, which restarts the process inside the existing container without re-reading `.env` files. | Always run `docker compose up -d --force-recreate <service>` after editing `.env` files. |
| **`PermissionError: [Errno 13] Permission denied: '/app/uploads/...'` on image upload** | The `/app/uploads` directory or `uploads_data` volume was initialized with `root:root` ownership instead of `appuser:appgroup` (`UID 1000`). | `operation_service/Dockerfile` pre-creates and `chown`s `/app/uploads` before `USER appuser`. Rebuild `operation-service` (`docker compose build --no-cache operation-service && docker compose up -d --force-recreate operation-service`) or fix existing volume ownership once: `docker compose exec -u root operation-service chown -R appuser:appgroup /app/uploads`. |
| **`413 Request Entity Too Large` when uploading a product image** | Uploaded image file exceeds `UPLOAD_MAX_BYTES` (`5 MB` / `5242880` bytes in `operation-service`) or Kong's `6 MB` route payload / Nginx body limit (`KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE: 6m`). | Compress or resize the JPEG/PNG/WebP image to `<= 5 MB` (`<= 4000x4000` px), or increase `UPLOAD_MAX_BYTES` in `operation_service/.env` and `allowed_payload_size` / `KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE` in `kong/kong.yml` and `docker-compose.yml`. |
| **Preview image not showing when sharing a product link in chat** | 1. Shared a `http://localhost:3000/...` URL (external chat crawlers cannot reach your PC's loopback interface).<br>2. `PUBLIC_BASE_URL` was changed in `.env` without rebuilding `frontend`.<br>3. Chat app cached an older preview for that URL. | 1. Expose port `3000` via a temporary HTTPS tunnel (`cloudflared` or `ngrok`), set `PUBLIC_BASE_URL=https://<tunnel-domain>` in `.env`, and run `docker compose build --no-cache frontend && docker compose up -d --force-recreate frontend`.<br>2. Verify locally with `Invoke-WebRequest -Uri "http://localhost:3000/products/1/opengraph-image" -UseBasicParsing` (must return `200` with `Content-Type: image/png`).<br>3. Refresh the chat crawler cache (e.g., via Telegram `@WebpageBot` or Facebook Sharing Debugger). |
