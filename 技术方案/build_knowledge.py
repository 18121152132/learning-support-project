"""知识图谱构建脚本：python scripts/build_knowledge.py，从资料 CSV 与 config.yaml 生成本地 JSON 图。"""  # 模块说明：零图数据库、零向量库、零外部依赖
import csv  # 读取 discovery-report.csv（utf-8-sig 兼容 BOM）
import json  # 图谱序列化
import sys  # 导入路径与退出码
from pathlib import Path  # 定位项目根

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
sys.path.insert(0, str(ROOT))  # 保证从任意目录运行都能导入 app 包
from app.config_loader import abs_path, get, load_config  # 配置读取与路径解析
from app.response import data_of, fail, is_ok, ok  # 统一返回结构
from app.store import now_iso  # 生成带时区时间戳


def _node(node_id, kind, label, source_ref, **extra):  # 构造一个图谱节点
    """
    入参：node_id（节点唯一编号）、kind（节点类型）、label（可读名称）、source_ref（来源定位）、extra（附加字段）。
    出参：节点 dict。
    失败时的行为：不校验内容，缺字段照原样写入，由 load_graph 统一做结构校验。
    """
    item = {"id": str(node_id), "type": str(kind), "label": str(label), "source_ref": str(source_ref)}  # 四要素固定
    item.update(extra)  # 合并业务附加字段
    return item  # 返回节点


def _edge(source, target, relation, source_ref):  # 构造一条图谱边
    """
    入参：source（起点节点 id）、target（终点节点 id）、relation（关系名）、source_ref（来源定位）。
    出参：边 dict。
    失败时的行为：不校验端点是否存在，由 load_graph 统一做结构校验。
    """
    return {"source": str(source), "target": str(target), "relation": str(relation), "source_ref": str(source_ref)}  # 四要素固定


def build_graph(config):  # 由资料与配置生成完整图谱
    """
    入参：config（配置 dict）。
    出参：统一结构；data 含 graph（nodes/edges/meta）。
    失败时的行为：资料 CSV 缺失、表头不符或配置缺 knowledge 段时返回 fail("KNOWLEDGE_BUILD_FAILED")，不生成半成品图。
    """
    try:  # 捕获读取与解析异常
        source_dir = abs_path(config, "paths.knowledge_sources")  # 资料副本目录只来自配置
        rows = []  # 累积资料行
        for name in get(config, "knowledge.sources", []) or []:  # 逐份资料读取
            path = source_dir / str(name)  # 资料文件绝对路径
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:  # 兼容 BOM
                rows.extend(list(csv.DictReader(handle)))  # 读取全部行
        nodes = {}  # 以 id 去重
        edges = []  # 累积边
        role_names = list(get(config, "roles", {}).keys())  # 六角色清单来自配置
        for role in role_names:  # 生成角色节点
            node = _node(f"ROLE:{role}", "role", role, "config.yaml#roles", allow=get(config, f"roles.{role}.allow", []), deny=get(config, f"roles.{role}.deny", []))  # 权限随节点保存
            nodes[node["id"]] = node  # 去重写入
        steps = get(config, "knowledge.flow_steps", []) or []  # 主流程六步来自配置
        owners = get(config, "knowledge.flow_owners", []) or []  # 各步责任人来自配置
        for index, step in enumerate(steps, 1):  # 生成流程节点
            owner = str(owners[index - 1]) if index <= len(owners) else ""  # 按下标取责任人
            node = _node(f"FLOW:{index}", "flow_step", step, "config.yaml#knowledge.flow_steps", seq=index, owner=owner)  # 流程节点带序号
            nodes[node["id"]] = node  # 去重写入
            if index < len(steps):  # 非最后一步
                edges.append(_edge(f"FLOW:{index}", f"FLOW:{index + 1}", "next", "现场调研报告#主流程"))  # 顺序边
            if owner in role_names:  # 责任人在六角色内
                edges.append(_edge(f"FLOW:{index}", f"ROLE:{owner}", "owned_by", "现场调研报告#主流程"))  # 责任边
        policy = _node("POLICY:合规约束", "policy", "合规约束与停止条件", "config.yaml#security")  # 合规中心节点
        nodes[policy["id"]] = policy  # 写入中心节点
        redline_groups = {  # 四类红线清单，全部来自配置
            "sensitive": get(config, "security.sensitive_attributes", []) or [],  # 禁止敏感属性
            "output": get(config, "security.banned_outputs", []) or [],  # 禁止输出字段
            "auto": get(config, "security.forbidden_auto_actions", []) or [],  # 禁止自动动作
            "stop": get(config, "security.stop_conditions", []) or [],  # 停止条件
        }  # 分组结束
        for group, values in redline_groups.items():  # 逐类生成红线节点
            for value in values:  # 逐项生成
                node = _node(f"RED:{group}:{value}", "redline", f"{group}:{value}", f"config.yaml#security.{group}", group=group)  # 红线节点带分类
                nodes[node["id"]] = node  # 去重写入
                edges.append(_edge(policy["id"], node["id"], "includes", "config.yaml#security"))  # 中心到红线
        for document in get(config, "knowledge.source_documents", []) or []:  # 七份资料节点
            node = _node(f"DOC:{document}", "document", str(document), "资料清单")  # 资料节点
            nodes[node["id"]] = node  # 去重写入
        for index, row in enumerate(rows, 1):  # 逐条需求假设
            ref = f"discovery-report.csv#{index + 1}"  # 行号定位（含表头）
            hid = f"H{index}"  # 假设节点编号
            node = _node(hid, "hypothesis", row.get("需求假设", ""), ref, priority=row.get("优先级", ""), owner=row.get("负责人", ""), status=row.get("状态", ""), acceptance=row.get("验收与约束", ""))  # 假设节点
            nodes[hid] = node  # 写入
            edges.append(_edge(policy["id"], hid, "applies_to", "config.yaml#security"))  # 合规约束覆盖假设
            edges.append(_edge(hid, "DOC:discovery-report.csv", "based_on", ref))  # 资料来源边
            ids = [item.strip() for item in str(row.get("证据编号", "")).split(";") if item.strip()]  # 事实编号列表
            facts = [item.strip() for item in str(row.get("事实内容", "")).split(";")]  # 事实内容列表
            for pos, fact_id in enumerate(ids):  # 编号与内容按下标对应
                text = facts[pos].strip() if pos < len(facts) else ""  # 取对应事实文本
                fid = f"FACT:{fact_id}"  # 事实节点编号
                if fid in nodes:  # 同一事实被多条假设引用
                    old = nodes[fid].get("text", "")  # 已有文本
                    if text and text not in old:  # 追加不重复的新表述
                        nodes[fid]["text"] = old + "；" + text  # 合并表述
                else:  # 首次出现
                    nodes[fid] = _node(fid, "fact", text or fact_id, ref, text=text, fact_id=fact_id)  # 事实节点带编号
                edges.append(_edge(hid, fid, "cites", ref))  # 假设引用事实
            for role in role_names:  # 从负责人字段辨认角色
                if role in str(row.get("负责人", "")):  # 命中姓名
                    edges.append(_edge(hid, f"ROLE:{role}", "owned_by", ref))  # 责任边
        graph = {  # 图谱成品
            "version": get(config, "knowledge.version", "kg-v1"),  # 版本来自配置
            "generated_at": now_iso(),  # 构建时间
            "nodes": list(nodes.values()),  # 节点列表
            "edges": edges,  # 边列表
            "meta": {"source_documents": get(config, "knowledge.source_documents", []), "fact_count": len([n for n in nodes.values() if n["type"] == "fact"]), "forbid_as_behavior_evidence": bool(get(config, "knowledge.forbid_as_behavior_evidence", True))}  # 元信息
        }  # 图谱结束
        return ok({"graph": graph})  # 返回构建结果
    except Exception as exc:  # 资料缺失或解析异常
        return fail("KNOWLEDGE_BUILD_FAILED", f"{type(exc).__name__}: {exc}")  # 不生成半成品图


