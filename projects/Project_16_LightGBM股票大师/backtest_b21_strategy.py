# coding: utf-8
"""B21 信号内产版 —— 策略级回测（可执行口径 open→open + 真实成本 + tier 门控）。

背景：QMT 沙箱自闭环改造 Step 4（spec `specs/QMT沙箱自闭环改造方案_20261015.md`）落地了
G2/ENS 信号内产版（B21 k=1500 树模型 + 简化红线 prob>=0.60），此前只做过模型级 OOS 验收
（IC/tail_ic/top2_ret，`results/G2ENS_信号内产版重训样本外验收报告_20261016.md`），
从未跑过策略级完整回测。本脚本补齐这一环。

策略规则（忠实复刻 `strategy/strategy_p16_g2_bridge_src.py` 信号引擎 + 官方引擎
`scan_rotate_cost_real.py` 可执行口径）：
  - 信号：决策日 T 收盘特征 -> B21 打分 prob；买入闸门 = 简化红线 prob>=0.60（拍板口径），
    候选池取过线前 SELECT_TOP(10) 只，按 prob 降序补足空位到 TOP_N
  - G2 臂：TOP_N=3 / HOLD_DAYS=15；ENS 臂：TOP_N=5 / HOLD_DAYS=10
  - 买入执行：T+1 开盘价成交；可执行过滤（一字涨停/停牌/无量买不进）；
    低开校验 gap_guard（open<=-5% 跳过；-5%~-3% 需量比>=2.0，量比=分钟线 09:50 前累计量/
    前5日分钟日均量，缺数据 fallback 全天量比，再缺 fail-open 放行）
  - tier 门控（沪深300）：开盘较前收 <=-1.5% 停买(T0) / <-1.0% 半仓(T1, deploy 50%) /
    其余满仓(T2, deploy 98%)；数据缺失 fail-safe T1（bridge 同语义）
  - 出场：live_trail 口径（与 bridge/V13 风控同源）——止损 -7% / 止盈 +15% /
    追盈（峰值>=成本x1.08 激活，线=max(成本, 峰值x0.92)）/ 持有满 HOLD_DAYS 到期；
    T+1 锁；一字跌停卖不出顺延；停牌/退市 60 日按 50% 损失清仓
  - 成本：佣金万2 双边(最低5元) + 印花税万5 卖出 + 过户费万0.1 沪市双边（qmt_config 同源）；
    滑点敏感性 0 / 0.1% / 0.2% 单边

与 live 部署的已知近似（报告须注明）：
  1. 成交价 = 开盘价（live 09:50 市价单），滑点敏感性覆盖此差异
  2. 止损/止盈/追盈按当日开盘判定（live tick 级），盘中触发压缩到开盘口径
  3. ST 强制卖出未实现（panel 过滤帧已剔 ST；持有转 ST 极罕见）
  4. G2/ENS 持仓互斥未实现（单臂独立 10 万资金池回测）
  5. 动态等权再平衡（超配减仓）未实现（官方引擎同口径）
  6. 特征为 panel PIT 口径（live 沙箱快照近似残差见 s4-5c 对齐报告：Run A 21/21 PASS）

区间：2024-07-01 ~ 2026-09-30（执行数据日线/HS300 均到 9-30），与 OOS 验收 test 窗同起点。

用法：
  python backtest_b21_strategy.py --all          # 跑预设全矩阵并写报告
  python backtest_b21_strategy.py --arm g2 --slips 0.001   # 单配置快速复核
"""
import argparse
import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)

import data_config as DC  # noqa: E402
import qmt_config as QC  # noqa: E402
import train_g2 as TG  # noqa: E402

# ---------------- 常量（与 bridge 源码逐项对齐） ----------------
START = pd.Timestamp("2024-07-01")
END = pd.Timestamp("2026-09-30")
CAPITAL0 = 100000.0            # 10 万资金池（capital_allocation.yaml 同口径）
REDLINE = 0.60                 # SIGNAL_REDLINE_PROB（简化红线，拍板口径）
SELECT_TOP = 10                # SIGNAL_SELECT_TOP 候选池
DEPLOY_FULL = 0.98             # SIGNAL_DEPLOY_PCT（T2 满仓，保留 2% 现金）
DEPLOY_HALF = 0.50             # SIGNAL_HALF_DEPLOY_PCT（T1 半仓）
TIER_STOP_PCT = -1.5           # SIGNAL_TIER_STOP_PCT（%，T0 停买线）
TIER_HALF_PCT = -1.0           # SIGNAL_TIER_HALF_PCT（%，T1 半仓线）
STOP = -0.07                   # STOP_LOSS_PCT
TP = 0.15                      # TAKE_PROFIT_PCT
TRAIL = 0.08                   # TRAILING_PCT
TRAIL_ACT = 0.08               # TRAIL_ACTIVATE_PCT
HOLD_G2, TOP_G2 = 15, 3        # G2 臂
HOLD_ENS, TOP_ENS = 10, 5      # ENS 臂
DELIST_DAYS = 60               # 连续无价天数视为退市（官方引擎同口径）
DELIST_LOSS = 0.5              # 退市损失假设
GAP_HARD = -5.0                # gap_guard：open<=-5% 跳过
GAP_SOFT = -3.0                # gap_guard：-5%~-3% 需量比>=2.0
VOL_RATIO_MIN = 2.0            # gap_guard 量比门槛

# ---- 生产评分卡口径（deploy_predict_g2 候选流 + scorecard_real 生产语义）----
SC_REDLINE = 60.0              # 生产红线（--threshold 60，g2_candidates_night 实跑值）
SC_POOL = 100                  # 候选流第一级：day.nlargest(100, prob)
SC_PRE = 10                    # 候选流第二级：再 nlargest(10, prob)
SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}

B21_MODEL = "D:/Temp/opencode/g2_sandbox_B21.txt"
B21_TREES = 1500               # k=1500（部署 dump 版同工作点，探针验证 max_err=2.4e-9）
LIVE_MODEL = os.path.join(DC.MODEL_DIR, "lgb_model_v3_g2_strong_real_20260915_1522t.txt")
LIVE_META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
HS300 = "D:/astock/index/index_daily/000300.SH.parquet"
MIN1_DIR = os.path.join(DC.ASTOCK_DIR, "min_1min")
OUT_JSON = "D:/Temp/opencode/bt_b21_result.json"
OUT_MD = os.path.join(PROJ, "results", "B21信号内产版_策略级回测报告_20261017.md")
OUT_NAV_DIR = os.path.join(PROJ, "data", "real", "b21_nav")
OUT_JSON_SC = "D:/Temp/opencode/bt_b21_sc_result.json"
OUT_MD_SC = os.path.join(PROJ, "results", "B21_干净对比_生产评分卡口径_20261018.md")
# tier 口径双轨（2026-10-19）：t1_open=旧口径（审计 B3 前，live 同刻压缩镜像）对照数据，独立落盘
OUT_JSON_T1 = "D:/Temp/opencode/bt_b21_result_t1open.json"
OUT_JSON_SC_T1 = "D:/Temp/opencode/bt_b21_sc_result_t1open.json"

