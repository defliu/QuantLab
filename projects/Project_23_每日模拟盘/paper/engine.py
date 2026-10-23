# -*- coding: utf-8 -*-
"""模拟盘引擎 —— 逐日推进 + 状态持久化 + 净值记录。

核心区别于 backtest/engine.py 的地方：
  - 每次调用只推进「一天」，账本落盘，可随时中断/恢复
  - 幂等：同一天重复推进不会重复成交（按 last_date 拦截）
  - NAV 逐日追加到 CSV，可产出完整资金曲线

推进顺序（复刻 scan_rotate_cost_real.simulate）：
  1. 先卖出：遍历持仓，检查 MATURE/STOP/TP/TRAIL，遇一字跌停顺延
  2. 再买入：按候选池补足到 top_n，遇一字涨停/停牌跳过
  3. 盯市：按当日 close 更新持仓市值
  4. 记录 NAV
"""
import json
import os

import numpy as np
import pandas as pd

from . import execution as EX
from . import signals as SIG

_PROJ23 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(_PROJ23, "state")
NAV_DIR = os.path.join(_PROJ23, "results")

# 现金超支时的退档预留：覆盖最低佣金 5 元 + 少量取整余量
C_MIN_FEE_HEADROOM = 20.0


class PaperEngine:
    """单策略模拟盘引擎。"""

    def __init__(self, name, cfg, global_cfg, panel):
        self.name = name
        self.cfg = cfg
        self.g = global_cfg
        self.panel = panel
        self.state_path = os.path.join(STATE_DIR, "%s_state.json" % name)
        self.nav_path = os.path.join(NAV_DIR, "%s_nav.csv" % name)
        self.trade_path = os.path.join(NAV_DIR, "%s_trades.csv" % name)

    # ---------------- 状态 ----------------

    def empty_state(self):
        return {
            "strategy": self.name,
            "account_id": str(self.cfg.get("account_id", "")),
            "start_capital": float(self.g["start_capital"]),
            "cash": float(self.g["start_capital"]),
            "last_date": None,
            "positions": {},   # code -> {vol, buy_px, buy_date, peak, cost}
            "n_trades": 0,
        }

    def load_state(self):
        if not os.path.isfile(self.state_path):
            return self.empty_state()
        with open(self.state_path, encoding="utf-8") as f:
            st = json.load(f)
        # account_id 戳校验（沿用 AGENTS.md T-20260823-004 红线）
        expect = str(self.cfg.get("account_id", ""))
        got = str(st.get("account_id", ""))
        if expect and got != expect:
            raise ValueError(
                "[%s] state account_id 不符（state=%s / 配置=%s），拒绝加载。"
                "若确需重置，请手工备份并删除 %s" % (self.name, got, expect, self.state_path))
        return st

    def save_state(self, st):
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    # ---------------- 推进 ----------------

    def _prev_signal_date(self, d):
        """返回严格早于 d 的最近一个有信号文件的日期；无则 None。

        V1.3 的 final_list 本身是「T-1 晚产、T 日执行」命名，跨节假日会自动错配
        （如 09-30 final_list → 10-09 执行），因此统一按「严格小于 d」取。
        """
        if not hasattr(self, "_sig_dates"):
            self._sig_dates = SIG.available_dates(self.cfg)
        prev = None
        for sd in self._sig_dates:
            if sd < d:
                prev = sd
            else:
                break
        return prev

    def step(self, date, st=None):
        """推进一天。返回 (state, day_record)。date 为 pd.Timestamp。"""
        if st is None:
            st = self.load_state()
        d = pd.Timestamp(date)
        last = st.get("last_date")
        if last and pd.Timestamp(last) >= d:
            raise ValueError("[%s] %s 已推进过（last_date=%s），拒绝重复推进"
                             % (self.name, d.date(), last))

        trades_today = []
        slip = float(self.g.get("slippage", 0.001))
        top_n = int(self.cfg["top_n"])

        # ---- 1. 卖出 ----
        for code in list(st["positions"].keys()):
            pos = st["positions"][code]
            bar = self.panel.bar(d, code)
            if bar is None:
                # 停牌/无数据：保持估值，不顺延计数（简化处理）
                continue
            prev_close = bar.get("prev_close")
            ok, why = EX.can_sell_at_open(bar, prev_close, bool(bar.get("is_st", False)))
            if not ok:
                if why == "limit_down_open":
                    # 一字跌停：今日卖不掉，顺延
                    continue
                continue

            buy_date = pd.Timestamp(pos["buy_date"])
            held = len(self.panel.trading_dates(buy_date, d)) - 1

            # 峰值更新：T+1 卫生，买入当日不并入 high
            peak = float(pos.get("peak") or pos["buy_px"])
            hi = bar.get("high")
            if held >= 1 and hi and not (isinstance(hi, float) and np.isnan(hi)):
                peak = max(peak, float(hi))

            should, reason, override_px = EX.check_exit(pos, bar, held, self.cfg, peak)
            if not should:
                pos["peak"] = peak
                continue

            open_px = float(bar["open"])
            fill_px = override_px if override_px else open_px
            vol = int(pos["vol"])
            amount = vol * fill_px
            fee = EX.sell_fee(amount, slip)
            st["cash"] += amount - fee
            pnl = (amount - fee) - float(pos["cost"])
            trades_today.append({
                "date": d.strftime("%Y-%m-%d"), "code": code, "side": "SELL",
                "vol": vol, "price": round(fill_px, 4), "amount": round(amount, 2),
                "fee": round(fee, 2), "pnl": round(pnl, 2), "reason": reason,
                "hold_days": held,
            })
            st["n_trades"] += 1
            del st["positions"][code]

        # ---- 2. 盯市（买入前用当日 close 更新已有持仓市值）----
        mv = 0.0
        for code, pos in st["positions"].items():
            bar = self.panel.bar(d, code)
            if bar and bar.get("close"):
                pos["last_px"] = float(bar["close"])
            px = float(pos.get("last_px") or pos["buy_px"])
            mv += int(pos["vol"]) * px

        # ---- 3a. 超配减仓（动态等权，复刻 rebalance_g2.py L586-625）----
        # target_value = 资金池 × deploy_pct / TOP_N（固定分母，市值口径）
        # 对最终持有集每只：市值 > target_value → 减仓到目标股数，释放资金。
        # 保守口径：kept（未到期保留）只做下界减仓，不做逆信号加仓。
        # 仅当 cfg.rebalance_mode == "equal_weight" 时启用（G2 实盘口径）。
        sig_date = self._prev_signal_date(d)
        cands = SIG.load_candidates(self.cfg, sig_date) if sig_date is not None else None

        if str(self.cfg.get("rebalance_mode", "")) == "equal_weight":
            deploy = float(self.g.get("deploy_pct", 0.98))
            total_asset = st["cash"] + mv
            target_value = total_asset * deploy / float(top_n)
            for code in list(st["positions"].keys()):
                pos = st["positions"][code]
                bar = self.panel.bar(d, code)
                if bar is None or not bar.get("open"):
                    continue
                prev_close = bar.get("prev_close")
                ok, why = EX.can_sell_at_open(bar, prev_close, bool(bar.get("is_st", False)))
                if not ok:
                    continue
                open_px = float(bar["open"])
                held_vol = int(pos["vol"])
                held_val = held_vol * open_px
                if held_val <= target_value:
                    continue          # 未超配，不动（不下逆信号加仓）
                target_vol = EX.lot_floor(target_value / open_px)
                diff = held_vol - target_vol
                if diff <= 0:
                    continue
                # T+1：当日买入不可卖
                buy_date = pd.Timestamp(pos["buy_date"])
                if len(self.panel.trading_dates(buy_date, d)) - 1 < 1:
                    continue
                amount = diff * open_px
                fee = EX.sell_fee(amount, slip)
                st["cash"] += amount - fee
                pos["vol"] = held_vol - diff
                trades_today.append({
                    "date": d.strftime("%Y-%m-%d"), "code": code, "side": "SELL",
                    "vol": diff, "price": round(open_px, 4), "amount": round(amount, 2),
                    "fee": round(fee, 2),
                    "pnl": round((amount - fee) - float(pos["cost"]) * diff / held_vol, 2),
                    "reason": "OVR_TRIM", "hold_days": len(self.panel.trading_dates(buy_date, d)) - 1,
                })
                st["n_trades"] += 1
                if pos["vol"] <= 0:
                    del st["positions"][code]

        # 重算市值（减仓后）
        mv = 0.0
        for code, pos in st["positions"].items():
            bar = self.panel.bar(d, code)
            if bar and bar.get("close"):
                pos["last_px"] = float(bar["close"])
            mv += int(pos["vol"]) * float(pos.get("last_px") or pos["buy_px"])

        # ---- 3b. 买入（补足到 top_n）----
        # 前视防护（关键）：T 日开盘只能消费「T-1 及更早」的信号。
        # 实盘链路是 T-1 盘后产出候选 → T 日开盘执行；若用 T 日当天文件在 T 日开盘成交，
        # 等于用收盘后才生成的信息做开盘决策，构成前视偏差。
        if cands is not None and len(cands) > 0:
            total_asset = st["cash"] + mv
            deploy = float(self.g.get("deploy_pct", 0.98))
            eq_mode = str(self.cfg.get("rebalance_mode", "")) == "equal_weight"
            for _, row in cands.iterrows():
                if len(st["positions"]) >= top_n:
                    break
                code = str(row["ts_code"])
                if code in st["positions"]:
                    continue
                bar = self.panel.bar(d, code)
                prev_close = bar.get("prev_close") if bar else None
                ok, why = EX.can_buy_at_open(bar, prev_close, bool(bar.get("is_st", False)))
                if not ok:
                    continue
                open_px = float(bar["open"])
                if eq_mode:
                    # 动态等权：每只固定分配 target_value，而不是按剩余现金均分
                    target_value = total_asset * deploy / float(top_n)
                    budget = min(target_value, st["cash"])
                else:
                    slots = top_n - len(st["positions"])
                    budget = min(st["cash"] * deploy / max(slots, 1), st["cash"])
                # 预留费用与滑点空间：先按理论最大量算，费用超支再退一档
                vol, amount = EX.calc_buy_volume(budget / (1 + slip + 0.0001), open_px)
                if vol <= 0:
                    continue
                fee = EX.buy_fee(amount, slip)
                if amount + fee > st["cash"]:
                    vol, amount = EX.calc_buy_volume(
                        (st["cash"] - C_MIN_FEE_HEADROOM) / (1 + slip + 0.0001), open_px)
                    if vol <= 0:
                        continue
                    fee = EX.buy_fee(amount, slip)
                    if amount + fee > st["cash"]:
                        continue
                st["cash"] -= (amount + fee)
                st["positions"][code] = {
                    "vol": vol, "buy_px": open_px, "buy_date": d.strftime("%Y-%m-%d"),
                    "peak": open_px, "cost": round(amount + fee, 2), "last_px": open_px,
                }
                trades_today.append({
                    "date": d.strftime("%Y-%m-%d"), "code": code, "side": "BUY",
                    "vol": vol, "price": round(open_px, 4), "amount": round(amount, 2),
                    "fee": round(fee, 2), "pnl": 0.0, "reason": "OPEN", "hold_days": 0,
                })
                st["n_trades"] += 1
                mv += amount

        # ---- 4. 收盘盯市 + NAV ----
        mv = 0.0
        for code, pos in st["positions"].items():
            bar = self.panel.bar(d, code)
            if bar and bar.get("close"):
                pos["last_px"] = float(bar["close"])
            mv += int(pos["vol"]) * float(pos.get("last_px") or pos["buy_px"])

        total = st["cash"] + mv
        rec = {
            "date": d.strftime("%Y-%m-%d"),
            "total_asset": round(total, 2),
            "cash": round(st["cash"], 2),
            "market_value": round(mv, 2),
            "n_positions": len(st["positions"]),
        }
        st["last_date"] = d.strftime("%Y-%m-%d")
        self.save_state(st)
        self._append_nav(rec)
        self._append_trades(trades_today)
        return st, rec

    # ---------------- 落盘 ----------------

    def _append_nav(self, rec):
        os.makedirs(NAV_DIR, exist_ok=True)
        df = pd.DataFrame([rec])
        if os.path.isfile(self.nav_path):
            old = pd.read_csv(self.nav_path, encoding="utf-8-sig")
            df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)
        tmp = self.nav_path + ".tmp"
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        os.replace(tmp, self.nav_path)

    def _append_trades(self, rows):
        if not rows:
            return
        os.makedirs(NAV_DIR, exist_ok=True)
        df = pd.DataFrame(rows)
        if os.path.isfile(self.trade_path):
            old = pd.read_csv(self.trade_path, encoding="utf-8-sig")
            df = pd.concat([old, df], ignore_index=True)
        tmp = self.trade_path + ".tmp"
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        os.replace(tmp, self.trade_path)
