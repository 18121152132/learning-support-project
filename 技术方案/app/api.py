"""对外接口模块：基于 FastAPI + uvicorn，所有响应统一为 {"ok": bool, "data": ..., "error": {...}}。"""  # 模块说明：框架由用户指定，业务模块不感知 Web 层
# 身份从请求头取（演示级），生产须接组织认证；本模块不内置任何账号口令
import json  # 请求体解析与响应可序列化性预检
from urllib.parse import unquote  # HTTP 头只允许 latin-1，中文身份须 URL 编码后传输

from fastapi import FastAPI, Request  # 用户指定的 Web 框架与请求对象
from fastapi.exceptions import RequestValidationError  # 框架参数校验异常，转成统一结构
from fastapi.responses import HTMLResponse, JSONResponse  # 统一 JSON 响应对象与控制台网页响应
from pathlib import Path  # 定位项目内的控制台网页文件
from starlette.exceptions import HTTPException as StarletteHTTPException  # 404/405 等路由异常

from app.config_loader import get  # 路由前缀、健康路径、ASGI 参数均来自 config.yaml
from app.response import fail, ok, wrap_exception  # 统一返回结构


def _identity(request):  # 从请求头读取操作人与岗位
    """
    入参：request（FastAPI Request 对象）。
    出参：(actor, role) 二元组，缺失时为空字符串。
    失败时的行为：不抛异常；空身份交由权限层返回 UNAUTHENTICATED。
    """
    # 关键卡点：演示级身份不做账号校验，但必须保留岗位字段供六角色权限矩阵判定（f41）
    return unquote(request.headers.get("X-Actor", "")), unquote(request.headers.get("X-Role", ""))  # 中文姓名按 URL 编码解码


def _json_result(result, status=200):  # 输出统一结构响应
    """
    入参：result（统一结构 dict）、status（HTTP 状态码）。
    出参：JSONResponse 对象。
    失败时的行为：业务数据不可序列化时改输出 INVALID_RESPONSE 失败结构并返回 500，绝不返回非契约内容。
    """
    try:  # 先自行序列化，避免框架把不可序列化对象泄漏成半截响应
        json.dumps(result, ensure_ascii=False)  # 仅做可序列化性检查
    except (TypeError, ValueError) as exc:  # 捕获不可序列化数据
        return JSONResponse(content=fail("INVALID_RESPONSE", f"响应不可序列化: {exc}"), status_code=500)  # 降级为统一错误结构
    return JSONResponse(content=result, status_code=status)  # 业务失败仍用 200，契约靠 body 的 ok 字段


async def _body(request):  # 解析 JSON 请求体
    """
    入参：request（FastAPI Request 对象）。
    出参：dict（无请求体时返回空 dict）。
    失败时的行为：JSON 非法或非对象时抛 ValueError，由路由层转 INVALID_REQUEST，不猜测请求意图。
    """
    raw = await request.body()  # 读取原始字节
    if not raw:  # 无请求体
        return {}  # 返回空对象
    try:  # 捕获 JSON 解码错误
        parsed = json.loads(raw.decode("utf-8"))  # 按 UTF-8 解码并解析
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # 非法编码或非法 JSON
        raise ValueError(f"请求体不是合法 JSON: {exc}") from exc  # 上抛由路由层转统一结构
    if not isinstance(parsed, dict):  # 只允许对象体
        raise ValueError("请求体必须是 JSON 对象")  # 卡住数组/标量等异常输入
    return parsed  # 返回对象体


