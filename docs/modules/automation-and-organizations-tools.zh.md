# 模块：automation / organizations 工具（`src/ninjaone_mcp/tools/`）

这两个工具注册模块的跨工单累积事实。英文版见同目录 `README.md`（内容须一致）。每张单追加一个带日期的小节，不改写早先的小节。

## PRD-19905 · 2026-10-09

锚点：`dev01.mbagent/PRD-19905@8aca01d` —— **尚未合入 `main`**（工作分支已推送，等待人工合入；见工单文档的「交接」一节）。合入之后要重新核对这些 path:line，因为行号可能会移位。

- `automation.py` 的 `ninjaone_get_automation_scripts` 与 `ninjaone_get_device_scripting_options`
  新增可选 `name_filter`（大小写不敏感子串，取数之后客户端过滤）—— `src/ninjaone_mcp/tools/automation.py:77-106` 与 `:108-130`。`ninjaone_get_automation_scripts` 还新增可选 `limit`/`after`，**只在调用方传了的时候**才透传给上游调用——裸的无参数调用已断言与 PRD-19905 之前的行为逐字节一致（测试：`tests/test_automation.py::test_no_filter_preserves_existing_truncation_behavior`）。
- `_filter_named_lists`（`src/ninjaone_mcp/tools/automation.py:52-61`）过滤**响应体里每一个"条目都带 name 键的顶层列表字段"**，不是硬编码某个具体 key（如 `"scripts"`）——这是刻意的选择，因为 `GET /device/{id}/scripting/options` 的真实响应结构（哪个 key 装脚本、哪个装动作）从未对真实 NinjaOne 租户核实过。如果以后哪张单拿到了真实 API 访问权限并确认了真实 key 名，这里是简化成直接按 key 取值的候选点。
- `organizations.py` 新增了 `ninjaone_get_organization_custom_fields(organization_id)` ——
  `src/ninjaone_mcp/tools/organizations.py:114-138`。**上游端点路径（`/api/v2/organization/{id}/custom-fields`）未对真实租户核实**——这是设计文档的暂定猜测，不是已确认事实。若以后真实调用发现路径不同，只需要改这一个函数。
- 本单之后工具计数是 24（此前是 23）；`tests/test_tools.py:14-63` 的 `EXPECTED_TOOLS`/`READ_ONLY_TOOLS` 字典是强制执行的清单——任何新工具或新的必填/可选参数都要在这里登记，否则 `test_tools_list_snapshot` 会红。
- 本仓没有 `AGENTS.md`/`CLAUDE.md`，`pyproject.toml` 也没有 `[tool.mypy]`/`[tool.ruff]`——`uv run pytest` 是唯一声明/强制执行的门禁（本单当时用 `tdd.py detect` 确认过）。
- 这两处改动要生效，需要在本分支合入 `main` 之后在 `mcp-service-gateway` 里手动跑一次 `vendor-deploy.sh update ninjaone`——`vendor/registry.yaml` 没有给 NinjaOne 锁 ref/commit，跑着的容器不会自动捡到这些改动。
