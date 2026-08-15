# coding: utf-8
"""Web 搜索/抓取工具（Tavily + Firecrawl），key 从 config/.web_search_keys.yaml 读取（gitignore）。

用法：
  python scripts/web_search.py search "低波动 策略 优化 A股" [--max 5]
  python scripts/web_search.py extract "https://... " [--urls "u1|u2"]
  python scripts/web_search.py scrape "https://..." [--max-chars 3000]
  python scripts/web_search.py fc_search "low volatility strategy enhancement" [--max 5]

代理：默认读环境 HTTPS_PROXY/HTTP_PROXY；也可 --proxy 指定，0=直连。
"""
import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_keys():
    import yaml
    p = os.path.join(PROJECT_ROOT, "config", ".web_search_keys.yaml")
    with open(p, "r", encoding="utf-8") as f:
        d = yaml.safe_load(f)
    return d.get("tavily_key"), d.get("firecrawl_key")


def _set_proxy(args):
    if args.proxy == "0":
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("ALL_PROXY", None)
    elif args.proxy:
        os.environ["HTTPS_PROXY"] = args.proxy
        os.environ["HTTP_PROXY"] = args.proxy


def cmd_search(args, tkey):
    from tavily import TavilyClient
    c = TavilyClient(api_key=tkey)
    r = c.search(query=args.query, max_results=args.max, search_depth="advanced")
    for i, res in enumerate(r.get("results", []), 1):
        print("[%d] %s" % (i, res.get("title", "")))
        print("    URL: %s" % res.get("url", ""))
        print("    %s" % (res.get("content", "") or "")[:300])
        print()


def cmd_extract(args, tkey):
    from tavily import TavilyClient
    c = TavilyClient(api_key=tkey)
    urls = [u for u in args.urls.split("|") if u.strip()]
    r = c.extract(urls=urls)
    for res in r.get("results", []):
        print("==== %s ====" % res.get("url", ""))
        print((res.get("raw_content") or "")[:args.max_chars])
        print()


def cmd_scrape(args, fkey):
    from firecrawl import FirecrawlApp
    app = FirecrawlApp(api_key=fkey)
    r = app.scrape(args.url, formats=["markdown"], only_main_content=True)
    md = getattr(r, "markdown", None) or (r.get("markdown") if isinstance(r, dict) else None) or ""
    print(md[:args.max_chars])


def cmd_fc_search(args, fkey):
    from firecrawl import FirecrawlApp
    app = FirecrawlApp(api_key=fkey)
    r = app.search(args.query, limit=args.max)
    items = getattr(r, "web", None) or getattr(r, "data", None) or []
    if not items and isinstance(r, dict):
        items = r.get("web") or r.get("data") or []
    for i, res in enumerate(items, 1):
        def _g(obj, k):
            if isinstance(obj, dict):
                return obj.get(k)
            return getattr(obj, k, None)
        print("[%d] %s" % (i, _g(res, "title")))
        print("    URL: %s" % _g(res, "url"))
        desc = _g(res, "description") or ""
        print("    %s" % desc[:300])
        print()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap.add_argument("--proxy", default=None, help="0=直连 / http://127.0.0.1:7897")

    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--max", type=int, default=5)
    p = sub.add_parser("extract"); p.add_argument("urls"); p.add_argument("--max-chars", type=int, default=3000)
    p = sub.add_parser("scrape"); p.add_argument("url"); p.add_argument("--max-chars", type=int, default=3000)
    p = sub.add_parser("fc_search"); p.add_argument("query"); p.add_argument("--max", type=int, default=5)

    args = ap.parse_args()
    _set_proxy(args)
    tkey, fkey = load_keys()

    if args.cmd == "search":
        cmd_search(args, tkey)
    elif args.cmd == "extract":
        cmd_extract(args, tkey)
    elif args.cmd == "scrape":
        cmd_scrape(args, fkey)
    elif args.cmd == "fc_search":
        cmd_fc_search(args, fkey)


if __name__ == "__main__":
    main()