def _dispatch_get(service, config, path, query, actor, role):  # 分发 GET 请求
    """
    入参：service（AgentService）、config（配置 dict）、path、query（查询参数映射）、actor、role。
    出参：统一结构 dict。
    失败时的行为：未知路由返回 fail("NOT_FOUND")；业务异常由全局异常处理器收口。
    """
    prefix = str(get(config, "api.prefix", "/api/v1"))  # 路由前缀来自配置
    health_path = str(get(config, "api.health_path", "/health"))  # 健康检查路径来自配置
    if path == health_path:  # 健康检查不需要身份
        return ok({"status": "running", "framework": "fastapi", "config_version": get(config, "service.config_version")})  # 直接返回运行状态
    if path == prefix + "/status":  # 系统状态
        return service.status(actor, role)  # 走权限门禁
    if path == prefix + "/tickets":  # 单据清单
        return service.list_tickets(actor, role)  # 走权限门禁
    if path == prefix + "/repairs":  # 人工修复队列
        return service.repair_queue(actor, role)  # 走权限门禁
    if path == prefix + "/audit":  # 审计链
        return service.audit_chain(str(query.get("biz_id") or ""), actor, role)  # 走权限门禁
    if path == prefix + "/knowledge/query":  # ③ 知识图谱检索（兼容原路由）
        return service.knowledge_query(str(query.get("keyword") or ""), actor, role)  # 只读门禁
    if path == prefix + "/knowledge/graph":  # ③ 图谱统计（兼容原路由）
        return service.knowledge_graph(actor, role)  # 只读门禁
    if path == prefix + "/rag/query":  # ③ RAG 规则上下文检索
        return service.rag_query(str(query.get("query") or query.get("keyword") or ""), actor, role)  # 只读门禁
    if path == prefix + "/wiki/list":  # ③ Wiki 条目列表
        return service.wiki_list(actor, role, str(query.get("keyword") or "") or None)  # 只读门禁
    if path == prefix + "/wiki/detail":  # ③ Wiki 条目正文
        return service.wiki_detail(str(query.get("node_id") or ""), actor, role)  # 只读门禁
    if path == prefix + "/saas/tenants":  # ④ 可见租户
        return service.saas_tenants(actor, role)  # 只读门禁 + 租户隔离
    if path == prefix + "/saas/users":  # ④ 可见用户
        return service.saas_users(actor, role, str(query.get("tenant") or "") or None)  # 只读门禁 + 租户隔离
    return fail("NOT_FOUND", f"未知 GET 路由: {path}")  # 未知路由


def _dispatch_post(service, config, path, body, actor, role):  # 分发 POST 请求
    """
    入参：service、config、path、body（请求体 dict）、actor、role。
    出参：统一结构 dict。
    失败时的行为：缺 biz_id 返回 fail("INVALID_REQUEST")；未知路由返回 fail("NOT_FOUND")。
    """
    prefix = str(get(config, "api.prefix", "/api/v1"))  # 路由前缀来自配置
    biz_id = str(body.get("biz_id") or "")  # 多数动作以业务编号为主键
    if path == prefix + "/cycle":  # 触发一个采集识别周期
        return service.run_cycle(actor, role, body)  # 支持 simulate_outage
    if path == prefix + "/suggestions/review":  # 外部建议安全审查
        return service.review_unsafe_suggestion(actor, role, body)  # 命中红线可暂停试点
    if path == prefix + "/saas/users/assign":  # ④ 分配/调整用户（无 biz_id，先处理）
        return service.saas_assign(actor, role, body)  # 分层门禁 + 审计
    if path == prefix + "/saas/users/tier":  # ④ 调整用户层级（无 biz_id，先处理）
        return service.saas_change_tier(actor, role, body)  # 分层门禁 + 审计
    if path == prefix + "/saas/users/remove":  # ④ 移除用户（无 biz_id，先处理）
        return service.saas_remove(actor, role, str(body.get("target_actor") or ""))  # 分层门禁 + 审计
    if not biz_id:  # 其余动作必须有业务编号
        return fail("INVALID_REQUEST", "请求体缺少 biz_id")  # 卡住无主键操作
    if path == prefix + "/handoff/confirm":  # 接手确认
        return service.confirm_handoff(biz_id, actor, role)  # 走门禁与确认
    if path == prefix + "/contact/confirm":  # 人工确认联系
        return service.confirm_contact(biz_id, actor, role)  # 走门禁与确认
    if path == prefix + "/contact":  # 联系学生
        return service.contact_student(biz_id, actor, role)  # 走门禁与本人校验
    if path == prefix + "/signoff":  # 专业签核
        return service.professional_signoff(biz_id, actor, role)  # 走签核人校验
    if path == prefix + "/retrospective":  # 复盘记录
        return service.record_retrospective(biz_id, actor, role)  # 走复盘责任人校验
    if path == prefix + "/pilot/resume":  # 恢复试点
        return service.resume_pilot(biz_id, actor, role)  # 走判定人与复盘校验
    return fail("NOT_FOUND", f"未知 POST 路由: {path}")  # 未知路由


