# -*- coding: utf-8 -*-
"""gate_strategy_layer.py —— 周更后「策略层回测门禁」（T-20260910-003）。

分工方案（T-20260910-002 落地）：
  模型层   ：train_g2.py --promote 的 G1-G4 strict 门禁（同窗口IC/尾部IC/Top2模拟/观察期回滚）
  策略层   ：本脚本 —— 新模型在「历史网格寻优最优配置」下跑官方引擎回测，
             必须不差于当前 live 模型（同一配置），否则拒绝 promote / 触发回滚。

为什么需要这道门：
  模型层门禁只保证「预测质量不退步」（IC/尾部IC），但 IC 与实盘收益隔着
  评分卡选股/过滤/出场/组合，IC 涨不代表策略收益涨（09-07 模型即反例：
  IC 0.079 过关、策略回测却只有 +0.039%）。策略层门禁直接比「钱」，
  在历史验证过的最优配置下要求新模型 >= live，防「越训越差」。

用法（在项目根目录运行）：
  python gate_strategy_layer.py --strategy G2 --candidate <候选模型> --meta <候选meta> [--dry-run]
  python gate_strategy_layer.py --strategy G2 --candidate <候选> --meta <meta> --rollback
  # --rollback：门禁失败时自动把 live 指针回退到候选之前的 live（data/g2_live_model.json）

退出码：
  0 = 通过（候选策略层不差于 live）
  2 = 未通过（候选策略层差于 live；--rollback 时已回滚）
  1 = 运行错误（无法评估，拒绝 promote）

对比逻辑：
  1. 读 data/strategy_cfg_lib.json 取该策略的最优配置（N/EXIT/TOP/红线/面板）
  2. 分别用 live 模型与候选模型跑官方引擎回测（同一配置，环境变量注入）
  3. 比较 daily_excess：候选 >= live - 0.0002（绝对容差 0.02pp）即通过
"""
import argparse
import datetime
import importlib
import json
import os
import shutil
import sys

import numpy as np

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

CFG_LIB = os.path.join(DC.DATA_DIR, "strategy_cfg_lib.json")
G2_POINTER = DC.G2_LIVE_POINTER
TOL = 0.0002  # 绝对容差：候选 >= live - 0.02pp 即通过（吸收面板重建/评分卡噪声）

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(m):
    print(m, flush=True)


def run_backtest(model_path, meta_path, cfg, extra_env=None):
    """在指定策略配置下跑官方引擎回测，返回 daily_excess。"""
    import scan_rotate_cost_real as SC

    # 清残留
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP", "BT_START", "BT_END", "BT_PKOUT"):
        os.environ.pop(k, None)
    c = cfg["config"]
    os.environ["BT_PANEL"] = os.path.join(PROJ, c["BT_PANEL"].replace("/", os.sep).replace("\\", os.sep))
    os.environ["BT_MODEL"] = model_path
    os.environ["BT_META"] = meta_path
    os.environ["BT_THRESHOLD"] = c.get("BT_THRESHOLD", "60.0")
    os.environ["BT_EXIT"] = c["EXIT"]
    os.environ["BT_SLIPS"] = "0.001"
    os.environ["BT_TOP"] = str(c["TOP"])
    if c.get("BT_POSFILTER"):
        os.environ["BT_POSFILTER"] = c["BT_POSFILTER"]
    if c.get("BT_ENSEMBLE"):
        os.environ["BT_ENSEMBLE"] = c["BT_ENSEMBLE"]
        os.environ["BT_MODEL2"] = c["BT_MODEL2"]
        os.environ["BT_META2"] = c["BT_META2"]
    if extra_env:
        os.environ.update(extra_env)
    importlib.reload(SC)
    dates, per_day, market_daily, open_map = SC.build_per_day()
    trades, daily_ret, n_skip = SC.simulate(dates, per_day, int(c["N"]), 0.001, open_map, exec_ok=True)
    rets = [t[2] for t in trades]
    fwd = np.array(rets)
    win = float((fwd > 0).mean()) if len(fwd) else np.nan
    aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
    al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
    pl = float(aw / abs(al)) if al else np.nan
    nav = (1 + daily_ret.fillna(0.0)).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    excess = float(daily_ret.fillna(0.0).mean() - market_daily.fillna(0.0).mean())
    return {"excess": excess, "win": win, "pl": pl, "mdd": mdd, "n": len(trades)}


