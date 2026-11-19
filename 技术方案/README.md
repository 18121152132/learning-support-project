# 学习支持与辅导协作智能体 · 本次工作成果

## 线上地址
https://learning-support-agent.app.workbuddy.host/?sharecode=QMWX2K5PS2

> 必须带 `?sharecode=QMWX2K5PS2`，去掉会返回「链接不完整」。

## 技术栈
FastAPI + Python 3.11+ + 自研 Harness 回放壳；零外部中间件；本地知识图谱 / RAG / LLM Wiki；六角色 RBAC + SaaS 多租户。所有响应统一为 `{ok, data, error}`。

## 本次新增 / 修改
- `web/index.html`：单文件控制台网页（原生 HTML/CSS/JS，零框架零外部依赖）。按五大模块组织：系统概览、业务闭环、人工确认动作、知识能力（图谱/RAG/Wiki）、权限与租户，外加运行验证入口。顶部切换操作人与岗位，自动带身份头和分享码，点按钮调接口并展示统一响应；点击工单可自动填入业务编号。
- `app/api.py`：根路由按 `Accept` 头区分——浏览器访问返回控制台网页，脚本/接口调用仍返回原 JSON 概览，不破坏统一结构与红线逻辑。

## 业务红线（仍然生效）
只提示不自动执行；联系学生须人工确认且本人执行；知识库/RAG 仅作规则解释上下文，不构成行为证据；越权拦截留痕；停止条件暂停试点；审计链可重建。

## 验证结果
- `python -m compileall`：通过
- `scripts/verify.py`：63 项全过
- `scripts/verify_stage1.py` / `verify_stage2.py`：通过
- `scripts/verify_harness.py`：13 项通过
- 本地实测：`status` / `cycle` 正常（触发周期产生采集、修复与连续求助工单）
- 线上复验：浏览器访问根路径 HTTP 200 返回网页，带身份头访问 `/api/v1/status` 正常返回 JSON

## 使用
打开线上地址即可看到可点击操作的控制台网页。选择操作人和岗位后，在各模块点「执行」调用接口；触发采集识别周期后，可在工单清单里点工单自动填入业务编号，继续做接手确认、人工确认、联系学生、签核等操作。