def create_app(service, config):  # 创建绑定具体服务的 FastAPI 应用
    """
    入参：service（AgentService 实例）、config（配置 dict）。
    出参：FastAPI 应用对象。
    失败时的行为：配置缺路由前缀时用默认前缀；不吞掉业务异常，由全局异常处理器转统一结构。
    """
    prefix = str(get(config, "api.prefix", "/api/v1"))  # 路由前缀来自配置
    health_path = str(get(config, "api.health_path", "/health"))  # 健康检查路径来自配置
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # 关闭自动文档，演示级身份不对外暴露接口清单

    @app.exception_handler(StarletteHTTPException)  # 404/405 等路由级异常
    async def handle_http_exception(request, exc):  # 路由异常转统一结构
        """
        入参：request（Request）、exc（StarletteHTTPException）。
        出参：JSONResponse 统一结构。
        失败时的行为：不抛异常；404 返回 NOT_FOUND，其余按 HTTP_ERROR 返回。
        """
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"  # 区分未知路由与其他 HTTP 错误
        return _json_result(fail(code, str(exc.detail)), exc.status_code)  # 契约不破

    @app.exception_handler(RequestValidationError)  # 框架参数校验异常
    async def handle_validation_exception(request, exc):  # 校验异常转统一结构
        """
        入参：request（Request）、exc（RequestValidationError）。
        出参：JSONResponse 统一结构（400）。
        失败时的行为：不抛异常；错误文本不回传请求体，避免泄漏输入内容。
        """
        return _json_result(fail("INVALID_REQUEST", "请求参数校验失败"), 400)  # 统一错误码

    @app.exception_handler(Exception)  # 未预期异常兜底
    async def handle_unexpected_exception(request, exc):  # 兜底异常转统一结构
        """
        入参：request（Request）、exc（未预期异常对象）。
        出参：JSONResponse 统一结构（500）。
        失败时的行为：不抛异常；只回传异常类型与文本，不泄漏堆栈与配置。
        """
        return _json_result(wrap_exception(exc), 500)  # 收口为统一结构

    @app.get(health_path)  # 健康检查
    async def health():  # 无需身份的存活探测
        """
        入参：无。
        出参：JSONResponse 统一结构，data 含 status/framework/config_version。
        失败时的行为：不读取状态文件，不因业务状态异常而失败。
        """
        return _json_result(_dispatch_get(service, config, health_path, {}, "", ""))  # 复用同一分发逻辑

    # 控制台网页：浏览器访问根路径时返回可操作的单页控制台；脚本/接口调用仍返回 JSON 概览
    console_path = Path(__file__).resolve().parent.parent / "web" / "index.html"  # 网页文件位置，来自项目结构
    console_html = ""  # 缓存网页正文，避免每次请求读盘
    try:  # 构建时一次性读取，文件缺失时降级为空串
        if console_path.is_file():  # 仅当文件存在时读取
            console_html = console_path.read_text(encoding="utf-8")  # 按 UTF-8 读取网页
    except OSError:  # 读盘失败不阻断服务启动
        console_html = ""  # 降级为空串，根路径退回 JSON 概览

    @app.get("/")  # 根路径：浏览器显示控制台，接口调用返回服务概览
    async def index(request: Request):  # 按 Accept 头区分网页与接口
        """
        入参：request（依据 Accept 头区分浏览器与脚本访问）。
        出参：浏览器返回 HTML 控制台；否则返回统一结构 JSON 概览。
        失败时的行为：网页文件缺失或读取失败时退回 JSON 概览，不因静态资源异常而失败。
        """
        accept = request.headers.get("accept", "")  # 读取客户端可接受的内容类型
        wants_html = "text/html" in accept and bool(console_html)  # 浏览器且网页可用才返回网页
        if wants_html:  # 浏览器访问
            return HTMLResponse(content=console_html)  # 直接返回缓存的单页控制台
        overview = {"name": get(config, "service.name", ""), "framework": "fastapi", "config_version": get(config, "service.config_version"), "health": health_path, "api_prefix": prefix, "endpoints": [health_path, prefix + "/status", prefix + "/cycle", prefix + "/tickets", prefix + "/knowledge/query", prefix + "/rag/query", prefix + "/wiki/list", prefix + "/saas/tenants"], "console": "/?sharecode=<分享码>" if console_html else None, "note": "本系统仅提示不自动执行；联系学生须人工确认，知识库不构成行为证据"}  # 静态概览，避免线上根路径 404
        return _json_result(ok(overview))  # 返回统一结构

    @app.get(prefix + "/status")  # 系统状态
    async def status(request: Request):  # 查看试点状态
        """
        入参：request（携带 X-Actor / X-Role 请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权返回权限失败结构，不泄漏业务状态。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.status(actor, role))  # 走权限门禁

    @app.get(prefix + "/tickets")  # 单据清单
    async def tickets(request: Request):  # 查看全部单据
        """
        入参：request（携带身份请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权返回权限失败结构，不返回单据内容。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.list_tickets(actor, role))  # 走权限门禁

    @app.get(prefix + "/repairs")  # 人工修复队列
    async def repairs(request: Request):  # 查看脏数据队列
        """
        入参：request（携带身份请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权返回权限失败结构，队列内容不外泄。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.repair_queue(actor, role))  # 走权限门禁

    @app.get(prefix + "/audit")  # 审计链查询
    async def audit(request: Request):  # 按业务编号查链路
        """
        入参：request（查询参数 biz_id，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权或缺链路时返回失败结构，不返回半截链路。
        """
        actor, role = _identity(request)  # 取身份
        result = _dispatch_get(service, config, prefix + "/audit", dict(request.query_params), actor, role)  # 复用分发
        return _json_result(result)  # 输出统一结构

    @app.get(prefix + "/knowledge/query")  # 知识图谱只读检索
    async def knowledge_query(request: Request):  # 按关键字查节点与一跳关联
        """
        入参：request（查询参数 keyword，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权、关键字为空或图谱未构建时返回失败结构，不输出全图。
        """
        actor, role = _identity(request)  # 取身份
        word = str(request.query_params.get("keyword") or "")  # 取查询关键字
        return _json_result(service.knowledge_query(word, actor, role))  # 走只读门禁

    @app.get(prefix + "/knowledge/graph")  # 知识图谱统计
    async def knowledge_graph(request: Request):  # 查看规模与来源
        """
        入参：request（身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权或图谱不可用时返回失败结构，不编造统计数字。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.knowledge_graph(actor, role))  # 走只读门禁

    @app.get(prefix + "/model/status")  # 本地模型部署状态
    async def model_status(request: Request):  # 查看 provider 与可达性
        """
        入参：request（身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权返回权限失败结构；探测异常由服务层标 reachable=False，不泄漏密钥。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.model_status(actor, role))  # 走只读门禁

    @app.get(prefix + "/rag/query")  # ③ RAG 规则上下文检索
    async def rag_query(request: Request):  # 查询规则上下文
        """
        入参：request（查询参数 query，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权、空查询或语料不可用时返回失败结构；检索结果非行为证据。
        """
        actor, role = _identity(request)  # 取身份
        word = str(request.query_params.get("query") or request.query_params.get("keyword") or "")  # 取查询词
        return _json_result(service.rag_query(word, actor, role))  # 只读门禁

    @app.get(prefix + "/wiki/list")  # ③ LLM Wiki 条目列表
    async def wiki_list(request: Request):  # 列出条目
        """
        入参：request（可选 keyword，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权或 Wiki 未构建时返回失败结构。
        """
        actor, role = _identity(request)  # 取身份
        word = str(request.query_params.get("keyword") or "")  # 取过滤词
        return _json_result(service.wiki_list(actor, role, word or None))  # 只读门禁

    @app.get(prefix + "/wiki/detail")  # ③ Wiki 条目正文
    async def wiki_detail(request: Request):  # 按编号取正文
        """
        入参：request（查询参数 node_id，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权、空编号或条目不存在时返回失败结构。
        """
        actor, role = _identity(request)  # 取身份
        node_id = str(request.query_params.get("node_id") or "")  # 取节点编号
        return _json_result(service.wiki_detail(node_id, actor, role))  # 只读门禁

    @app.get(prefix + "/saas/tenants")  # ④ 可见租户
    async def saas_tenants(request: Request):  # 列出租户
        """
        入参：request（身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：身份未授权时返回失败结构；非跨租户用户只见本租户。
        """
        actor, role = _identity(request)  # 取身份
        return _json_result(service.saas_tenants(actor, role))  # 门禁 + 租户隔离

    @app.get(prefix + "/saas/users")  # ④ 可见用户
    async def saas_users(request: Request):  # 列出用户
        """
        入参：request（可选 tenant，身份在请求头）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权或跨租户被拒时返回失败结构；结果脱敏。
        """
        actor, role = _identity(request)  # 取身份
        tenant = str(request.query_params.get("tenant") or "")  # 取租户过滤
        return _json_result(service.saas_users(actor, role, tenant or None))  # 门禁 + 租户隔离

    async def _post(request, path):  # 复用 POST 处理：解析请求体、分发、收口错误
        """
        入参：request（Request）、path（路由路径）。
        出参：JSONResponse 统一结构。
        失败时的行为：请求体非法返回 400；未知路由返回 404；业务异常由全局处理器返回 500。
        """
        actor, role = _identity(request)  # 取身份
        try:  # 捕获请求体解析错误
            body = await _body(request)  # 解析 JSON 对象
        except ValueError as exc:  # 非法请求体
            return _json_result(fail("INVALID_REQUEST", str(exc)), 400)  # 统一错误结构
        result = _dispatch_post(service, config, path, body, actor, role)  # 分发业务动作
        code = (result.get("error") or {}).get("code") if isinstance(result, dict) else None  # 取错误码
        status = 404 if code == "NOT_FOUND" else 200  # 业务失败仍用 200
        return _json_result(result, status)  # 输出统一结构

    @app.post(prefix + "/cycle")  # 采集识别周期
    async def cycle(request: Request):  # 触发一个周期
        """
        入参：request（可选 simulate_outage 字段）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权或试点暂停时返回失败结构，不产生业务事件。
        """
        return await _post(request, prefix + "/cycle")  # 复用处理

    @app.post(prefix + "/handoff/confirm")  # 接手确认
    async def handoff_confirm(request: Request):  # 确认接手
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：缺编号或无待确认交接单时返回失败结构。
        """
        return await _post(request, prefix + "/handoff/confirm")  # 复用处理

    @app.post(prefix + "/contact/confirm")  # 人工确认联系
    async def contact_confirm(request: Request):  # 人工确认
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：未授权、试点暂停或无挂起建议时返回失败结构。
        """
        return await _post(request, prefix + "/contact/confirm")  # 复用处理

    @app.post(prefix + "/contact")  # 联系学生
    async def contact(request: Request):  # 不可逆对外动作
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：未经人工确认或非确认人本人时返回失败结构，绝不产生对外动作。
        """
        return await _post(request, prefix + "/contact")  # 复用处理

    @app.post(prefix + "/signoff")  # 专业签核
    async def signoff(request: Request):  # 签核动作
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：非指定签核人本人一律返回权限失败结构并留痕。
        """
        return await _post(request, prefix + "/signoff")  # 复用处理

    @app.post(prefix + "/suggestions/review")  # 建议安全审查
    async def suggestions_review(request: Request):  # 审查外部建议
        """
        入参：request（请求体须含 suggestion 对象）。
        出参：JSONResponse 统一结构。
        失败时的行为：缺对象返回 INVALID_REQUEST；命中红线时拦截并可暂停试点。
        """
        return await _post(request, prefix + "/suggestions/review")  # 复用处理

    @app.post(prefix + "/retrospective")  # 复盘记录
    async def retrospective(request: Request):  # 记录复盘
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：非复盘责任人返回权限失败结构。
        """
        return await _post(request, prefix + "/retrospective")  # 复用处理

    @app.post(prefix + "/pilot/resume")  # 恢复试点
    async def pilot_resume(request: Request):  # 判定恢复
        """
        入参：request（请求体须含 biz_id）。
        出参：JSONResponse 统一结构。
        失败时的行为：非判定人、未复盘或未暂停时返回失败结构。
        """
        return await _post(request, prefix + "/pilot/resume")  # 复用处理

    @app.post(prefix + "/saas/users/assign")  # ④ 分配/调整用户
    async def saas_assign(request: Request):  # 分配用户
        """
        入参：request（请求体含 target_actor/tenant/tier）。
        出参：JSONResponse 统一结构。
        失败时的行为：缺参、分层不足或写入失败时返回失败结构；每步留痕。
        """
        return await _post(request, prefix + "/saas/users/assign")  # 复用处理

    @app.post(prefix + "/saas/users/tier")  # ④ 调整用户层级
    async def saas_change_tier(request: Request):  # 调整层级
        """
        入参：request（请求体含 target_actor/tier）。
        出参：JSONResponse 统一结构。
        失败时的行为：缺参、分层不足或写入失败时返回失败结构；每步留痕。
        """
        return await _post(request, prefix + "/saas/users/tier")  # 复用处理

    @app.post(prefix + "/saas/users/remove")  # ④ 移除用户
    async def saas_remove(request: Request):  # 移除用户
        """
        入参：request（请求体含 target_actor）。
        出参：JSONResponse 统一结构。
        失败时的行为：缺参、目标受保护或写入失败时返回失败结构；每步留痕。
        """
        return await _post(request, prefix + "/saas/users/remove")  # 复用处理

    return app  # 返回应用对象，由 server.py 交给 uvicorn
