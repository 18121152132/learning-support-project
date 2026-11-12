"""harness 验证脚本：python scripts/verify_harness.py，验证运行壳隔离、白名单、回放与 CLI。"""  # 模块说明：全部状态写入临时目录，不改项目 data/state 业务记录
import copy  # 深拷贝配置，只覆盖报告路径
import json  # 解析回放报告与 CLI JSON 输出
import os  # 设置子进程控制台编码
import shutil  # 清理临时目录
import subprocess  # 调用 python -m harness 命令行
import sys  # 解释器路径、导入路径与退出码
import tempfile  # 创建独立临时目录
from pathlib import Path  # 路径处理

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
sys.path.insert(0, str(ROOT))  # 保证从任意目录运行都能导入 app 与 harness 包
from app.config_loader import get, load_config  # 配置加载
from app.response import data_of, is_ok  # 统一返回结构
from app.service import AgentService  # 业务服务，用于白名单直调验证
from harness.cases import BUILTIN_CASES  # 内置用例集
from harness.core import run_action  # 动作白名单执行器
from harness.gates import assert_case, envelope_shape  # 用例校验与结构断言
from harness.replay import replay  # 回放执行器

PASSED = []  # 累计通过的检查项


def check(name, condition, detail=""):  # 断言并打印一项检查结果
    """
    入参：name（检查项名称）、condition（布尔结果）、detail（失败时补充说明）。
    出参：无。
    失败时的行为：任一检查不通过即抛 AssertionError，脚本以非零码退出，不静默跳过。
    """
    if not condition:  # 结果不为真
        raise AssertionError(f"{name} 未通过: {detail}")  # 立即失败
    PASSED.append(name)  # 记录通过项
    print(f"[PASS] {name}")  # 输出通过项


def make_config():  # 构造报告路径指向临时目录的配置副本
    """
    入参：无。
    出参：(config, tmp_dir) 二元组。
    失败时的行为：config.yaml 不可读时由 load_config 抛 ConfigError，脚本直接失败。
    """
    config = copy.deepcopy(load_config(str(ROOT / "config.yaml")))  # 读取唯一配置并深拷贝
    tmp = Path(tempfile.mkdtemp(prefix="lsa-harness-verify-"))  # 创建临时目录
    config["harness"]["report_path"] = str(tmp / "harness_report.json")  # 报告落临时目录
    config["paths"]["state_dir"] = str(tmp / "state")  # 所有业务产物也落临时目录
    config["paths"]["events_log"] = str(tmp / "state" / "events.jsonl")  # 事件日志落临时目录
    config["paths"]["audit_log"] = str(tmp / "state" / "audit.jsonl")  # 审计日志落临时目录
    config["paths"]["repair_queue"] = str(tmp / "state" / "repair_queue.jsonl")  # 修复队列落临时目录
    config["paths"]["snapshot"] = str(tmp / "state" / "snapshot.json")  # 只读快照落临时目录
    graph_path = ROOT / "data" / "knowledge" / "graph.json"  # 知识图谱属于只读交付数据
    if not graph_path.exists():  # 尚未构建
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_knowledge.py")], cwd=str(ROOT), check=True, capture_output=True, text=True)  # 自动构建
    return config, tmp  # 返回配置与临时目录


def cli_config(tmp):  # 生成给子进程使用的临时配置文件
    """
    入参：tmp（临时目录 Path）。
    出参：临时配置文件 Path。
    失败时的行为：原配置中未找到 report_path 行时抛 AssertionError，拒绝用错配置跑 CLI。
    """
    raw = (ROOT / "config.yaml").read_text(encoding="utf-8")  # 读取原配置全文
    old_line = "report_path: data/state/harness_report.json"  # 待替换的报告路径行
    assert old_line in raw  # 配置契约必须存在
    new_line = "report_path: " + str(tmp / "cli_report.json").replace("\\", "/")  # 绝对路径，统一用正斜杠
    path = tmp / "config.yaml"  # 临时配置文件位置
    path.write_text(raw.replace(old_line, new_line), encoding="utf-8")  # 写出配置副本
    return path  # 返回路径