def load_live_g2():
    """读取当前 G2 live 模型路径与 meta（回退 08-25 初始 live）。"""
    return DC.g2_live()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=["G2", "融合", "V1.4"], help="候选配置库中的策略键")
    ap.add_argument("--candidate", required=True, help="候选模型路径")
    ap.add_argument("--meta", required=True, help="候选模型 meta（features_*.json）")
    ap.add_argument("--live", default="", help="对比基准 live 模型（缺省：G2/融合读 g2_live 指针，V1.4 用 v3_enh）")
    ap.add_argument("--live-meta", default="", help="对比基准 live 模型 meta")
    ap.add_argument("--dry-run", action="store_true", help="只判定不落盘/不回滚")
    ap.add_argument("--rollback", action="store_true", help="门禁失败时自动回滚 live 指针（仅 G2 支持）")
    args = ap.parse_args()

    lib = json.load(open(CFG_LIB, encoding="utf-8"))
    if args.strategy not in lib["strategies"]:
        log(f"!! 策略 {args.strategy} 不在配置库")
        sys.exit(1)
    cfg = lib["strategies"][args.strategy]
    cand = os.path.abspath(args.candidate)
    meta = os.path.abspath(args.meta)
    if not os.path.exists(cand) or not os.path.exists(meta):
        log(f"!! 候选或 meta 不存在: {cand} / {meta}")
        sys.exit(1)

    log("=" * 68)
    log(f"[gate_strategy_layer] 策略层回测门禁  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"策略: {args.strategy}（{cfg['体系']}）| 配置: {cfg['config']['N']}N/{cfg['config']['EXIT']}/红线{cfg['config'].get('BT_THRESHOLD')}/TOP{cfg['config']['TOP']}")
    log(f"候选: {os.path.basename(cand)}")
    log("=" * 68)

    # ---- live 模型来源 ----
    if args.live and os.path.exists(args.live):
        live_model = os.path.abspath(args.live)
        live_meta = os.path.abspath(args.live_meta) if args.live_meta and os.path.exists(args.live_meta) else meta
    elif args.strategy in ("G2", "融合"):
        live_model, live_meta = load_live_g2()
    else:  # V1.4
        live_model = DC.model_file("_v3_enh")
        live_meta = os.path.join(DC.DATA_DIR, "features_v3_enh.json")
    log(f"live : {os.path.basename(live_model)}")

    # ---- 跑对比 ----
    log(f"[1/2] live 模型回测（{args.strategy} 最优配置）...")
    m_live = run_backtest(live_model, live_meta, cfg)
    log(f"      excess={m_live['excess']:+.4f} 胜率={m_live['win']:.1%} 盈亏比={m_live['pl']:.2f} 回撤={m_live['mdd']:.1%} 交易={m_live['n']}")
    log(f"[2/2] 候选模型回测（同一配置）...")
    m_cand = run_backtest(cand, meta, cfg)
    log(f"      excess={m_cand['excess']:+.4f} 胜率={m_cand['win']:.1%} 盈亏比={m_cand['pl']:.2f} 回撤={m_cand['mdd']:.1%} 交易={m_cand['n']}")

    # ---- 判定 ----
    diff = m_cand["excess"] - m_live["excess"]
    ok = m_cand["excess"] >= m_live["excess"] - TOL
    log("-" * 68)
    log(f"候选 excess {m_cand['excess']:+.4f} vs live {m_live['excess']:+.4f}（差 {diff:+.4f}，容差 {TOL:+.4f}）")
    if ok:
        log(f"判定：PASS —— 候选策略层不差于 live（{args.strategy} 最优配置口径）")
        sys.exit(0)
    else:
        log(f"判定：FAIL —— 候选策略层差于 live {abs(diff):.4f} pp，拒绝上线")
        if args.rollback and args.strategy in ("G2", "融合"):
            # 读取 live 指针的 prev（train_g2 --promote 写入的 trial.prev_model）
            try:
                d = json.load(open(G2_POINTER, encoding="utf-8"))
                prev = d.get("trial", {}).get("prev_model")
                prev_meta = None
                if prev and os.path.exists(prev):
                    # prev meta 从模型文件名推导（features_v3_g2_strong_real_<date>.json）
                    stamp = os.path.basename(prev).split("_")[-2]  # ..._20260825_1964t.txt
                    cand_meta_candidates = [os.path.join(DC.DATA_DIR, f"features_v3_g2_strong_real_{stamp}.json")]
                    prev_meta = next((p for p in cand_meta_candidates if os.path.exists(p)), None)
                if not prev_meta:
                    log("  [rollback] 未找到 prev_meta，跳过自动回滚（保持当前 live，需人工）")
                elif args.dry_run:
                    log(f"  [rollback][dry-run] 将回滚 live -> {os.path.basename(prev)}")
                else:
                    shutil.copy2(G2_POINTER, G2_POINTER + f".bak_gate_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
                    d["model_path"] = prev
                    d["meta_path"] = prev_meta
                    d["note"] = (d.get("note", "") + f" | gate回滚 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: 候选 {os.path.basename(cand)} 策略层 FAIL (excess {m_cand['excess']:+.4f} < live {m_live['excess']:+.4f})")
                    json.dump(d, open(G2_POINTER, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                    log(f"  [rollback] 已回滚 live -> {os.path.basename(prev)}（备份指针 .bak_gate_*）")
            except Exception as e:
                log(f"  [rollback] 回滚失败: {e}")
        elif args.rollback:
            log("  [rollback] 当前策略不支持自动回滚（仅 G2/融合），需人工决策")
        sys.exit(2)


if __name__ == "__main__":
    main()
