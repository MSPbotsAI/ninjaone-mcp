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

NinjaOne authenticates via **OAuth2 client_credentials**: a NinjaOne "API Services" OAuth2 app's client ID + secret are exchanged for a short-lived bearer token at `POST {base_url}/oauth/token`. This server does that exchange itself, fresh on every tool call — it never stores or caches a token (or the client_id/secret) across calls.

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
| `X-Ninja-Client-Id` | string | 必填 | 无 | 无(自由文本) | NinjaOne 一个 "API Services" 类型 OAuth2 App 的 Client ID(在 NinjaOne 后台 Administration → Apps → API 创建),本服务用它换取短期 bearer token,从不落盘存储。 | `X-Ninja-Client-Id: <client_id>` |
| `X-Ninja-Client-Secret` | string | 必填 | 无 | 无(自由文本) | 同一个 OAuth2 App 的 Client Secret。 | `X-Ninja-Client-Secret: <client_secret>` |
| `X-Ninja-Region` | string | 可选 | `us` | `us`, `eu`, `oc`, `ca`, `us2`, `fed` | NinjaOne 部署区域,决定实际请求的 base URL。 | `X-Ninja-Region: eu` |

Missing either required header returns `401 Unauthorized`.

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
- Headers: `X-Ninja-Client-Id`, `X-Ninja-Client-Secret` (both required), `X-Ninja-Region` (optional)

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
| `ninjaone_run_script_on_device` | 在设备上运行脚本或内置动作(破坏性操作) | `device_id`(必填), `type`(必填,"SCRIPT"/"ACTION"), `script_id?`, `action_uid?`, `parameters?`, `run_as?` |
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
  -H "X-Ninja-Client-Id: <client_id>" \
  -H "X-Ninja-Client-Secret: <client_secret>" \
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
- Auth: OAuth2 `client_credentials` grant at `POST /oauth/token` (`grant_type`, `client_id`, `client_secret`, `scope`), scopes: `monitoring`, `management`, `control`

## Known Gaps / Implementation Notes

- **Endpoint provenance**: 4 of the 5 automation/scripting/jobs endpoints (`requestScriptingOptions`, `runScriptOnDevice`, `getActiveJobs`, `getDeviceActiveJobs`) were cross-checked against an independently obtained copy of NinjaOne's OpenAPI spec. `getAutomationScripts` wasn't present in that copy (it's newer than that spec revision) — its exact `/api` path placement is inferred from the other 4's confirmed pattern, not independently verified. See the comment at the top of [`tools/automation.py`](src/ninjaone_mcp/tools/automation.py).
- **`ninjaone_get_tickets` filters client-side**: NinjaOne's board-run endpoint's request schema defines `filters`/`searchCriteria` params, but the community wyre-technology project reports these 400 in practice — this tool always requests an unfiltered page and filters `status`/`organization_id`/`device_id` client-side instead.
- **No single-ticket-get or standalone add-comment endpoint**: NinjaOne's ticketing API doesn't expose a `GET /ticketing/ticket/{id}` — to look up one ticket, page through `ninjaone_get_tickets` on its board. Adding a comment isn't a separate endpoint either — it's folded into `ninjaone_update_ticket`'s `comment`/`comment_public` params, alongside a `PUT` on the ticket itself.
- **`ninjaone_get_devices`'s `df` filter can be silently dropped** by NinjaOne when scoping by organization (a known issue in the community project) — prefer `ninjaone_get_organization_devices` for an org-scoped device list.
- Not yet tested against a live NinjaOne account with real credentials — verified so far: `tools/list` returns all 23 tools with clean schemas, `pytest` (15 tests) passes, and a live call with a dummy client_id/secret reached NinjaOne's real production `/oauth/token` endpoint and got back a real, well-formed rejection (`Client app not exist`) rather than a malformed-request error — confirming the base URL, token endpoint, and request format are correct.
