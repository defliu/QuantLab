# -*- coding: utf-8 -*-
"""财汇(大智慧企业预警通) MCP 客户端 —— 项目内接入。

财汇 MCP 为 Streamable-HTTP 服务，key 在 config/data_source_keys.json 登记。
应用级 MCP（.workbuddy/.mcp.json）超出工作区可写范围，本模块提供等价的数据访问能力，
供 Project_16 策略脚本 / agent 直接调用（HTTP JSON-RPC + x-api-key）。

用法:
    from scripts.caihui_client import caihui_call, execute_tool, list_tools
    list_tools()                                   # 38 个一级工具
    execute_tool("get_company_basic_info", {...})  # 二级子工具
    caihui_call("query_stock_profile", {...})      # 一级工具
"""
import json
import os
import urllib.request

URL = "https://mcp.finchina.com/finchina-data-mcp-server/mcp"
_KEY = None


def _load_key():
    """从 config/data_source_keys.json 读财汇 key。"""
    global _KEY
    if _KEY:
        return _KEY
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", "..", "config", "data_source_keys.json")
    p = os.path.normpath(p)
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    _KEY = cfg["sources"]["caihui"]["key"]
    return _KEY


def _post(payload, timeout=30):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": _load_key(),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def list_tools():
    """返回全部一级工具名列表。"""
    resp = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [t["name"] for t in resp["result"]["tools"]]


def caihui_call(tool_name, arguments=None):
    """调用一级工具（tools/call）。arguments 为可选 dict。"""
    params = {"name": tool_name}
    if arguments:
        params["arguments"] = arguments
    resp = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    res = resp.get("result", {})
    if "error" in resp:
        raise RuntimeError(resp["error"])
    texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
    return "\n".join(texts) if texts else res


def execute_tool(tool_name, arguments):
    """执行二级子工具（财汇三层架构第 3 层）。"""
    return caihui_call("execute_tool", {"tool_name": tool_name, "arguments": arguments})


if __name__ == "__main__":
    tools = list_tools()
    print("[caihui] 一级工具数:", len(tools))
    # 连通性自检：查一只股票的资料
    out = caihui_call("query_stock_profile", {"stock_code": "600519"})
    print("[caihui] query_stock_profile 样例:")
    print(out[:500])
