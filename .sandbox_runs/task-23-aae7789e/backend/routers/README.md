# backend/routers 说明

本目录用于存放 FastAPI 路由模块。目标是将 `backend/main.py` 保持为应用入口与装配层，避免业务路由再次集中到单文件。

## 设计原则

- `main.py` 只负责应用初始化、middleware、异常处理、静态资源挂载和 `include_router`。
- 业务路由按功能拆分到 `routers/*.py`。
- 路由函数尽量保持“薄路由”，共享依赖统一从 `backend/core.py` 导入。
- 拆分优先“迁移而非重写”，减少行为变化风险。

## 文件职责

- `auth.py`
  - 登录、鉴权相关 HTTP 接口。
  - 示例：`/api/login`、`/api/ping`。

- `servers.py`
  - 服务器资产管理、连接测试、最近连接、进程管理、状态历史接口。
  - 示例：`/api/servers/*`、`/api/servers/{server_id}/process/kill`。

- `ai.py`
  - AI endpoint、角色、技能、翻译、技能商店相关 HTTP 接口。
  - 示例：`/api/ai_endpoints/*`、`/api/roles/*`、`/api/skills/*`。

- `system.py`
  - 系统设置、日志管理、数据库备份、代理配置、设备类型/快捷命令管理、首页路由。
  - 示例：`/api/system_settings`、`/api/logs/*`、`/api/proxies/*`、`/`。

- `ws.py`
  - WebSocket 相关路由与处理逻辑（SSH、AI、状态采集）。
  - 示例：`/ws/ssh/{server_id}`、`/ws/ai`、`/ws/stats/{server_id}`。

- `sftp.py`
  - SFTP 文件管理接口（列表、下载、上传、重命名、chmod、删除、读取、保存、创建）。
  - 示例：`/api/sftp/*`。

## 共享依赖来源

统一从 `backend/core.py` 导入以下内容，避免重复定义和循环依赖：

- 全局对象：`db`
- 鉴权：`verify_token`、`verify_ws_token`
- 常量：如 `SERVER_NOT_FOUND_DETAIL`、`SKILL_NOT_FOUND_DETAIL`
- 共享模型：如 `ServerModel`、`RoleModel`、`Sftp*Model`
- 通用工具：如 `_get_ssh_config`、`_get_skills_proxy`、`get_log_path`

## 新增路由规范

新增接口时请遵循：

1. 先判断归属模块，放到对应 `routers/*.py`。
2. 如需新领域，新增独立路由文件（例如 `audit.py`），并在 `main.py` 中 `include_router`。
3. 不在路由文件中重复创建数据库实例或重复定义鉴权函数。
4. 保持接口路径、请求参数和返回结构向后兼容；若必须变更，先补迁移说明。

## 快速自检

修改后建议执行：

- `python -m py_compile backend/main.py backend/core.py backend/routers/*.py`

并确认：

- `backend/main.py` 中不再出现业务路由装饰器（如 `@app.get(...)` 的业务接口）。