def main():  # 执行 harness 全部验证项
    """
    入参：无。
    出参：int 退出码（全部通过为 0）。
    失败时的行为：任一检查失败抛 AssertionError 并打印原因；finally 中清理临时目录。
    """
    config, tmp = make_config()  # 构造独立报告路径
    expected = len(BUILTIN_CASES)  # 用例总数由用例集决定，新增用例不必改脚本
    try:  # 保证异常时清理
        check("harness 配置段齐全", bool(get(config, "harness.enabled")) and int(get(config, "harness.max_cases")) >= expected)  # 配置生效
        report = replay(config)  # 第一轮回放
        check(f"首轮回放 {expected}/{expected} 通过", is_ok(report) and data_of(report)["total"] == expected and data_of(report)["failed"] == 0, str(report.get("error")))  # 全量通过
        body = data_of(report)  # 取报告
        check("报告标注 FastAPI 与 Python 3.11+", body["framework"] == "fastapi" and tuple(int(x) for x in body["python"].split(".")[:2]) >= (3, 11))  # 技术栈落地
        check("逐用例隔离且审计证据可取", all(item["isolated"] for item in body["cases"]) and any(item["evidence"]["denied_audit"] for item in body["cases"]))  # 隔离与越权留痕
        paused_cases = [item for item in body["cases"] if "停止条件" in item["name"]]  # 找停止条件用例
        check("停止条件用例确实暂停试点", len(paused_cases) == 1 and paused_cases[0]["evidence"]["paused"] is True)  # f42 生效
        later = [item for item in body["cases"] if "试点状态" in item["name"]]  # 停止条件之后的用例
        check("隔离保证暂停不污染后续用例", len(later) == 1 and later[0]["passed"] is True)  # 回放可重复
        report_file = Path(body["report_path"])  # 报告文件
        check("报告落盘且 JSON 可解析", report_file.exists() and json.loads(report_file.read_text(encoding="utf-8"))["total"] == expected)  # 落盘完整
        second = replay(config)  # 第二轮回放，验证可重复
        check("重复回放结果一致", is_ok(second) and data_of(second)["failed"] == 0 and data_of(second)["total"] == expected)  # 幂等
        service = AgentService(config)  # 构造服务用于白名单直调
        denied = run_action(service, {"action": "drop_all_records"}, config)  # 未登记动作
        check("白名单外动作被运行壳拒绝", envelope_shape(denied) and denied["error"]["code"] == "UNSUPPORTED_ACTION")  # 任意方法调用被卡
        invalid = assert_case({"name": "坏用例", "action": "status"}, config)  # 缺 expect 的用例
        check("残缺用例不静默跳过", not is_ok(invalid) and invalid["error"]["code"] == "INVALID_CASE")  # 用例合法性
        check("内置用例数不超过配置上限", len(BUILTIN_CASES) <= int(get(config, "harness.max_cases")))  # 上限生效
        child_config = cli_config(tmp)  # 生成子进程配置
        env = dict(os.environ, PYTHONIOENCODING="utf-8")  # 子进程统一 UTF-8 输出
        listed = subprocess.run([sys.executable, "-m", "harness", "--config", str(child_config), "--list"], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)  # 列表模式
        check(f"CLI --list 列出 {expected} 条用例", listed.returncode == 0 and len([line for line in listed.stdout.splitlines() if line.strip()]) == expected, listed.stderr)  # CLI 可用
        one = subprocess.run([sys.executable, "-m", "harness", "--config", str(child_config), "--case", "匿名请求", "--json"], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)  # 单用例 JSON 模式
        check("CLI --case --json 只跑一条", one.returncode == 0 and json.loads(one.stdout)["total"] == 1, one.stderr)  # 筛选与输出
        print(f"HARNESS OK: {len(PASSED)}")  # 输出总计
        return 0  # 全部通过
    except AssertionError as exc:  # 任一检查失败
        print(f"HARNESS VERIFY FAILED: {exc}")  # 输出失败项
        return 1  # 非零退出码
    finally:  # 无论成败都清理
        shutil.rmtree(str(tmp), ignore_errors=True)  # 只清理本次创建的临时目录


if __name__ == "__main__":  # 作为脚本运行时执行
    sys.exit(main())  # 以 main 返回值作为退出码