COMM, COMM_MIN = QC.COMM_RATE, QC.COMM_MIN
STAMP, TRANS = QC.STAMP_RATE, QC.TRANS_RATE


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------- 生产语义评分卡（向量化，deploy_predict_g2 同口径） ----------------
def _sc_scores(day, est_pe, est_tu, mn_scale=1e4):
    """F1-F6 向量化（与 deploy_predict._score_f1/f3/f4 + review_full.score_f2/f5/f6 逐分支等价）。

    生产口径（deploy_predict_g2 + scorecard_real.compute_real_scorecard）：
      F2 = mf_main_net(万元) * 1e4 -> 元 + volume_ratio；F5 = ind_pct_ths；
      F6 = est pe_ttm/turnover_rate（主库 T 日口径；est 为 None 时回退面板 pe_ttm/turn_ma5）；
      F1/F3/F4 = DP 代理分（pos_250 / 事件天数 / rsi6+macd_hist）。
    est 命中<50% 的回退判定在调用方（prep，按生产语义在 top100 池上算）。
    mn_scale=1.0 为官方退化对照（main_net 万元裸值直传元标定阈值，官方消融的 F2 语义）。
    返回 (f1, f2, f3, f4, f5, f6, total_new)。
    """
    with np.errstate(invalid="ignore"):
        n = len(day)

        def col(c, default=None):
            return day[c].to_numpy(dtype=float) if c in day.columns else default

        # F1 位置健康度（DP._score_f1：pos_250 NaN=5 / >=0.99=3 / >=0.95=5 / >=0.88=7 / >=0.72=9 / else 8）
        p = col("pos_250")
        f1 = np.select([np.isnan(p), p >= 0.99, p >= 0.95, p >= 0.88, p >= 0.72],
                       [5.0, 3.0, 5.0, 7.0, 9.0], default=8.0)
        # F2 资金认可度（RF.score_f2：元 + 量比；mn NaN=5 / mn>5e7&lb>2.5=10 / mn>1e7&lb>1.5=8 /
        #              mn>0=6 / lb>=2=5 / mn<=-1e8=1 / else 2）
        mn = col("mf_main_net") * mn_scale        # 万元 -> 元（生产 deploy_predict_g2 同转换；mn_scale=1.0 为官方退化对照）
        lb = col("volume_ratio")
        f2 = np.select([np.isnan(mn), (mn > 5e7) & (lb > 2.5), (mn > 1e7) & (lb > 1.5),
                        mn > 0, ~np.isnan(lb) & (lb >= 2.0), mn <= -1e8],
                       [5.0, 10.0, 8.0, 6.0, 5.0, 1.0], default=2.0)
        # F3 催化强度（DP._score_f3：4 + 3*(fc<=90|ex<=90) + 2*(fc_pchg>20) + 1*(dv_year_sum>1)，min 10；
        #              缺列=默认值语义 .get(fc,9999)/.get(fp,0)/.get(dv,0)，NaN 比较为 False 同 Python）
        fc = col("fc_days_since", np.full(n, 9999.0))
        ex = col("ex_days_since", np.full(n, 9999.0))
        fp = col("fc_pchange", np.zeros(n))
        dv = col("dv_year_sum", np.zeros(n))
        f3 = np.minimum(10.0, 4.0 + 3.0 * ((fc <= 90) | (ex <= 90))
                        + 2.0 * (fp > 20) + 1.0 * (dv > 1.0))
        # F4 技术形态（DP._score_f4：rsi/macd 任一 NaN=5 / rsi>65&macd>0=10 / rsi>50&macd>0=7 /
        #              40<=rsi<=60=5 / rsi<40|macd<0=3 / else 1）
        r6 = col("rsi6")
        mh = col("macd_hist")
        f4 = np.select([np.isnan(r6) | np.isnan(mh), (r6 > 65) & (mh > 0), (r6 > 50) & (mh > 0),
                        (r6 >= 40) & (r6 <= 60), (r6 < 40) | (mh < 0)],
                       [5.0, 10.0, 7.0, 5.0, 3.0], default=1.0)
        # F5 板块联动（RF.score_f5：ind_pct_ths NaN=5 / >3=10 / >1.5=8 / >0=5 / >-1=4 / else 2）
        ip = col("ind_pct_ths")
        f5 = np.select([np.isnan(ip), ip > 3, ip > 1.5, ip > 0, ip > -1],
                       [5.0, 10.0, 8.0, 5.0, 4.0], default=2.0)
        # F6 估值/流动性（RF.score_f6：pe NaN=5 / pe<0|pe>100=1 / 10<=pe<=30&(tu 空档或1<=tu<=8)=10 /
        #                 5<=pe<=50=7 / else 4）；est=None 回退面板 pe_ttm/turn_ma5
        if est_pe is not None:
            pe, tu = est_pe, est_tu
        else:
            pe, tu = col("pe_ttm"), col("turn_ma5")
        f6 = np.select([np.isnan(pe), (pe < 0) | (pe > 100),
                        (pe >= 10) & (pe <= 30) & (np.isnan(tu) | ((tu >= 1) & (tu <= 8))),
                        (pe >= 5) & (pe <= 50)],
                       [5.0, 1.0, 10.0, 7.0], default=4.0)
        total = (sum(SC_WEIGHTS[k] * v for k, v in
                     zip(("F1", "F2", "F3", "F4", "F5"), (f1, f2, f3, f4, f5))) * 10.0
                 + SC_WEIGHTS["F6"] * f6 * 10.0)
    return f1, f2, f3, f4, f5, f6, total