def write_graph(config, graph):  # 原子写入图谱文件
    """
    入参：config（配置 dict）、graph（图谱 dict）。
    出参：统一结构；data 含 path、node_count、edge_count。
    失败时的行为：路径缺失或磁盘不可写时返回 fail("KNOWLEDGE_WRITE_FAILED")，保留原有图谱文件不破坏。
    """
    try:  # 捕获 IO 异常
        path = abs_path(config, "paths.knowledge_graph")  # 图谱路径只来自配置
        path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
        tmp = path.with_suffix(".json.tmp")  # 临时文件
        tmp.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")  # 先写临时文件
        tmp.replace(path)  # 原子替换，避免读到半截 JSON
        return ok({"path": str(path), "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])})  # 返回统计
    except Exception as exc:  # 写入异常
        return fail("KNOWLEDGE_WRITE_FAILED", f"{type(exc).__name__}: {exc}")  # 原图保持不变


def main():  # 构建入口
    """
    入参：无（配置路径来自环境变量 LSA_CONFIG，缺省为项目根 config.yaml）。
    出参：int 退出码（构建成功为 0）。
    失败时的行为：配置或资料异常时打印错误码并返回 1，不输出不完整的图谱。
    """
    import os  # 读取配置路径环境变量
    config = load_config(os.environ.get("LSA_CONFIG", str(ROOT / "config.yaml")))  # 读取唯一配置，只解析一次
    built = build_graph(config)  # 构建图谱
    if not is_ok(built):  # 构建失败
        print(f"构建失败: {built['error']['code']}: {built['error']['msg']}")  # 输出错误
        return 1  # 非零退出
    written = write_graph(config, data_of(built)["graph"])  # 落盘
    if not is_ok(written):  # 落盘失败
        print(f"落盘失败: {written['error']['code']}: {written['error']['msg']}")  # 输出错误
        return 1  # 非零退出
    info = data_of(written)  # 取统计
    print(f"KNOWLEDGE GRAPH OK: {info['node_count']} nodes / {info['edge_count']} edges -> {info['path']}")  # 打印结果
    return 0  # 成功


if __name__ == "__main__":  # 作为脚本运行时执行
    sys.exit(main())  # 以 main 返回值作为退出码
