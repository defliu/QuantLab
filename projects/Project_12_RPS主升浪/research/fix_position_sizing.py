# coding: utf-8
"""批量更新所有 RPS 配置的 position_sizing 为 custom。"""
import yaml
import os

CONFIG_DIR = "D:/QuantLab/projects/Project_12_RPS主升浪/config"
files = [f for f in os.listdir(CONFIG_DIR) if f.endswith(".yaml")]

for fname in files:
    path = os.path.join(CONFIG_DIR, fname)
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sp = cfg.get("strategy_params", {})
    if "position_sizing" in sp and sp["position_sizing"] != "custom":
        old = sp["position_sizing"]
        sp["position_sizing"] = "custom"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print("[UPDATED] %s: %s -> custom" % (fname, old))
    elif "position_sizing" not in sp:
        # 没有则加默认 custom
        sp["position_sizing"] = "custom"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print("[ADDED] %s: position_sizing=custom" % fname)
    else:
        print("[SKIP] %s: already custom" % fname)