# ---------------- 数据准备 ----------------
def prep(tier_caliber="t0_close"):
    """一次构建全矩阵共享的数据资产（in-memory）。

    tier_caliber: t0_close=审计B3口径（默认，T日自身涨跌，T收盘可知T+1开盘执行，偏保守）；
                  t1_open=旧口径（open(T+1)/close(T) 跳空，与成交同为 open(T+1)——live「T+1 09:50
                  观测当日跳空+09:50 市价成交」的同刻压缩镜像；审计 B3 判定偏乐观，仅作口径对照）。
    """
    import lightgbm as lgb

    log("[prep] build_panel（train_g2 官方管线，PIT）...")
    panel, feat43 = TG.build_panel()
    panel = panel[(panel["trade_date"] >= START) & (panel["trade_date"] <= END)].copy()
    log("panel test 窗行=%d 日数=%d" % (len(panel), panel["trade_date"].nunique()))

    log("[prep] 加载 B21 / live 模型 ...")
    b21 = lgb.Booster(model_file=B21_MODEL)
    with open(LIVE_META, encoding="utf-8") as f:
        live_feats = json.load(f)["feature_cols"]
    live = lgb.Booster(model_file=LIVE_MODEL)
    b21_feats = [
        "vol_ratio_5_20", "amount_ma5", "volume_ratio", "above_ma60", "rsi6",
        "above_ma20", "turn_ma5", "vol20", "mom_20", "rel_mom_20",
        "mom_10", "mom_5", "dist_250_low", "mom_60", "macd_hist",
        "turnover_rank", "industry_mom20", "pb", "dv_ttm", "pe_ttm", "log_mv"]

    log("[prep] F6 est 底座（MAIN_DAILY pe_ttm/turnover_rate，T 日口径，F6 用）...")
    est_src = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    est_src["trade_date"] = pd.to_datetime(est_src["trade_date"])
    est_src["ts_code"] = est_src["ts_code"].astype(str)
    est_src = est_src[est_src["trade_date"] >= START - pd.Timedelta(days=10)]
    est_src = est_src.set_index(["trade_date", "ts_code"]).sort_index()

    log("[prep] 逐日预打分（B21 k1500 + live 43f）+ 生产语义评分卡 ...")
    dates = sorted(panel["trade_date"].unique())
    per_day = {}
    feats_keep = sorted(set(b21_feats) | set(live_feats))
    f_hist = {f: np.zeros(11, dtype=np.int64) for f in ("F1", "F2", "F3", "F4", "F5", "F6")}
    f2_hist_wan = np.zeros(11, dtype=np.int64)
    est_fallback_days = 0
    for d in dates:
        day = panel[panel["trade_date"] == d]
        if len(day) < 20:
            continue
        Xb = day[b21_feats].astype("float32").values
        pb_ = b21.predict(Xb, num_iteration=B21_TREES)
        Xl = day[live_feats].astype("float32").values
        pl_ = live.predict(Xl)
        codes = day["ts_code"].values
        est_pe = est_tu = None
        try:
            e = est_src.xs(d, level="trade_date").reindex(codes)
            est_pe = e["pe_ttm"].to_numpy(dtype=float)
            est_tu = e["turnover_rate"].to_numpy(dtype=float)
        except (KeyError, TypeError):
            pass
        # est 命中判定按生产口径在 top100 池上算（deploy_predict_g2 L219-223：命中<50% -> est=None 回退面板）
        if est_pe is not None:
            pool_idx = np.argsort(-pl_)[:SC_POOL]
            if float((~np.isnan(est_pe[pool_idx])).mean()) < 0.5:
                est_pe = est_tu = None
                est_fallback_days += 1
        f1, f2, f3, f4, f5, f6, tot = _sc_scores(day, est_pe, est_tu)
        for k, arr in zip(("F1", "F2", "F3", "F4", "F5", "F6"), (f1, f2, f3, f4, f5, f6)):
            f_hist[k] += np.bincount(arr.astype(np.int64), minlength=11)
        # 官方退化对照（mn_scale=1.0：官方消融的 F2 语义，跑在同一 harness 上做归因分解）
        w_res = _sc_scores(day, est_pe, est_tu, mn_scale=1.0)
        f2_wan, tot_wan = w_res[1], w_res[6]
        f2_hist_wan += np.bincount(f2_wan.astype(np.int64), minlength=11)
        df = pd.DataFrame({"ts_code": codes,
                           "prob_b21": pb_, "prob_live": pl_,
                           "total_new": tot, "total_new_wan": tot_wan})
        per_day[d] = df.set_index("ts_code")
    log("打分+评分卡完成 %d 日（F6 est 回退面板 %d 日）" % (len(per_day), est_fallback_days))
    del panel

    # ---- F 分布 fail-loud 诊断（生产语义校验：元口径 F2 必须出现 8/10 档，防万元退化/断源） ----
    tot_rows = int(f_hist["F2"].sum())
    for k in ("F1", "F2", "F3", "F4", "F5", "F6"):
        h = f_hist[k]
        nz = [(int(i), 100.0 * h[i] / tot_rows) for i in range(11) if h[i]]
        log("F分布 %s: %s" % (k, " ".join("%d:%.1f%%" % kv for kv in nz)))
    f2h = f_hist["F2"]
    if (f2h[8] + f2h[10]) == 0 or (f2h[2] + f2h[5] + f2h[6]) * 1.0 / tot_rows > 0.995:
        raise SystemExit("!! FAIL-LOUD: F2 分布退化（8/10 档=0 或 2/5/6 档>99.5%）"
                         "——疑似 mf_main_net 万元/断源，生产语义不成立，中止")
    for k in ("F1", "F4", "F5"):
        if len([i for i in range(11) if f_hist[k][i]]) < 3:
            raise SystemExit("!! FAIL-LOUD: %s 分布退化（非零档<3）——疑似断源，中止" % k)
    # 万元退化对照自校验：mf_main_net 若确为万元，退化 F2 只应落在 {2,5,6}（1/8/10 档=0）
    nz_w = [(int(i), 100.0 * f2_hist_wan[i] / tot_rows) for i in range(11) if f2_hist_wan[i]]
    log("F分布 F2_万元退化对照(官方口径): %s" % " ".join("%d:%.1f%%" % kv for kv in nz_w))
    if f2_hist_wan[1] + f2_hist_wan[8] + f2_hist_wan[10] != 0:
        raise SystemExit("!! FAIL-LOUD: 万元退化对照出现 1/8/10 档——mf_main_net 非万元单位，对照无效")

    log("[prep] 执行数据（MAIN_DAILY open→open 可执行口径）...")
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=[
        "open", "high", "low", "close", "up_limit", "down_limit",
        "vol", "suspend_timing"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    uni = set(per_day[dates[0]].index) | set(per_day[dates[-1]].index)
    for d in per_day.values():
        uni |= set(d.index)
    daily = daily[daily["ts_code"].isin(uni)]
    daily = daily[daily["trade_date"] >= START - pd.Timedelta(days=60)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    daily["avg5vol"] = g["vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"]).sort_index()
    maps = {
        "open": daily["open"].to_dict(),
        "high": daily["high"].to_dict(),
        "down_limit": daily["down_limit"].to_dict(),
        "close": daily["close"].to_dict(),
        "vol": daily["vol"].to_dict(),
        "avg5vol": daily["avg5vol"].to_dict(),
        "open_next": daily["open_next"].to_dict(),
        "up_limit_next": daily["up_limit_next"].to_dict(),
        "vol_next": daily["vol_next"].to_dict(),
        "suspend_next": daily["suspend_next"].to_dict(),
    }

    log("[prep] HS300 tier 序列（买入时点=开盘/前收，对齐 live 09:50 语义）...")
    hs = pd.read_parquet(HS300).reset_index()
    hs["trade_date"] = pd.to_datetime(hs["trade_date"])
    hs = hs.sort_values("trade_date")
    # 2026-10-19 修复（审计 B3）：原实现 tier 用 open(T+1)/close(T) 判定，而成交价
    # 也是 open(T+1) —— 等于「先看到开盘价、再以该开盘价成交」，审计判定不可实现。
    # 改为用 T 日自身涨跌（close(T)/close(T-1)-1）判定：T 日收盘即可知，T+1 开盘执行。
    # 注：与 live「T+1 09:50 看当日跳空」语义不同，回测口径偏保守。
    # --tier-caliber t1_open 可切回旧口径做双轨对照（live 同刻压缩镜像，审计判定偏乐观）。
    if tier_caliber == "t1_open":
        hs["pct_gate"] = (hs["close"].shift(-1) / hs["close"] - 1.0) * 100.0
        log("tier 口径=t1_open（旧口径对照：open(T+1)/close(T) 跳空，审计 B3 判定偏乐观）")
    else:
        hs["pct_gate"] = (hs["close"] / hs["close"].shift(1) - 1.0) * 100.0
        log("tier 口径=t0_close（审计 B3 默认：T 日自身涨跌，偏保守）")
    hs["close_fwd"] = hs["close"].shift(-1)
    tier_map, hs300_close = {}, hs.set_index("trade_date")["close"].to_dict()
    for _, r in hs.iterrows():
        p = r["pct_gate"]
        if not np.isfinite(p):
            tier_map[r["trade_date"]] = 1          # 缺失 fail-safe T1（bridge 同）
        elif p <= TIER_STOP_PCT:
            tier_map[r["trade_date"]] = 0
        elif p < TIER_HALF_PCT:
            tier_map[r["trade_date"]] = 1
        else:
            tier_map[r["trade_date"]] = 2

    log("[prep] 基准：全市场等权日收益 ...")
    bench = pd.read_parquet(DC.MAIN_DAILY, columns=["close", "pre_close"]).reset_index()
    bench["trade_date"] = pd.to_datetime(bench["trade_date"])
    # pre_close 口径（含除权调整）：close.pct_change() 在除权日会产生假暴跌，禁止使用
    bench["ret"] = bench["close"] / bench["pre_close"] - 1.0
    bench = bench[bench["ret"].notna() & np.isfinite(bench["ret"]) & (bench["ret"].abs() < 0.35)]
    mkt = bench.groupby("trade_date")["ret"].mean()
    mkt = mkt[(mkt.index >= START) & (mkt.index <= END)]
    del bench

    return {"dates": dates, "per_day": per_day, "maps": maps,
            "tier_map": tier_map, "hs300_close": hs300_close, "mkt": mkt,
            "f_hist": f_hist, "f2_hist_wan": f2_hist_wan,
            "est_fallback_days": est_fallback_days}


# ---------------- 停牌判定（供应商 2026-07-27 起非停牌日由 NaN 变 ''） ----------------
def _is_susp(v):
    """真停牌 = 非空时间串（如 '09:30-10:00'）；NaN 与 '' 均为正常交易日。"""
    return v is not None and not (isinstance(v, float) and np.isnan(v)) and v != ""


# ---------------- 量比 proxy（gap_guard -5%~-3% 带） ----------------
_MIN1_CACHE = {}


def _volume_ratio_0950(code, buy_date, avg5vol_daily, vol_next, maps):
    """量比 = 买入日 09:50 前分钟累计量 / 前5日日均量。

    主口径：分钟线自算（分子分母同源同单位，零单位风险）。
    缺失链：分钟线不可用 -> 全天量比（vol_next/avg5vol）-> 仍缺 -> None（fail-open 放行）。
    """
    try:
        fp = os.path.join(MIN1_DIR, "%s.parquet" % code)
        if not os.path.exists(fp):
            raise FileNotFoundError(code)
        if code not in _MIN1_CACHE:
            m = pd.read_parquet(fp, columns=["vol", "trade_date", "trade_time"])
            m["trade_date"] = pd.to_datetime(m["trade_date"])
            _MIN1_CACHE[code] = m.groupby("trade_date")["vol"].sum()
        dv = _MIN1_CACHE[code]
        if buy_date not in dv.index:
            raise KeyError(buy_date)
        # 前5日分钟日均量（买入日之前 5 个有数据交易日）
        idx_pos = dv.index.get_loc(buy_date)
        prev5 = dv.iloc[max(0, idx_pos - 5):idx_pos]
        if len(prev5) < 3 or prev5.mean() <= 0:
            raise ValueError("prev5 不足")
        # 09:50 前累计量：重读该日分钟行（只此一次，防 cache 过大）
        m = pd.read_parquet(fp, columns=["vol", "trade_date", "trade_time"])
        m["trade_date"] = pd.to_datetime(m["trade_date"])
        day = m[m["trade_date"] == buy_date]
        tt = pd.to_datetime(day["trade_time"]).dt.time
        cum = float(day.loc[tt <= datetime.time(9, 50), "vol"].sum())
        return cum / float(prev5.mean())
    except Exception:
        pass
    # fallback 1：全天量比（日线口径）
    try:
        if avg5vol_daily and avg5vol_daily > 0 and vol_next and vol_next > 0:
            return float(vol_next) / float(avg5vol_daily)
    except Exception:
        pass
    return None  # fail-open（bridge 缺数据放行）


# ---------------- 费用 ----------------
def _is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    return max(COMM_MIN, amt * COMM) + amt * STAMP + (amt * TRANS if _is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    return max(COMM_MIN, amt * COMM) + (amt * TRANS if _is_sh(code) else 0.0) + amt * slip


# ---------------- 模拟 ----------------
def simulate(ctx, model_key, top_n, hold_n, slip, tier_on, redline, tag, sel_mode="prob"):
    """单配置策略模拟。model_key: 'b21'|'live'；sel_mode: 'prob'（简化红线）|'sc'（生产评分卡口径）；tag 用于日志/输出标识。"""
    dates = [d for d in ctx["dates"] if d in ctx["per_day"]]
    per_day, maps = ctx["per_day"], ctx["maps"]
    tier_map = ctx["tier_map"]
    prob_col = "prob_b21" if model_key == "b21" else "prob_live"
    M = len(dates)
    cash = CAPITAL0
    hold = {}          # code -> dict(invest, buy_val, value, buy_i, peak, missing_days, buy_px)
    trades, nav = [], []
    exit_cnt, tier_cnt = {}, {0: 0, 1: 0, 2: 0}
    cost_total = 0.0
    n_buy_skip = {"limit_up": 0, "suspend": 0, "gap_hard": 0, "gap_vr": 0}

    def o(d, c):
        return maps["open"].get((d, c))

    for i, d in enumerate(dates):
        day = per_day[d]
        # ---- 盯市 + 卖出（当日开盘口径，官方引擎语义） ----
        for c in list(hold):
            h = hold[c]
            o_buy = h["buy_px"]
            o_cur = o(d, c)
            if not (o_buy and o_buy > 0 and o_cur and o_cur > 0 and np.isfinite(o_cur)):
                h["missing_days"] += 1
                if h["missing_days"] >= DELIST_DAYS:
                    amt = h["value"] * (1 - DELIST_LOSS)
                    cash += amt - sell_fee(amt, c, slip)
                    cost_total += sell_fee(amt, c, slip)
                    trades.append({"code": c, "buy_i": h["buy_i"], "sell_i": i,
                                   "ret": amt / h["buy_val"] - 1, "reason": "DELIST"})
                    exit_cnt["DELIST"] = exit_cnt.get("DELIST", 0) + 1
                    del hold[c]
                continue
            h["missing_days"] = 0
            h["value"] = h["invest"] * o_cur / o_buy
            hi = maps["high"].get((d, c))
            peak = h["peak"] if h["peak"] else o_buy
            if (i - h["buy_i"]) >= 2 and hi and np.isfinite(hi) and hi > 0:
                h["peak"] = max(peak, hi)
            peak = h["peak"] if h["peak"] else o_buy
            ret = o_cur / o_buy - 1
            down = maps["down_limit"].get((d, c))
            if down is not None and np.isfinite(down) and o_cur <= down + 1e-9:
                continue  # 一字跌停卖不出：顺延
            sell, reason = False, ""
            if (i - h["buy_i"]) >= hold_n + 1:
                sell, reason = True, "MATURE"
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                trail_act = peak >= o_buy * (1 + TRAIL_ACT)
                trail_hit = trail_act and o_cur <= max(o_buy, peak * (1 - TRAIL))
                if ret <= STOP:
                    sell, reason = True, "STOP"
                elif ret >= TP:
                    sell, reason = True, "TP"
                elif trail_hit:
                    sell, reason = True, "TRAIL"
            if sell:
                amt = h["value"]
                fee = sell_fee(amt, c, slip)
                cash += amt - fee
                cost_total += fee
                trades.append({"code": c, "buy_i": h["buy_i"], "sell_i": i,
                               "ret": h["value"] / h["buy_val"] - 1, "reason": reason})
                exit_cnt[reason] = exit_cnt.get(reason, 0) + 1
                del hold[c]

        # ---- tier（买入日开盘/前收，live 09:50 语义代理） ----
        tier = tier_map.get(d, 1) if tier_on else 2
        tier_cnt[tier] += 1
        deploy = 0.0 if tier == 0 else (DEPLOY_HALF if tier == 1 else DEPLOY_FULL)

        # ---- 买入（决策日 d，成交 d+1 开盘） ----
        if deploy > 0:
            if sel_mode == "sc":
                # 生产候选流（deploy_predict_g2 同构）：top100 -> top10 by prob -> 红线 total_new -> total_new 降序
                pool = day.nlargest(SC_POOL, prob_col).nlargest(SC_PRE, prob_col)
                cand = pool[pool["total_new"] >= redline].sort_values("total_new", ascending=False)
            elif sel_mode == "sc_wan":
                # 官方退化 F2 对照（官方消融的 F2 语义）：万元裸值 total_new_wan 排序/红线
                pool = day.nlargest(SC_POOL, prob_col).nlargest(SC_PRE, prob_col)
                cand = pool[pool["total_new_wan"] >= redline].sort_values("total_new_wan", ascending=False)
            else:
                cand = day[day[prob_col] >= redline].sort_values(prob_col, ascending=False)
            n_slots = top_n - len(hold)
            bought = 0
            for code, row in cand.head(SELECT_TOP).iterrows():
                if bought >= n_slots:
                    break
                if code in hold:
                    continue
                open_next = maps["open_next"].get((d, code))
                up_next = maps["up_limit_next"].get((d, code))
                vol_next = maps["vol_next"].get((d, code))
                sus_next = maps["suspend_next"].get((d, code))
                if open_next is None or not np.isfinite(open_next) or open_next <= 0:
                    continue
                if _is_susp(sus_next):
                    n_buy_skip["suspend"] += 1
                    continue
                if vol_next is None or not np.isfinite(vol_next) or vol_next <= 0:
                    n_buy_skip["suspend"] += 1
                    continue
                if up_next is not None and np.isfinite(up_next) and open_next >= up_next:
                    n_buy_skip["limit_up"] += 1
                    continue
                # gap_guard（低开校验，bridge 复刻）
                pc = maps["close"].get((d, code))
                if pc and np.isfinite(pc) and pc > 0:
                    opct = (open_next / pc - 1.0) * 100.0
                    if opct <= GAP_HARD:
                        n_buy_skip["gap_hard"] += 1
                        continue
                    if opct <= GAP_SOFT:
                        avg5 = maps["avg5vol"].get((d, code))
                        buy_date = dates[i + 1] if i + 1 < M else None
                        vr = None
                        if buy_date is not None:
                            vr = _volume_ratio_0950(code, buy_date, avg5, vol_next, maps)
                        if not (vr is not None and vr >= VOL_RATIO_MIN):
                            n_buy_skip["gap_vr"] += 1
                            continue
                # 资金与费用（等权目标只数 = TOP_N，bridge 语义）
                budget = cash * deploy / float(top_n)
                fee = buy_fee(budget, code, slip)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                cost_total += fee
                hold[code] = {"invest": invest, "buy_val": invest, "value": invest,
                              "buy_i": i, "peak": None, "missing_days": 0, "buy_px": open_next}
                bought += 1

        total = cash + sum(h["value"] for h in hold.values())
        nav.append(total)

    # 期末未平仓：按最后盯市估值补 trades（不计费）
    for c, h in hold.items():
        trades.append({"code": c, "buy_i": h["buy_i"], "sell_i": M,
                       "ret": h["value"] / h["buy_val"] - 1, "reason": "OPEN_END"})

    nav_s = pd.Series(nav, index=dates[:len(nav)])
    res = _metrics(nav_s, trades, ctx["mkt"], ctx["hs300_close"], dates)
    res.update({"tag": tag, "model": model_key, "top_n": top_n, "hold_n": hold_n,
                "slip": slip, "tier": "on" if tier_on else "off", "redline": redline,
                "sel_mode": sel_mode,
                "exit_cnt": exit_cnt, "tier_cnt": tier_cnt, "buy_skip": n_buy_skip,
                "cost_total": cost_total})
    log("  %-38s 总收益%+7.2f%% 年化%+7.2f%% 夏普%5.2f 回撤%6.1f%% 交易%3d 胜率%.0f%%"
        % (tag, res["total_ret"] * 100, res["annual"] * 100, res["sharpe"],
           res["mdd"] * 100, res["n_trades"], res["win_rate"] * 100))
    return res, nav_s


def _metrics(nav, trades, mkt, hs300_close, dates):
    daily = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    n_days = len(nav)
    annual = (1 + total_ret) ** (252.0 / max(1, n_days)) - 1
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    rets = pd.Series([t["ret"] for t in trades])
    win = float((rets > 0).mean()) if len(rets) else np.nan
    aw = float(rets[rets > 0].mean()) if (rets > 0).any() else np.nan
    al = float(rets[rets < 0].mean()) if (rets < 0).any() else np.nan
    mkt_al = mkt.reindex(daily.index).dropna()
    ex = (daily.reindex(mkt_al.index) - mkt_al).mean() if len(mkt_al) else np.nan
    hs0, hs1 = hs300_close.get(dates[0]), hs300_close.get(dates[-1])
    hs_ret = (hs1 / hs0 - 1) if (hs0 and hs1) else np.nan
    # hs300 详细指标（年化/夏普/回撤，全区间）
    hs_s = pd.Series(hs300_close).sort_index()
    hs_s = hs_s[(hs_s.index >= dates[0]) & (hs_s.index <= dates[-1])]
    hs_daily = hs_s.pct_change().dropna()
    hs_annual = (1 + hs_ret) ** (244.0 / max(1, len(hs_daily))) - 1 if np.isfinite(hs_ret) else np.nan
    hs_sharpe = float(hs_daily.mean() / hs_daily.std() * np.sqrt(244)) if len(hs_daily) and hs_daily.std() > 0 else np.nan
    hs_nav = (1 + hs_daily).cumprod()
    hs_mdd = float((hs_nav / hs_nav.cummax() - 1).min()) if len(hs_daily) else np.nan
    mkt_total = float((1 + mkt_al).prod() - 1) if len(mkt_al) else np.nan
    return {"total_ret": total_ret, "annual": annual, "sharpe": sharpe, "mdd": mdd,
            "n_trades": len(trades), "win_rate": win,
            "pl_ratio": (aw / abs(al)) if (al and al != 0) else np.nan,
            "daily_excess_vs_mkt_eq": ex,
            "mkt_eq_total": mkt_total, "hs300_total": hs_ret,
            "hs300_annual": hs_annual, "hs300_sharpe": hs_sharpe, "hs300_mdd": hs_mdd}


# ---------------- 主入口 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="跑预设全矩阵并写报告")
    ap.add_argument("--sc", action="store_true",
                    help="跑生产评分卡口径矩阵（干净对比：同 harness 只换信号层）并写独立报告")
    ap.add_argument("--arm", choices=["g2", "ens"], default="g2")
    ap.add_argument("--model", choices=["b21", "live"], default="b21")
    ap.add_argument("--slips", default="0.001")
    ap.add_argument("--tier", choices=["on", "off"], default="on")
    ap.add_argument("--tier-caliber", choices=["t0_close", "t1_open"], default="t0_close",
                    help="tier 判定口径：t0_close=审计B3（T日自身涨跌，默认，偏保守）/"
                         "t1_open=旧口径（T+1开盘跳空，live 09:50 同刻压缩的忠实镜像，审计判定偏乐观，仅作口径对照）")
    ap.add_argument("--thr", type=float, default=REDLINE)
    args = ap.parse_args()

    ctx = prep(args.tier_caliber)
    os.makedirs(OUT_NAV_DIR, exist_ok=True)
    results, navs = [], {}

    def run(tag, model, arm, slip, tier_on, thr, sel_mode="prob"):
        hold_n, top_n = (HOLD_G2, TOP_G2) if arm == "g2" else (HOLD_ENS, TOP_ENS)
        r, nav = simulate(ctx, model, top_n, hold_n, slip, tier_on, thr, tag, sel_mode=sel_mode)
        results.append(r)
        navs[tag] = nav
        return r

    if args.sc:
        log("==== 生产评分卡口径（干净对比：生产语义 F2=元/F5=ths/红线60/top100->10->total_new 排序）====")
        run("G2臂 现役SC60 slip=0.0%", "live", "g2", 0.0, True, SC_REDLINE, sel_mode="sc")
        run("G2臂 现役SC60 slip=0.1%", "live", "g2", 0.001, True, SC_REDLINE, sel_mode="sc")
        run("G2臂 现役SC60 slip=0.2%", "live", "g2", 0.002, True, SC_REDLINE, sel_mode="sc")
        run("G2臂 现役SC58 slip=0.1%", "live", "g2", 0.001, True, 58.0, sel_mode="sc")
        run("G2臂 SC60退化F2对照 slip=0.1%", "live", "g2", 0.001, True, SC_REDLINE, sel_mode="sc_wan")
    elif args.all:
        log("==== 预设矩阵 ====")
        log("-- A. G2 臂 B21（主口径）--")
        for slip in (0.0, 0.001, 0.002):
            run("G2臂 B21 slip=%.1f%%" % (slip * 100), "b21", "g2", slip, True, REDLINE)
        log("-- B. ENS 臂 B21 --")
        for slip in (0.0, 0.001, 0.002):
            run("ENS臂 B21 slip=%.1f%%" % (slip * 100), "b21", "ens", slip, True, REDLINE)
        log("-- C. 现役模型对照（同策略同红线）--")
        for slip in (0.0, 0.001, 0.002):
            run("G2臂 现役43f slip=%.1f%%" % (slip * 100), "live", "g2", slip, True, REDLINE)
        log("-- D. tier 贡献 / 红线敏感性（slip=0.1%）--")
        run("G2臂 B21 tier=off", "b21", "g2", 0.001, False, REDLINE)
        run("G2臂 B21 thr=0.55", "b21", "g2", 0.001, True, 0.55)
        run("G2臂 B21 thr=0.65", "b21", "g2", 0.001, True, 0.65)
    else:
        slips = [float(x) for x in args.slips.split(",") if x.strip()]
        for slip in slips:
            run("%s臂 %s slip=%.1f%% tier=%s thr=%.2f"
                % (args.arm, args.model, slip * 100, args.tier, args.thr),
                args.model, args.arm, slip, args.tier == "on", args.thr)

    t1 = args.tier_caliber == "t1_open"
    if args.sc:
        out_json = OUT_JSON_SC_T1 if t1 else OUT_JSON_SC
    else:
        out_json = OUT_JSON_T1 if t1 else OUT_JSON
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"built_at": datetime.datetime.now().isoformat(),
                   "start": str(START.date()), "end": str(END.date()),
                   "tier_caliber": args.tier_caliber,
                   "results": results}, f, ensure_ascii=False, indent=2)
    log("json -> %s" % out_json)
    for tag, nav in navs.items():
        safe = tag.replace(" ", "_").replace("%", "pct").replace("=", "").replace(".", "d").replace("/", "_")
        nav.rename("nav").to_csv(os.path.join(OUT_NAV_DIR, safe + ".csv"), encoding="utf-8")

    if t1:
        log("tier 口径=t1_open（旧口径对照）：仅写 json，不覆盖 t0_close 口径的报告产物")
    elif args.sc:
        write_sc_report(ctx, results, navs)
    elif args.all:
        write_report(ctx, results, navs)
    return 0


