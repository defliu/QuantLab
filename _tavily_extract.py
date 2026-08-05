import urllib.request, json, sys

def tavily_extract(urls):
    data = json.dumps({
        "api_key": "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",
        "urls": urls
    }).encode()
    req = urllib.request.Request("https://api.tavily.com/extract", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req).read().decode()
    return json.loads(resp)

urls = [
    "https://www.premia-partners.com/insight/china-a-shares-q1-2026-factor-review",
    "https://www.premia-partners.com/insight/china-a-shares-q4-2025-factor-review"
]
d = tavily_extract(urls)
for r in d.get("results", []):
    print("--- URL:", r.get("url","") , "---")
    content = r.get("raw_content", "")[:2000]
    print(content)
    print()
