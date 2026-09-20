# coding=utf-8
"""因子挖掘管线 - 数据普查（S0 前置）

对方向 A（另类数据：北向/龙虎榜/研报）与方向 B（财务 8 表）做字段/覆盖/质量普查，
输出 research_factor_mining/survey_report.md，供候选表达式设计前置确认。

用法：
  D:\\Python311\\python.exe research_factor_mining/survey_data.py
"""
import os, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "survey_report.md")

DAILY = r"D:\astock\daily\stock_daily.parquet"
BASIC = r"D:\astock\basic\stock_basic.parquet"
HK = r"D:\astock\northbound\hk_hold_full.parquet"
LHB = r"D:\astock\lhb\top_list.parquet"
RES = r"D:\astock\research\report_rc_daily.parquet"
FIN_DIR = r"D:\astock\finance"

lines = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    lines.append(s)

def h1(t): lines.append("\n## %s\n" % t)
def p(t): lines.append(t)


def load_index(df, n=0):
    """尝试解析 DataFrame 的 index 为 (date, code) 视图并返回 (date_series, code_series)。"""
    if isinstance(df.index, pd.MultiIndex):
        lv = df.index.levels
        if len(lv) >= 2:
            return df.index.get_level_values(0), df.index.get_level_values(1)
    return None, None


def main():
    t0 = time.time()
    log("# 因子挖掘数据普查报告\n")
    log("- 生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("- 口径: 覆盖/质量仅作候选设计前置，不涉及回测结论\n")

    # ---------- 1. 主库 daily ----------
    h1("1. 主库 daily（stock_daily.parquet）")
    daily = pd.read_parquet(DAILY)
    idx = daily.index
    dts = pd.to_datetime(idx.get_level_values("trade_date"))
    codes = idx.get_level_values("ts_code")
    log("- 行数: %d | 日期: %s ~ %s | 股票数: %d"
        % (len(daily), dts.min().date(), dts.max().date(), codes.nunique()))
    log("- 列: %s" % daily.columns.tolist())
    # 与增量数据源 date 对齐范围（train 2021-2023 / oos 2024-2026）
    for seg, a, b in [("训练段2021-23", "2021-01-01", "2023-12-31"),
                      ("样本外2024-26", "2024-01-01", "2026-09-11")]:
        m = (dts >= a) & (dts <= b)
        log("- %s 覆盖日期 %s ~ %s, 行数 %d"
            % (seg, dts[m].min().date() if m.any() else "—",
               dts[m].max().date() if m.any() else "—", int(m.sum())))

    # ---------- 2. 方向 A 三源 ----------
    h1("2. 方向 A：另类数据三源")

    def probe(src_name, path, date_cols, code_cols, key_note):
        p("\n### %s" % src_name)
        p("- 路径: `%s`" % path)
        if not os.path.isfile(path):
            p("- ❌ 文件不存在")
            return None
        df = pd.read_parquet(path)
        p("- 行数: %d | 列: %s" % (len(df), df.columns.tolist()[:20]))
        date_s = None
        for dc in date_cols:
            if dc in df.columns:
                date_s = pd.to_datetime(df[dc], errors="coerce")
                break
        if date_s is None and isinstance(df.index, pd.MultiIndex):
            date_s = pd.to_datetime(df.index.get_level_values(0), errors="coerce")
        if date_s is not None and date_s.notna().any():
            date_ser = date_s if isinstance(date_s, pd.Series) else pd.Series(date_s)
            date_ser = pd.to_datetime(date_ser, errors="coerce").dropna()
            p("- 日期: %s ~ %s | 唯一交易日 %d"
              % (date_ser.min().date(), date_ser.max().date(),
                 date_ser.dt.normalize().nunique()))
            for seg, a, b in [("训练段2021-23", "2021-01-01", "2023-12-31"),
                              ("样本外2024-26", "2024-01-01", "2026-09-11")]:
                m = (date_ser >= a) & (date_ser <= b)
                p("  - %s 覆盖: %s ~ %s, %d 行"
                  % (seg, date_ser[m].min().date() if m.any() else "—",
                     date_ser[m].max().date() if m.any() else "—", int(m.sum())))
        code_s = None
        for cc in code_cols:
            if cc in df.columns:
                code_s = df[cc]
                break
        if code_s is None and isinstance(df.index, pd.MultiIndex):
            code_s = df.index.get_level_values(1)
        if code_s is not None:
            p("- 覆盖股票数: %d | %s" % (code_s.nunique(), key_note))
        return df

    probe("北向持仓 hk_hold_full", HK, ["trade_date"], ["ts_code", "code"],
          "字段: ratio(持股比例)/vol(持股数) → 可构造持股比例变动")
    probe("龙虎榜 top_list", LHB, ["trade_date"], ["ts_code", "code"],
          "字段: net_amount/net_rate/l_amount/l_sell → 可构造席位净买入")
    probe("研报 report_rc_daily", RES, ["report_date"], ["ts_code"],
          "字段: rating/eps/tp/np → 可构造覆盖数/评级变动")

    # ---------- 3. 方向 B 财务 8 表 ----------
    h1("3. 方向 B：财务 8 表（PIT 锚 ann_date/f_ann_date）")
    for f in sorted(os.listdir(FIN_DIR)):
        if not f.endswith(".parquet") or ".bak" in f:
            continue
        path = os.path.join(FIN_DIR, f)
        df = pd.read_parquet(path, columns=None)
        has_ann = "ann_date" in df.columns
        has_fann = "f_ann_date" in df.columns
        has_end = "end_date" in df.columns
        end_s = pd.to_datetime(df["end_date"], errors="coerce") if has_end else None
        p("\n### %s" % f)
        p("- 行数: %d | 列数: %d | PIT锚: ann_date=%s f_ann_date=%s"
          % (len(df), len(df.columns), has_ann, has_fann))
        if end_s is not None and end_s.notna().any():
            p("- 报告期: %s ~ %s" % (end_s.min().date(), end_s.max().date()))
        if has_ann:
            ann = pd.to_datetime(df["ann_date"], errors="coerce")
            p("- ann_date 覆盖率: %.1f%% (%d/%d)"
              % (ann.notna().mean() * 100, int(ann.notna().sum()), len(df)))
        # 列抽样（去掉通用锚列）
        core = [c for c in df.columns if c not in
                ("ts_code", "end_date", "ann_date", "f_ann_date",
                 "report_type", "comp_type", "update_flag")]
        p("- 业务字段样例(前18): %s" % core[:18])

    log("\n---\n总用时 %.1fs" % (time.time() - t0))

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("报告已写出:", OUT)


if __name__ == "__main__":
    main()
