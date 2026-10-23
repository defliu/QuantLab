# -*- coding: utf-8 -*-
"""信号加载 —— 从 P16 产物读取每日候选池。

两个策略的信号产物结构不同：
  G2  : data/selections/g2/{date}_g2_top10.csv   列 ts_code / prob / total_new
        ⚠️ 目录内同时存在 _g2_top2/_g2_top3/_g2_top10，是同一天的候选池+截断结果，
           统一读 top10 再按 top_n 截断，避免同日多文件重复建仓
  V13 : data/selections/{date}_final_list.csv    列 ts_code / total / redline_pass

两者都只提供「当日候选」，不提供「今日是否换仓」的显式信号。
换仓语义复刻 scan_rotate_cost_real.simulate：
  - 每日按候选池补足持仓到 top_n（持仓未满就买，不是全量换仓）
  - 已持有的票不重复买
  - 卖出只由出场规则（MATURE/STOP/TP/TRAIL）触发，不是"掉出候选就卖"
"""
import glob
import os
import re

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../Project_23_每日模拟盘/paper
_PROJ23 = os.path.dirname(_HERE)                            # .../Project_23_每日模拟盘
ROOT = os.path.dirname(os.path.dirname(_PROJ23))            # d:/QuantLab


def signal_path(cfg, date):
    """返回某日信号文件绝对路径。

    支持候选文件后缀列表（patterns）：按顺序取第一个存在的文件。
    原因：G2 产物命名随 TOP_N 拍板变更过 ——
      2026-08-17 ~ 09-01 只有 _g2_top2.csv
      2026-09-02 起     同时有 _g2_top2/_g2_top3/_g2_top10.csv
    统一优先读 top10（候选池最大），缺失时回退 top3/top2，
    以保证持仓只数由 cfg.top_n 控制，而不是由文件名控制。
    """
    d = os.path.join(ROOT, cfg["signal_dir"])
    pats = cfg.get("signal_patterns") or [cfg["signal_pattern"]]
    for p in pats:
        fp = os.path.join(d, p.format(date=date.strftime("%Y%m%d")))
        if os.path.isfile(fp):
            return fp
    # 全都不存在时返回首选路径（调用方用 isfile 判定为 None）
    return os.path.join(d, pats[0].format(date=date.strftime("%Y%m%d")))


def load_candidates(cfg, date):
    """加载某日候选池，返回 DataFrame[ts_code, score, ...]，按 score 降序；无文件返回 None。

    date: pd.Timestamp 或 str
    """
    d = pd.Timestamp(date)
    p = signal_path(cfg, d)
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p, encoding="utf-8-sig", dtype={"ts_code": str})
    if len(df) == 0:
        return pd.DataFrame(columns=["ts_code", "score"])

    score_col = cfg["score_col"]
    if score_col not in df.columns:
        raise KeyError("信号文件 %s 缺少评分列 %s（实际列：%s）"
                       % (os.path.basename(p), score_col, list(df.columns)))

    # 红线过滤（V1.3 有 redline_pass 列时优先用它；G2 直接按分数阈值）
    if "redline_pass" in df.columns:
        df = df[df["redline_pass"].astype(str).str.lower() == "true"]
    else:
        df = df[df[score_col] >= float(cfg["redline"])]

    out = df.copy()
    out["score"] = out[score_col].astype(float)
    out = out.sort_values("score", ascending=False)
    # 截断到 top_n（候选池 top10 → 目标持仓 top_n）
    out = out.head(int(cfg["top_n"]))
    return out[["ts_code", "score"]].reset_index(drop=True)


def available_dates(cfg):
    """列出信号目录中所有可用日期（升序 pd.Timestamp 列表）。

    扫描 signal_patterns 中所有后缀，合并去重（同日多文件只算一天）。
    """
    d = os.path.join(ROOT, cfg["signal_dir"])
    if not os.path.isdir(d):
        return []
    pats = cfg.get("signal_patterns") or [cfg["signal_pattern"]]
    out = []
    for p in pats:
        suffix = p.split("{date}", 1)[1]
        rx = re.compile(r"^(\d{8})" + re.escape(suffix) + r"$")
        for fn in os.listdir(d):
            m = rx.match(fn)
            if m:
                try:
                    out.append(pd.Timestamp(m.group(1)))
                except Exception:
                    continue
    return sorted(set(out))
