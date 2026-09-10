# -*- coding: utf-8 -*-
"""悟道(quicktiny) MCP 直连客户端 —— 项目内接入，根治定时任务连挂。

背景（悟道数据源方案复盘_20260903.html + 2026-09-03 实测）：
  - 悟道只有 MCP 端点 https://stock.quicktiny.cn/api/mcp（JSON-RPC over POST，Bearer key），
    /api/openclaw REST 路径实测 404 不存在（报告推断值，需以 /api/mcp 为准）。
  - 9-02/9-03 定时任务连挂根因：项目级 .mcp.json 未含 mcp_wudao，定时任务环境加载不到全局 MCP 插件
    → 运行时调用 mcp_wudao 直接"不可达"。
  - 本客户端用 requests/urllib 直连 MCP 端点，绕开客户端插件层，任务指令改用 `python scripts/wudao_client.py`。

用法:
    from scripts.wudao_client import list_tools, execute_tool, wudao_call
    list_tools()                                          # 63 个工具名
    execute_tool("intraday_main_flow", {"codes": ["300475"]})
    wudao_call("market_overview", {})                     # 返回 result dict

CLI:
    python scripts/wudao_client.py tools/list
    python scripts/wudao_client.py intraday_main_flow '{"codes":["300475"],"format":"json"}'
"""
import json
import os
import sys
import time
import urllib.request

URL = "https://stock.quicktiny.cn/api/mcp"
_KEY = None
_TIMEOUT = 25

_MARKET_OPEN_RESTRICTED = (9 * 60 + 15, 10 * 60 + 30)


def _market_open_restricted():
    """悟道免费版盘中受限窗口（交易日 09:15-10:30，服务端 FREE_TIER_MARKET_OPEN_RESTRICTED）。"""
    now = time.localtime()
    if now.tm_wday >= 5:
        return False
    t = now.tm_hour * 60 + now.tm_min
    return _MARKET_OPEN_RESTRICTED[0] <= t < _MARKET_OPEN_RESTRICTED[1]


def _load_key():
    """从 config/data_source_keys.json 读悟道 key（Bearer）。"""
    global _KEY
    if _KEY:
        return _KEY
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", "..", "config", "data_source_keys.json")
    p = os.path.normpath(p)
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    _KEY = cfg["sources"]["wudao"]["key"]
    return _KEY


def _post(payload, timeout=_TIMEOUT):
    """POST JSON-RPC 到悟道 MCP 端点，返回完整响应 dict；失败抛异常。"""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + _load_key(),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wudao_call(method, params, rpc_id=1):
    """通用 JSON-RPC 调用（initialize/tools/list/tools/call）。返回 result 或抛异常。"""
    payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
    data = _post(payload)
    if "error" in data:
        raise RuntimeError("悟道 RPC 错误: %s" % json.dumps(data["error"], ensure_ascii=False))
    return data.get("result")


def list_tools():
    """返回工具名列表（63 个）。"""
    result = wudao_call("tools/list", {})
    return [t.get("name") for t in result.get("tools", [])]


def execute_tool(name, args=None):
    """调用工具，返回 text 内容字符串（JSON 文本）。"""
    result = wudao_call("tools/call", {"name": name, "arguments": args or {}})
    if result.get("isError"):
        raise RuntimeError("悟道工具 %s 执行失败: %s" % (name, json.dumps(result, ensure_ascii=False)))
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    return "\n".join(texts)


def main():
    if len(sys.argv) < 2:
        print("用法: python wudao_client.py <tool> [<args_json>]")
        return 2
    if _market_open_restricted():
        print("[SKIP] 悟道免费版盘中受限（09:15-10:30，FREE_TIER_MARKET_OPEN_RESTRICTED），跳过调用")
        return 0
    tool = sys.argv[1]
    try:
        if tool == "tools/list":
            names = list_tools()
            print(json.dumps(names, ensure_ascii=False, indent=2))
        else:
            args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
            print(execute_tool(tool, args))
        return 0
    except Exception as e:
        print("[WUDAO-ERR] %s" % repr(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
