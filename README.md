# ninjaone-mcp

**NinjaOne RMM** MCP server — exposes NinjaOne's Public API v2 (Organizations, Devices, Alerts, Ticketing, Automation/Scripting, Jobs) as MCP tools.

## What is NinjaOne / when would an agent use this

NinjaOne is an RMM (remote monitoring and management) platform MSPs use to manage clients' IT fleets. An agent should reach for this MCP for requests like:

- "How many devices does this customer have, and which are offline?" → `ninjaone_get_organization_devices` / `ninjaone_get_devices`
- "Any active alerts for this device/org?" → `ninjaone_get_device_alerts` / `ninjaone_get_alerts`
- "What tickets are open on the support board?" → `ninjaone_get_ticket_boards` then `ninjaone_get_tickets`
- "Run disk cleanup on this device and tell me when it's done" → `ninjaone_get_device_scripting_options` to confirm what's runnable, `ninjaone_run_script_on_device`, then `ninjaone_get_device_active_jobs` to watch it finish
- "What automation scripts do we have available?" → `ninjaone_get_automation_scripts`

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (Streamable HTTP transport) with **23 tools** across 5 groups, following the MSPbots **Vendor MCP Service SOP**: stateless, no stored credentials, per-request header authentication.

This was built starting from the community [wyre-technology/ninjaone-mcp](https://github.com/wyre-technology/ninjaone-mcp) project's tool surface (organizations/devices/alerts/tickets, reimplemented here directly against NinjaOne's REST API rather than its Node SDK) and extends it with 5 automation/scripting/jobs tools pulled from NinjaOne's own OpenAPI 3.0.1 spec — every endpoint below was checked against a real NinjaOne API spec, not guessed or copied from a secondary source.

**The gateway does the OAuth2 exchange, not this server.** NinjaOne authenticates via OAuth2 (client_credentials for a machine identity, refresh_token for a user identity — see below); this server takes only the already-exchanged bearer access token via header and calls NinjaOne's REST API directly with it. It never sees a client_id/client_secret/refresh_token, never talks to `/oauth/token`, and never caches anything — whoever operates the gateway is responsible for minting and refreshing tokens before they expire (NinjaOne's access tokens last 1 hour).

**One tool needs a second identity.** `ninjaone_run_script_on_device` is believed to be rejected by NinjaOne when called with a machine (API Services app) token regardless of scope, because NinjaOne ties script execution to a real user for its audit trail — this is the working hypothesis behind the design below, not yet independently confirmed against a real device/script by this repo. That tool alone takes a *second*, optional bearer token — one the gateway exchanged via the refresh_token grant against a NinjaOne "Web Application" app (which requires a one-time human browser authorization to obtain the refresh token in the first place). The other 22 tools are unaffected either way.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m ninjaone_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

No credentials are required for the health endpoint.

## 授权参数说明 (Authentication)

Every request to `/mcp` must include the following HTTP headers:

| Header | 类型 | 是否必填 | 默认值 | 枚举值 | 字段描述 | Example |
|---|---|---|---|---|---|---|
| `X-Ninja-Token` | string | 必填 | 无 | 无(自由文本) | **已经换好的** NinjaOne OAuth2 bearer access token(网关侧用 `client_credentials` grant 换出来的,机器身份)。本服务直接拿它打 NinjaOne API,不做任何换取/刷新——网关要负责在 token 过期前(1小时有效期)刷新好。 | `X-Ninja-Token: <access_token>` |
| `X-Ninja-Region` | string | 可选 | `us` | `us`, `eu`, `oc`, `ca`, `us2`, `fed` | NinjaOne 部署区域,决定实际请求的 base URL。 | `X-Ninja-Region: eu` |
| `X-Ninja-User-Token` | string | 可选(仅 `ninjaone_run_script_on_device` 需要) | 无 | 无(自由文本) | **已经换好的** NinjaOne OAuth2 bearer access token,但是网关用 `refresh_token` grant 针对一个 **"Web Application"** 类型 App 换出来的(用户身份,不是机器身份)。那个 refresh token 本身需要真人走一次浏览器授权(`grant_type=authorization_code`)才能拿到,是一次性的人工步骤,不在本服务运行时发生。 | `X-Ninja-User-Token: <access_token>` |

