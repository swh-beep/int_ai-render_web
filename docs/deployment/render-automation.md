# Render Automation Setup

This repo is prepared for three Render automation paths:

1. `render.yaml` Blueprint for the existing Python web + worker services.
2. GitHub CI checks for Render `checksPass` auto deploys.
3. Manual GitHub Actions deploy trigger through the Render REST API.

Local machine setup already completed:

- Render CLI installed at `C:\Users\User\.codex\bin\render.exe`
- `C:\Users\User\.codex\bin` added to the user PATH
- Render official Codex skills installed under `C:\Users\User\.codex\skills\render-*`

## Required Render Values

Create these values outside Git:

- Render API key: `RENDER_API_KEY`
- Web service ID: `RENDER_WEB_SERVICE_ID`
- Worker service ID: `RENDER_WORKER_SERVICE_ID`

Create the Render API key from:

- `https://dashboard.render.com/u/settings?add-api-key=`

Current Render resources detected via API:

- Workspace: `interiorteacher_ai-render` (`tea-d4naoiidbo4c738tni4g`)
- Web service: `interiorteacher-ai_` (`srv-d4nas8uuk2gs739mogv0`)
- Worker service: `int_ai-render_worker` (`srv-d4ok9q2li9vc7384c0kg`)
- Key Value: `int-ai-redis` (`red-d5s6slggjchc73fbdhv0`)

GitHub Actions secrets already registered:

- `RENDER_API_KEY`
- `RENDER_WEB_SERVICE_ID`
- `RENDER_WORKER_SERVICE_ID`

## Logs

SSH is not required for normal log inspection. Use the Render CLI/API:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\render-logs.ps1 -Target both -Limit 100
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\render-logs.ps1 -Target worker -Level error -Limit 50
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\render-logs.ps1 -Target both -Tail
```

SSH is only needed for an interactive shell session.

## Dashboard Setup

1. In Render, create or connect a Blueprint from this repository.
2. Use `render.yaml` from the repository root.
3. Fill all `sync: false` environment variables in Render Dashboard.
4. Confirm both services are linked to the `main` branch.
5. Confirm auto deploy is set to CI checks passing.

The checked-in `Dockerfile` is available for a future Docker migration if the
native Python runtime lacks the `ffmpeg` binary. The active Blueprint keeps the
existing Python runtime because Render does not allow changing runtime or region
for an existing service.

## GitHub Secrets

Add these repository secrets:

- `RENDER_API_KEY`
- `RENDER_WEB_SERVICE_ID`
- `RENDER_WORKER_SERVICE_ID`

After that, run GitHub Actions > Render Manual Deploy when you want an explicit deploy from GitHub.

## Codex MCP Setup

Add the following to `C:\Users\User\.codex\config.toml` after creating a Render API key:

```toml
[mcp_servers.render]
url = "https://mcp.render.com/mcp"
http_headers = { Authorization = "Bearer <YOUR_RENDER_API_KEY>" }
```

Restart Codex after editing the config.
