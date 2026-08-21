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

**One tool needs a second identity.** `ninjaone_run_script_on_device` is believed to be rejected by NinjaOne when called with a machine (API Services app) token regardless of scope, because NinjaOne ties script execution to a real user for its audit trail — this is the working hypothesis behind the design below, not yet independently confirmed against a real device/script by this repo. That tool alone uses a *second*, optional credential set — a NinjaOne "Web Application" OAuth2 app's client ID + secret, plus a refresh token obtained once via that app's browser authorization (`grant_type=authorization_code`) — exchanged via `grant_type=refresh_token` on every call, the same "never cache" pattern as the machine credential. The other 22 tools are unaffected either way.

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
| `X-Ninja-Scopes` | string | 可选 | `monitoring management` | `monitoring`, `management`, `control`(空格或逗号分隔,可组合) | 要向 NinjaOne OAuth2 App 申请的 scope。**必须与该 App 实际被授予的 scope 完全匹配或是其子集**——多要一个没被授予的 scope,整个换 token 请求会被 NinjaOne 拒绝(400 `invalid_scope`),不会自动降级。本服务这 23 个工具都只需要 `monitoring`/`management`,不涉及 `control`(远程会话类),所以默认值不包含它;如果你的 App 只被授予了其中一个,必须显式传这个 header 精确指定。 | `X-Ninja-Scopes: monitoring` |
| `X-Ninja-User-Client-Id` | string | 可选(仅 `ninjaone_run_script_on_device` 需要) | 无 | 无(自由文本) | NinjaOne 一个 **"Web Application"** 类型 OAuth2 App 的 Client ID(不是 API Services App)。 | `X-Ninja-User-Client-Id: <client_id>` |
| `X-Ninja-User-Client-Secret` | string | 可选(同上) | 无 | 无(自由文本) | 同一个 Web Application App 的 Client Secret。 | `X-Ninja-User-Client-Secret: <client_secret>` |
| `X-Ninja-Refresh-Token` | string | 可选(同上) | 无 | 无(自由文本) | 该 Web Application App 一次性浏览器授权(`grant_type=authorization_code`,建议勾 `offline_access` scope)拿到的 refresh token。本服务用它换取代表那个登录用户的短期 bearer token(`grant_type=refresh_token`),权限跟随该用户在 NinjaOne 里的角色,与 App 自身的 scope 无关。 | `X-Ninja-Refresh-Token: <refresh_token>` |

Missing either of the two required headers returns `401 Unauthorized`. Missing the three optional user-identity headers only affects `ninjaone_run_script_on_device` (returns a `not_configured` error) — every other tool works fine without them.

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
- Headers: `X-Ninja-Client-Id`, `X-Ninja-Client-Secret` (both required), `X-Ninja-Region`, `X-Ninja-Scopes` (optional), `X-Ninja-User-Client-Id`, `X-Ninja-User-Client-Secret`, `X-Ninja-Refresh-Token` (optional, only for `ninjaone_run_script_on_device`)

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
| `ninjaone_run_script_on_device` | 在设备上运行脚本或内置动作(破坏性操作,需要 `X-Ninja-User-*`/`X-Ninja-Refresh-Token`) | `device_id`(必填), `type`(必填,"SCRIPT"/"ACTION"), `script_id?`, `action_uid?`, `parameters?`, `run_as?` |
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
- **Fixed**: an earlier version of this server always requested `monitoring management control` regardless of what the caller's OAuth2 App was actually granted, which 400s (`Invalid scope control for client`) for any App without `control` — nearly all of them, since none of these 23 tools need it. The default is now `monitoring management` (matching the community SDK's own default), and `X-Ninja-Scopes` lets a caller narrow further or add `control` if a future tool needs it.
- **`ninjaone_run_script_on_device` uses a second, user-context credential** (`X-Ninja-User-Client-Id`/`-Secret`/`X-Ninja-Refresh-Token`, a Web Application app + refresh_token grant) instead of the machine credential every other tool uses — see the Overview section above for why. This is unverified against a real device/script so far; only the plumbing (missing-creds error path, and a live token exchange attempt with dummy user credentials reaching NinjaOne's real `/oauth/token` and getting a genuine rejection rather than a malformed-request error) has been checked.
- Verified against a live NinjaOne account: `tools/list` returns all 23 tools with clean schemas, `pytest` (26 tests) passes, and a real `ninjaone_get_organizations` call with real credentials (no `X-Ninja-Scopes` header, exercising the default) returned real organization data.