Missing `X-Ninja-Token` returns `401 Unauthorized`. Missing the optional `X-Ninja-User-Token` only affects `ninjaone_run_script_on_device` (returns a `not_configured` error) — every other tool works fine without it.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |

There is no base-URL env var — the base URL is derived per-request from the `X-Ninja-Region` header (see [config.py](src/ninjaone_mcp/config.py)'s region table).

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP)
- Headers: `X-Ninja-Token` (required, an already-exchanged bearer access token), `X-Ninja-Region` (optional), `X-Ninja-User-Token` (optional, only for `ninjaone_run_script_on_device`)

## Tool List

| Tool | 功能 | 参数 |
|---|---|---|
| `ninjaone_get_organizations` | 列出所有客户组织 | `limit?`, `after?` |
| `ninjaone_get_organization` | 按 ID 查单个组织详情 | `organization_id`(必填) |
| `ninjaone_create_organization` | 创建新组织 | `name`(必填), `description?`, `node_approval_mode?`, `tags?`, `template_organization_id?` |
| `ninjaone_get_organization_locations` | 列出组织下的站点(location) | `organization_id`(必填) |
| `ninjaone_get_organization_devices` | 列出组织下的设备 | `organization_id`(必填), `limit?`, `after?` |
| `ninjaone_get_devices` | 全局列出设备,支持 `df` 过滤表达式 | `df?`, `limit?`, `after?` |
| `ninjaone_get_device` | 按 ID 查单个设备详情 | `device_id`(必填) |
| `ninjaone_get_device_alerts` | 查单个设备的活跃告警 | `device_id`(必填) |
| `ninjaone_get_device_activities` | 查设备活动日志 | `device_id`(必填), `activity_type?`, `status?`, `older_than?`, `newer_than?`, `limit?` |
| `ninjaone_get_device_services` | 查设备的 Windows 服务列表 | `device_id`(必填), `name?`, `state?` |
| `ninjaone_reboot_device` | 重启设备(破坏性操作) | `device_id`(必填), `mode?`("NORMAL"/"FORCED",默认 NORMAL), `reason?` |
| `ninjaone_get_alerts` | 全局列出活跃告警 | `source_type?`, `df?` |
| `ninjaone_reset_alert` | 重置/关闭一条告警(破坏性操作) | `alert_uid`(必填), `activity_note?` |
| `ninjaone_get_ticket_boards` | 列出所有工单看板 | 无 |
| `ninjaone_get_tickets` | 按看板列出工单,支持状态/组织/设备过滤 | `board_id`(必填), `status?`, `organization_id?`, `device_id?`, `limit?`, `cursor?` |
| `ninjaone_create_ticket` | 创建新工单 | `summary`(必填), `organization_id`(必填), `description?`, `device_id?`, `location_id?`, `ticket_form_id?`, `status?`, `priority?`, `severity?`, `type?` |
| `ninjaone_update_ticket` | 更新工单字段和/或添加评论 | `ticket_id`(必填), `summary?`, `status?`, `priority?`, `assignee_id?`, `comment?`, `comment_public?` |
| `ninjaone_get_ticket_log_entries` | 查工单日志(描述/评论/变更历史) | `ticket_id`(必填), `entry_type?` |
| `ninjaone_get_automation_scripts` | 列出可用的自动化脚本 | 无 |
| `ninjaone_get_device_scripting_options` | 查设备上可运行的脚本/内置动作/凭据选项 | `device_id`(必填) |
| `ninjaone_run_script_on_device` | 在设备上运行脚本或内置动作(破坏性操作,需要 `X-Ninja-User-Token`) | `device_id`(必填), `type`(必填,"SCRIPT"/"ACTION"), `script_id?`, `action_uid?`, `parameters?`, `run_as?` |
| `ninjaone_get_active_jobs` | 全局列出正在运行/排队的任务 | `job_type?`, `df?` |
| `ninjaone_get_device_active_jobs` | 查单个设备正在运行/排队的任务 | `device_id`(必填) |