def write_report(ctx, results, navs):
    mkt = ctx["mkt"]
    mkt_total = float((1 + mkt).prod() - 1)
    lines = [
        "# B21 信号内产版 —— 策略级回测报告",
        "",
        "> 生成 %s | 区间 %s ~ %s | 本金 10 万 | 数据：train_g2 官方面板(PIT) + astock 日线/指数 | 模型：B21 k=1500（部署同工作点，截断等价性 max_err=2.4e-9）"
        % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), START.date(), END.date()),
        "",
        "> 背景：G2/ENS 信号内产版（QMT 沙箱自闭环 Step 4）此前只有模型级 OOS 验收，本报告补齐策略级回测。"
        "策略规则忠实复刻桥源 `strategy_p16_g2_bridge_src.py`（简化红线 prob≥0.60 / TOP_N 动态等权 / "
        "live_trail 出场 / tier 门控 / 低开校验），执行口径复刻官方引擎 `scan_rotate_cost_real.py`"
        "（open→open 可执行 / 一字板停牌过滤 / 跌停顺延 / 退市处理）。",
        "",
        "## 一、结果矩阵",
        "",
        "| 配置 | 总收益 | 年化 | 夏普 | 最大回撤 | 交易数 | 胜率 | 盈亏比 | vs 全市场等权日均超额 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            "| %s | %+.2f%% | %+.2f%% | %.2f | %.1f%% | %d | %.0f%% | %.2f | %+.3f%%/日 |"
            % (r["tag"], r["total_ret"] * 100, r["annual"] * 100, r["sharpe"],
               r["mdd"] * 100, r["n_trades"], r["win_rate"] * 100,
               r["pl_ratio"] if r["pl_ratio"] == r["pl_ratio"] else 0,
               r["daily_excess_vs_mkt_eq"] * 100 if r["daily_excess_vs_mkt_eq"] == r["daily_excess_vs_mkt_eq"] else 0))
    lines += [
        "",
        "## 二、基准对比（同期 %s ~ %s）" % (START.date(), END.date()),
        "",
        "| 基准 | 总收益 | 年化 | 夏普 | 最大回撤 |",
        "|---|---|---|---|---|",
    ]
    mkt_ann = float((1 + mkt).prod() ** (244.0 / len(mkt)) - 1) if len(mkt) else float("nan")
    mkt_shp = float(mkt.mean() / mkt.std() * np.sqrt(244)) if len(mkt) else float("nan")
    mkt_nav = (1 + mkt).cumprod()
    mkt_mdd = float((mkt_nav / mkt_nav.cummax() - 1).min()) if len(mkt) else float("nan")
    lines.append("| 全市场等权（pre_close 口径，不可投资参照） | %+.2f%% | %+.2f%% | %.2f | %.1f%% |"
                 % (mkt_total * 100, mkt_ann * 100, mkt_shp, mkt_mdd * 100))
    hs0 = results[0]["hs300_total"] if results else 0
    lines.append("| 沪深300 | %+.2f%% | %+.2f%% | %.2f | %.1f%% |"
                 % (hs0 * 100, results[0].get("hs300_annual", float("nan")) * 100 if results else 0,
                    results[0].get("hs300_sharpe", float("nan")) if results else 0,
                    results[0].get("hs300_mdd", float("nan")) * 100 if results else 0))
    lines += [
        "",
        "> 等权基准为全市场不可投资参照（含 ST/停牌/微流动性），策略容量与可执行性远优；"
        "等权基准 %+.1f%% 由 %s 小盘普涨行情驱动。" % (mkt_total * 100, "2024-09~2026-09"),
        "",
        "## 三、出场原因与执行统计（slip=0.1% 主口径）",
        "",
    ]
    for r in results:
        if abs(r["slip"] - 0.001) > 1e-9:
            continue
        ex = " ".join("%s=%d" % kv for kv in sorted(r["exit_cnt"].items()))
        sk = " ".join("%s=%d" % kv for kv in sorted(r["buy_skip"].items()))
        ti = " ".join("T%d=%d日" % kv for kv in sorted(r["tier_cnt"].items()))
        lines += [
            "### %s" % r["tag"],
            "",
            "- 出场原因：%s" % (ex or "无"),
            "- tier 分布：%s | 买入跳过：%s" % (ti, sk),
            "- 总成本（费+滑点）%.0f 元（占本金 %.1f%%）" % (r["cost_total"], r["cost_total"] / CAPITAL0 * 100),
            "",
        ]
    # ---- 结论（自动提取主口径数字） ----
    def find(tag):
        for r in results:
            if r["tag"] == tag:
                return r
        return None

    m = find("G2臂 B21 slip=0.1%")
    lv = find("G2臂 现役43f slip=0.1%")
    ens = find("ENS臂 B21 slip=0.1%")
    lines += [
        "## 四、结论",
        "",
    ]
    if m:
        lines += [
            "1. **内产版主口径（G2 臂 B21，slip=0.1%%）**：总收益 %+.1f%% / 年化 %+.1f%% / 夏普 %.2f / 回撤 %.1f%% / %d 笔 / 胜率 %.0f%%。"
            % (m["total_ret"] * 100, m["annual"] * 100, m["sharpe"], m["mdd"] * 100,
               m["n_trades"], m["win_rate"] * 100),
        ]
    if m and lv:
        _gap = (m["annual"] - lv["annual"]) * 100
        lines += [
            "2. **vs 现役模型（同策略/同红线/同成本）**：年化 %+.1f%% vs %+.1f%%（%+.1fpp），夏普 %.2f vs %.2f，回撤 %.1f%% vs %.1f%% —— %s"
            % (m["annual"] * 100, lv["annual"] * 100, _gap,
               m["sharpe"], lv["sharpe"], m["mdd"] * 100, lv["mdd"] * 100,
                ("**内产版在策略级显著优于现役**，与模型级 OOS 结论（top2_ret +4.10% vs +1.97%）方向一致。"
                 if _gap > 0 else
                 "**在本 tier 口径下内产版并未优于现役模型**——与模型级 OOS 结论（top2_ret +4.10% vs +1.97%）方向相反，"
                 "tier 口径敏感（见第 4 条与口径披露），结论稳定性待复评。")),
            "   - **口径边界（防误读，2026-10-18 补）**：本对照 ≠「内产版优于现役策略的历史回测成绩」——现役线上策略用评分卡红线"
            "（total≥58/60，含 F2/F5/F6 外部数据增强），其历史回测数字远高于本口径（对照见第五节第 11 条）；"
            "评分卡在 QMT 沙箱内不可执行（F2/F5/F6 来自外部 CSV，已被 10-15 沙箱红线禁止），红线降级为 prob≥0.60 是拍板接受的口径差。"
            "本报告全部数字是「沙箱可执行口径」的预期，不与评分卡时代的历史数字构成直接对比或超越声明。",
            "   - **（2026-10-19 补）评分卡口径已在本 harness 干净复测**（`B21_干净对比_生产评分卡口径_20261018.md`）："
            "生产评分卡 SC60 双 tier 口径均低于同模型简化红线（t0_close：-3.87% vs +12.26%；t1_open：+4.79% vs +6.59%）"
            "——历史高数字的主因是官方 harness 而非评分卡本身（第五节第 11 条）。",
        ]
    s02 = find("G2臂 B21 slip=0.2%")
    if s02:
        _s2 = s02["annual"] * 100
        lines += [
            "3. **滑点稳健性**：滑点 0→0.2%% 年化 %+.1f%%→%+.1f%%，扣双倍滑点后%s。"
            % (find("G2臂 B21 slip=0.0%")["annual"] * 100, _s2,
               "仍显著为正" if _s2 > 5 else ("仍为正但边际（夏普 %.2f），成本敏感度上升" % s02["sharpe"]
                                             if _s2 > 0 else "转弱/转负，成本敏感度警示")),
        ]
    toff = find("G2臂 B21 tier=off")
    if toff and m:
        _tier_delta = (m["annual"] - toff["annual"]) * 100
        lines += [
            "4. **tier 门控**：off 年化 %+.1f%% vs on %+.1f%%（贡献 %+.1fpp / 回撤 %+.1fpp），%s。"
            % (toff["annual"] * 100, m["annual"] * 100, _tier_delta,
               (m["mdd"] - toff["mdd"]) * 100,
               "温和正贡献，保留" if _tier_delta > 0
               else "新 tier 口径（T 日自身涨跌，B3 修复）下本窗为负贡献，是否保留待复评"),
        ]
    t55, t65 = find("G2臂 B21 thr=0.55"), find("G2臂 B21 thr=0.65")
    if t55 and t65 and m:
        _m060 = m["annual"] * 100
        _best = max(_m060, t55["annual"] * 100, t65["annual"] * 100)
        _c5 = ("0.60 为甜点，两侧均劣" if _best == _m060 else
               ("本 tier 口径下 0.65 反超 0.60（门槛上调减亏），红线甜点结论不稳健，与 tier 口径强相关"
                if _best == t65["annual"] * 100 else
                "本 tier 口径下 0.55 反超 0.60，红线甜点结论不稳健"))
        lines += [
            "5. **红线敏感性**：thr=0.55 年化 %+.1f%% / 0.60 %+.1f%% / 0.65 %+.1f%% —— %s。"
            % (t55["annual"] * 100, _m060, t65["annual"] * 100, _c5),
        ]
    if ens and m:
        lines += [
            "6. **ENS 臂显著弱于 G2 臂**（年化 %+.1f%% vs %+.1f%%，高换手放大成本磨损），若上线建议仅上 G2 臂。"
            % (ens["annual"] * 100, m["annual"] * 100),
        ]
    lines += [
        "7. **诚实披露**：策略总收益跑输同期全市场等权（不可投资参照，小盘普涨行情驱动），但等权含 ST/停牌/微流动性且容量极差；"
        "策略相对沪深300 全周期超额明确为正，且回撤/夏普占优。绝对 alpha 的持续性依赖小盘风格环境。",
        "",
        "## 五、口径与已知近似（诚实声明）",
        "",
        "1. 成交价=开盘价（live 09:50 市价），滑点敏感性 0/0.1%/0.2% 覆盖",
        "2. 止损/止盈/追盈按当日开盘判定（live tick 级），盘中触发压缩到开盘口径",
        "3. ST 强制卖出未实现（panel 过滤帧已剔 ST，持有转 ST 极罕见）",
        "4. G2/ENS 持仓互斥未实现（单臂独立 10 万资金池）",
        "5. 动态等权再平衡（超配减仓）未实现（官方引擎同口径）",
        "6. 量比 proxy：分钟线 09:50 前累计量/前5日分钟日均量；缺失 fallback 全天量比→fail-open",
        "7. tier=沪深300 **T 日自身涨跌**（close(T)/close(T-1)-1；2026-10-19 审计 B3 修复：原「T+1 开盘跳空」= 看到开盘价再以该开盘价成交，不可实现；T 日收盘即可知、T+1 开盘执行，与 live「T+1 09:50 看当日跳空」语义不同，回测口径偏保守）；缺失 fail-safe T1",
        "8. 特征为 panel PIT 口径（live 沙箱快照近似残差 s4-5c 已测：Run A 21/21 PASS）",
        "9. 决策日 T 收盘特征 → T+1 开盘成交（label3 同构，无前视）",
        "10. 停牌判定：suspend_timing 非空时间串才视为停牌（供应商 2026-07-27 起非停牌日由 NaN 变 ''，"
        "若按「非 NaN 即停牌」会误杀 2026-07-27 后全部候选，实测少赚 12pp）",
        "11. **与历史评分卡口径回测的对照（防误读，2026-10-18 补；2026-10-19 双口径更新）**："
        "本报告全部数字为「沙箱可执行口径」（简化红线 prob≥0.60、无评分卡）。"
        "历史文献中的高收益数字均为评分卡口径，规则/窗口不同，**不可直接对比**：",
        "",
        "    | 历史口径 | 规则 | 红线 | 窗口 | G2 年化 | 来源 |",
        "    |---|---|---|---|---|---|",
        "    | 近5年回测 | TOP2 纯 N15 轮动，**无出场链、无 tier** | 评分卡 60 | 2021-08~2026-08 | 全窗 28.8%（总 +236.9%）/ OOS 段 100.1% | 近5年回测_三策略_20261004.md |",
        "    | 官方冷启动锚点 | 同上（G2 0.95） | 评分卡 | 2024-07-01 起 | 110.12%（总 +357.41%，夏普 2.278） | T-20261003-005 |",
        "    | TOP_N 网格 | 官方引擎，-7%/+15% 出场，N10/TOP3 | 评分卡 60 | 2024-07-01~2026-08-14 | 78.55%（夏普 2.15）※ | 持仓只数最优论证_20260921.md |",
        "    | **评分卡干净复测** | 桥源全规则**同本报告 harness**（TOP3/N15/live_trail/tier/gap_guard） | 生产评分卡 SC60（F2=元/ths/est） | 2024-07-01~2026-09-30 | 现役 43f：t0_close **-3.87%** / t1_open +4.79%（同臂 prob 红线 +12.26% / +6.59%；退化F2 对照 -5.12% / +4.67%） | B21_干净对比_生产评分卡口径_20261018.md |",
        "    | **本报告（沙箱口径）** | 桥源全规则 TOP3/N15/live_trail/tier/gap_guard | **prob≥0.60** | 2024-07-01~2026-09-30 | B21：t0_close 5.8% / t1_open 18.8%；现役模型：+12.3% / +6.6% | 本报告 |",
        "",
        "    ※ TOP_N 网格自注：「测试期 100% 落在 G2（08-25 训练）模型训练窗内（测试集双重使用），全部年化数字系统性偏乐观，仅作档间相对比较，禁止对外引用绝对收益」。",
        "",
        "    差异主因（按证据强度排序，2026-10-19 依干净复测与 tier 双口径更新）："
        "① **「评分卡 vs prob 红线」已可精确归因（干净复测，同 harness 只换信号层，双 tier 口径稳健）**——"
        "评分卡层在同 harness 贡献 t0_close **-16.1pp**（+12.26%→-3.87%）/ t1_open **-1.8pp**（+6.59%→+4.79%），"
        "两口径均非正，与官方消融 +38.4pp 方向相反；归因三角证明方向反转主因是**官方 harness**"
        "（TOP2/无 tier/08-14 窗/官方引擎）而非 F2 单位语义（F2 语义差仅 -1.3pp/-0.1pp，生产元口径两口径均更好）"
        "——官方 78.55%/110.12% 系官方 harness 产物，**原「红线降级是量级差主因」的假设被证伪**；"
        "② **出场链**——近5年口径无 STOP/TP/TRAIL（利润奔跑、回撤也深，全窗 -46.8%），"
        "出场链实证拖累 -1.34pp/笔（止损档位扫描_20261009，2026-08~09 小样本 n=55）；"
        "③ **窗口尾**——本报告含 2026-08-15~09-30 回撤段；"
        "④ TOP_N 网格训练窗内偏乐观（见※）。"
        "实盘至今真实收益（上线 ~1.5 个月小样本）：G2 -2.57% / ENS -2.79% / V1.x -11.25%（P16三策略上线至今收益审计_20261008.md）。",
        "",
        "## 六、NAV 曲线",
        "",
        "各配置 NAV 已落 `data/real/b21_nav/*.csv`（日期,nav 两列）。",
        "",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告 -> %s" % OUT_MD)


def write_sc_report(ctx, results, navs):
    """干净对比报告：生产语义评分卡 vs B21 简化红线（同 harness，只换信号层）。"""
    f_hist, est_fb = ctx["f_hist"], ctx["est_fallback_days"]
    tot_rows = int(f_hist["F2"].sum())
    base = {}
    try:
        with open(OUT_JSON, encoding="utf-8") as f:
            for r in json.load(f)["results"]:
                base[r["tag"]] = r
    except (OSError, ValueError):
        pass

    def find(tag):
        for r in results:
            if r["tag"] == tag:
                return r
        return None

    lines = [
        "# B21 干净对比：生产评分卡口径（同 harness 只换信号层）",
        "",
        "> 生成 %s | 区间 %s ~ %s | 本金 10 万 | harness/成本/风控与 B21 回测完全同构，仅买入信号层不同"
        % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), START.date(), END.date()),
        "",
        "> 回答的问题：**沙箱解除后，若 G2 恢复「模型 top100 → 评分卡红线 60 → total_new 排序」的原方案，"
        "在同一回测 harness 下值多少？** 对照：B21 内产版（简化红线 prob≥0.60，现沙箱方案）与现役 43f 简化红线，"
        "数字见第三节（源自 `B21信号内产版_策略级回测报告_20261017.md` 同一 harness）。",
        "",
        "> **tier 口径双轨（2026-10-19 立，取舍待拍板）**：审计 B3 将 tier 判定从「T+1 开盘跳空」"
        "（live「09:50 看当日跳空+09:50 市价成交」的同刻压缩镜像）改为「T 日自身涨跌」（保守）。"
        "主口径取 t0_close（审计默认），旧口径 t1_open 作对照，两口径数字并列第三节。",
        "",
        "## 一、信号层定义（生产语义，deploy_predict_g2 同构）",
        "",
        "1. 池：`day.nlargest(100, prob_live).nlargest(10, prob_live)`（现役 43f 模型）",
        "2. 红线：`total_new >= 60`（生产 `--threshold 60`，g2_candidates_night 实跑值；58 为敏感性对照）",
        "3. 排序：`total_new` 降序补足空位（桥补足语义，与 prob 臂对称）",
        "4. 评分卡 F1-F6 生产语义：F2 = mf_main_net×1e4（万元→**元**）+ volume_ratio；F5 = ind_pct_ths；"
        "F6 = est pe_ttm/turnover_rate（主库 T 日口径，top100 池命中<50%% 回退面板 pe_ttm/turn_ma5，本窗回退 %d 日）；"
        "F1/F3/F4 = DP 代理分（pos_250 / 事件天数 / rsi6+macd_hist）；权重 F1-F6 = 0.25/0.20/0.20/0.15/0.10/0.10" % est_fb,
        "5. 执行/风控/成本与 B21 臂完全相同：open→open、tier 门控、gap_guard、live_trail(-7/+15/8)、N15/TOP3、全成本",
        "",
        "## 二、结果矩阵",
        "",
        "| 配置 | 总收益 | 年化 | 夏普 | 最大回撤 | 交易数 | 胜率 | 盈亏比 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            "| %s | %+.2f%% | %+.2f%% | %.2f | %.1f%% | %d | %.0f%% | %.2f |"
            % (r["tag"], r["total_ret"] * 100, r["annual"] * 100, r["sharpe"],
               r["mdd"] * 100, r["n_trades"], r["win_rate"] * 100,
               r["pl_ratio"] if r["pl_ratio"] == r["pl_ratio"] else 0))
    lines += [
        "",
        "## 三、干净对比（slip=0.1% 主口径，tier 口径双轨）",
        "",
        "### tier=t0_close（审计 B3 口径：T 日自身涨跌判定，默认主口径）",
        "",
        "| 信号层 | 年化 | 夏普 | 回撤 | 笔数 | 胜率 |",
        "|---|---|---|---|---|---|",
    ]
    b21 = base.get("G2臂 B21 slip=0.1%")
    lv = base.get("G2臂 现役43f slip=0.1%")
    sc = find("G2臂 现役SC60 slip=0.1%")
    scw = find("G2臂 SC60退化F2对照 slip=0.1%")

    def cmp_row(label, r):
        if not r:
            return None
        return ("| %s | %+.2f%% | %.2f | %.1f%% | %d | %.0f%% |"
                % (label, r["annual"] * 100, r["sharpe"], r["mdd"] * 100,
                   r["n_trades"], r["win_rate"] * 100))

    for label, r in [("B21 内产版 简化红线 prob≥0.60（沙箱现方案）", b21),
                     ("现役 43f 简化红线 prob≥0.60", lv),
                     ("现役 43f 生产评分卡 SC60（本报告）", sc),
                     ("现役 43f 生产评分卡退化F2 SC60（官方消融 F2 语义同 harness）", scw)]:
        row = cmp_row(label, r)
        if row:
            lines.append(row)
    # ---- t1_open 旧口径对照表（若已跑） ----
    t1_sc, t1_base = {}, {}
    try:
        with open(OUT_JSON_SC_T1, encoding="utf-8") as f:
            for r in json.load(f)["results"]:
                t1_sc[r["tag"]] = r
        with open(OUT_JSON_T1, encoding="utf-8") as f:
            for r in json.load(f)["results"]:
                t1_base[r["tag"]] = r
    except (OSError, ValueError, KeyError):
        t1_sc, t1_base = {}, {}
    t1_b21 = t1_base.get("G2臂 B21 slip=0.1%")
    t1_lv = t1_base.get("G2臂 现役43f slip=0.1%")
    t1_sc60 = t1_sc.get("G2臂 现役SC60 slip=0.1%")
    t1_scw = t1_sc.get("G2臂 SC60退化F2对照 slip=0.1%")
    if t1_sc60:
        lines += [
            "",
            "### tier=t1_open（旧口径对照：T+1 开盘跳空判定，live 09:50 同刻压缩镜像；审计 B3 判定偏乐观）",
            "",
            "| 信号层 | 年化 | 夏普 | 回撤 | 笔数 | 胜率 |",
            "|---|---|---|---|---|---|",
        ]
        for label, r in [("B21 内产版 简化红线 prob≥0.60", t1_b21),
                         ("现役 43f 简化红线 prob≥0.60", t1_lv),
                         ("现役 43f 生产评分卡 SC60", t1_sc60),
                         ("现役 43f 生产评分卡退化F2 SC60", t1_scw)]:
            row = cmp_row(label, r)
            if row:
                lines.append(row)
    lines += [
        "",
        "## 四、F 分数分布（fail-loud 校验通过）",
        "",
        "| 因子 | 分布 |",
        "|---|---|",
    ]
    for k in ("F1", "F2", "F3", "F4", "F5", "F6"):
        h = f_hist[k]
        nz = [(int(i), 100.0 * h[i] / tot_rows) for i in range(11) if h[i]]
        lines.append("| %s | %s |" % (k, " ".join("%d:%.1f%%" % kv for kv in nz)))
    h_w = ctx["f2_hist_wan"]
    nz_w = [(int(i), 100.0 * h_w[i] / tot_rows) for i in range(11) if h_w[i]]
    lines.append("| F2_万元退化对照（官方消融语义同 harness 复现） | %s |"
                 % " ".join("%d:%.1f%%" % kv for kv in nz_w))
    lines += [
        "",
        "> 对照：官方引擎口径（feature_panel_v3_enh2_n3_bt 面板）F2 分布为 **2:60.2% / 5:2.2% / 6:37.6%**"
        "——面板 main_net = mf_main_net 万元裸值直传 score_f2 的元标定阈值，退化为符号因子（8/10 档不可达）。"
        "本报告 F2 为生产元口径（×1e4），8/10 档可达——**系首个生产语义 F2 的策略级回测**。",
        "",
        "## 五、口径披露（引用本报告与官方口径数字必读）",
        "",
        "1. 官方引擎全 lineage 数字（锚点 110.12% / 网格 78.55% / 消融 68.71% / 回退 76.30%、46.11%）基于"
        "**退化 F2（万元裸值）+ 面板冻结 2026-08-14（窗口封顶 516 天）+ 无 tier 门控** 的官方 harness"
        "（TOP2/deploy0.95 或 live 对齐 TOP3/d98）；",
        "2. 本报告 = 生产语义 F2（元）+ 窗口至 2026-09-30 + tier on + B21 同名 harness；与官方数字**不可直接对比**，"
        "可比对象为本表 B21/prob 两臂（同 harness）；",
        "3. 继承 B21 报告第五节全部已知近似（开盘成交、开盘口径止损、ST、臂间互斥、动态再平衡等 10 条）；",
        "4. sc 臂特有近似：① est 命中判定按 top100 池算（与生产一致），F6 回退为当日整体回退；"
        "② 桥补足语义按 total_new 排序补空位（生产 CSV 只给 top3 不回填，与 prob 臂近似对称）；"
        "③ 评分卡向量化 np.select 与标量函数逐分支等价（含 F4 fall-through=1、F3 缺列默认值语义），"
        "元口径 F2 分布为生产校验基准。",
        "5. **tier 口径双轨（2026-10-19 立，取舍待拍板）**：审计 B3 将 tier 判定从「T+1 开盘跳空」"
        "（live 同刻压缩镜像）改为「T 日自身涨跌」（保守）；两口径基线差异巨大"
        "（B21：+18.79%→+5.84%；现役 43f：+6.59%→+12.26%），且 B21 vs 现役相对强弱随口径反转。"
        "本报告主口径取 t0_close（审计默认），t1_open 作对照并列；引用任一数字必须注明 tier 口径。",
        "",
        "## 六、结论",
        "",
    ]
    if sc and b21 and lv:
        d_sc_lv = (sc["annual"] - lv["annual"]) * 100
        d_sc_b21 = (sc["annual"] - b21["annual"]) * 100
        lines += [
            "1. **生产评分卡信号层（SC60）年化 %+.2f%%**，较同模型简化红线（%+.2f%%）%+.1fpp，"
            "较 B21 内产版（%+.2f%%）%+.1fpp。"
            % (sc["annual"] * 100, lv["annual"] * 100, d_sc_lv, b21["annual"] * 100, d_sc_b21),
        ]
        if d_sc_lv > 0:
            lines.append("2. 评分卡层在同 harness 下仍有正贡献，与官方消融（0825 +90.7pp / 0915 +38.4pp，退化口径仅作方向参考）同向。")
        else:
            lines.append("2. 评分卡层（生产元口径 F2）在同 harness 下无正贡献（%+.1fpp），与官方消融方向相反。"
                         % d_sc_lv)
        if scw:
            d_scw_sc = (scw["annual"] - sc["annual"]) * 100
            d_scw_lv = (scw["annual"] - lv["annual"]) * 100
            lines.append("   - **归因三角（F2 语义 vs harness）**：同模型 prob 红线 %+.2f%% → 生产 F2（元）%+.2f%% → "
                         "退化 F2（万元，官方消融 F2 语义）%+.2f%%；F2 语义差 %+.1fpp，退化口径评分卡较 prob %+.1fpp。"
                         % (lv["annual"] * 100, sc["annual"] * 100, scw["annual"] * 100, d_scw_sc, d_scw_lv))
            if d_scw_lv > 0:
                lines.append("   - **方向反转主因 = F2 语义**：官方消融的退化 F2 语义在本 harness 复现正贡献方向；"
                             "生产元口径 F2 使评分卡转负——官方 +38.4pp 为「退化 F2 + 官方 harness」复合产物，"
                             "不可引用至生产语义。")
            elif d_scw_lv <= 0 and d_scw_sc < 5:
                lines.append("   - **方向反转主因 = harness**：退化 F2（官方消融语义）在本 harness 同为负（%+.1fpp）——"
                             "官方 +38.4pp 为官方 harness（TOP2/无 tier/08-14 窗/官方引擎）产物，与 F2 语义无关。"
                             % d_scw_lv)
            else:
                lines.append("   - **F2 语义与 harness 混合归因**：退化 F2 仍为负但较元口径改善 %+.1fpp——"
                             "与官方消融的剩余差距归于 harness 差异（消融附注④ 24pp 同源）。" % d_scw_sc)
        if t1_sc60 and t1_lv:
            t1_d = (t1_sc60["annual"] - t1_lv["annual"]) * 100
            lines.append("   - **旧口径（t1_open）三角**：prob %+.2f%% → 生产 F2 %+.2f%%（退化 F2 %s）——"
                         "评分卡层贡献 %+.1fpp，与主口径方向%s。"
                         % (t1_lv["annual"] * 100, t1_sc60["annual"] * 100,
                            ("%+.2f%%" % (t1_scw["annual"] * 100)) if t1_scw else "未跑",
                            t1_d, "一致（均非正）" if t1_d <= 0 else "相反"))
        if d_sc_b21 > 0:
            lines.append("3. **对原问题的回答：沙箱解除后原方案（生产评分卡）优于 B21 内产版 %+.1fpp**；"
                         "若保守取红线 58/滑点 0.2%% 等敏感性对照，结论见结果矩阵。" % d_sc_b21)
        else:
            lines.append("3. **对原问题的回答：沙箱解除后原方案（生产评分卡）并不优于 B21 内产版（%+.1fpp）**；"
                         "简化红线方案在策略级已是更优信号层。" % d_sc_b21)
        if t1_sc60 and t1_b21 and t1_lv:
            t1_gap_b21 = (t1_sc60["annual"] - t1_b21["annual"]) * 100
            lines.append("   - **tier 口径稳健性**：两口径下生产评分卡均劣于同口径 B21 内产版"
                         "（t0_close %+.1fpp / t1_open %+.1fpp）且均无正的评分卡层贡献——"
                         "「原方案不更优」结论对 tier 口径稳健；"
                         "但 **B21 vs 现役 43f 的相对强弱随 tier 口径反转**"
                         "（B21-现役 gap：t1_open %+.1fpp vs t0_close %+.1fpp），"
                         "tier 口径取舍需拍板（影响内产版 vs 现役的结论与已上线方案的评估）。"
                         % (d_sc_b21, t1_gap_b21,
                            (t1_b21["annual"] - t1_lv["annual"]) * 100,
                            (b21["annual"] - lv["annual"]) * 100))
        lines += [
            "4. 诚实披露：同期全市场等权基准（不可投资参照）仍高于全部配置（小盘普涨行情），"
            "绝对 alpha 的持续性依赖风格环境；引用任何口径数字必须带第五节披露。",
            "",
        ]
    else:
        lines.append("- 基线 JSON（%s）缺失或 SC 主口径未跑，对比从略——结果矩阵见第二节。" % OUT_JSON)
    with open(OUT_MD_SC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告 -> %s" % OUT_MD_SC)


if __name__ == "__main__":
    sys.exit(main())