## 测试示例 (Test Example)

List ticket boards:

```json
{
  "method": "tools/call",
  "params": { "name": "ninjaone_get_ticket_boards", "arguments": {} }
}
```

Equivalent `curl` against the running server (streamable HTTP MCP endpoint):

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "X-Ninja-Token: <access_token>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": { "name": "ninjaone_get_ticket_boards", "arguments": {} }
  }'
```

Running a script on a device:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ninjaone_run_script_on_device",
    "arguments": { "device_id": 123, "type": "SCRIPT", "script_id": 456 }
  }
}
```

## API Reference

- Documentation: `https://app.ninjarmm.com/apidocs-beta/core-resources` (per-region equivalents for `eu`/`oc`/`ca`/`us2`/`fed`)
- Auth: OAuth2 (the gateway's job, not this server's) — `client_credentials` grant for the machine identity, `refresh_token` grant for the user identity, both at `POST /oauth/token`; scopes for the machine identity: `monitoring`, `management`, `control`

## Known Gaps / Implementation Notes

- **Endpoint provenance**: 4 of the 5 automation/scripting/jobs endpoints (`requestScriptingOptions`, `runScriptOnDevice`, `getActiveJobs`, `getDeviceActiveJobs`) were cross-checked against an independently obtained copy of NinjaOne's OpenAPI spec. `getAutomationScripts` wasn't present in that copy (it's newer than that spec revision) — its exact `/api` path placement is inferred from the other 4's confirmed pattern, not independently verified. See the comment at the top of [`tools/automation.py`](src/ninjaone_mcp/tools/automation.py).
- **`ninjaone_get_tickets` filters client-side**: NinjaOne's board-run endpoint's request schema defines `filters`/`searchCriteria` params, but the community wyre-technology project reports these 400 in practice — this tool always requests an unfiltered page and filters `status`/`organization_id`/`device_id` client-side instead.
- **No single-ticket-get or standalone add-comment endpoint**: NinjaOne's ticketing API doesn't expose a `GET /ticketing/ticket/{id}` — to look up one ticket, page through `ninjaone_get_tickets` on its board. Adding a comment isn't a separate endpoint either — it's folded into `ninjaone_update_ticket`'s `comment`/`comment_public` params, alongside a `PUT` on the ticket itself.
- **`ninjaone_get_devices`'s `df` filter can be silently dropped** by NinjaOne when scoping by organization (a known issue in the community project) — prefer `ninjaone_get_organization_devices` for an org-scoped device list.
- **Architecture history**: this server originally did its own OAuth2 exchange (took `client_id`/`client_secret` and called `/oauth/token` itself, per-request, never caching the result). That's since moved to the gateway — this server now only ever takes an already-exchanged bearer token (`X-Ninja-Token`) — matching the pattern MSPbots' gateway already uses for `ms-graph-mcp`/`connectwise-asio-mcp`. The gateway is responsible for the OAuth2 exchange and for refreshing tokens before their 1-hour expiry; if it doesn't, `X-Ninja-Token`/`X-Ninja-User-Token` requests will 401 against real NinjaOne endpoints (mapped to `unauthorized` here), not against this server's own logic.
- **`ninjaone_run_script_on_device` uses a second, user-context token** (`X-Ninja-User-Token`, gateway-exchanged via the refresh_token grant against a Web Application app) instead of the machine token every other tool uses — see the Overview section above for why. This is unverified against a real device/script so far; only the plumbing (missing-token error path, and a live call with a real machine-identity token reaching NinjaOne's real API and getting real data) has been checked.
- Verified against a live NinjaOne account: `tools/list` returns all 23 tools with clean schemas, `pytest` (17 tests) passes, and a real `ninjaone_get_organizations` call using a real, already-exchanged bearer token (via `X-Ninja-Token`) returned real organization data.
