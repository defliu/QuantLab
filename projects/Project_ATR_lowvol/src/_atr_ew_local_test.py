# coding=utf-8
"""ATR equalweight 本地 miniQMT 验证运行器（py3.6 兼容，pythonw.exe 执行）。
只做选股管线验证，绝不真实下单（passorder 直接抛错）。
结果写入 D:/QuantLab/tmp_atr_ew_local_result.txt (UTF-8)。
"""
import os
import io
import time
import json
from datetime import datetime

RESULT_FILE = u"D:/QuantLab/tmp_atr_ew_local_result.txt"
BUILD_FILE = u"D:/QuantLab/projects/Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py"

_out = []
def log(msg):
    _out.append(msg)

def dump_result():
    try:
        with io.open(RESULT_FILE, "w", encoding="utf-8") as f:
            f.write(u"\n".join(_out))
    except Exception as e:
        try:
            with io.open(RESULT_FILE, "w", encoding="utf-8") as f:
                f.write(u"dump failed: %s\n" % e)
        except Exception:
            pass


class LocalContext(object):
    """把等权策略的 C.* 调用映射到本地 miniQMT xtdata（只读行情，禁下单）。"""
    do_back_test = False
    do_backtest = False

    def __init__(self):
        from xtquant import xtdata
        self._xtdata = xtdata
        try:
            xtdata.connect()
            log(u"[connect] xtdata.connect() done")
        except Exception as e:
            log(u"[connect] connect exception: %s" % e)

    def get_current_time(self):
        return datetime.now()

    def is_last_bar(self):
        return True

    def get_stock_list_in_sector(self, sec):
        try:
            codes = self._xtdata.get_stock_list_in_sector(sec)
            return codes or []
        except Exception as e:
            log(u"[sector] %s fail: %s" % (sec, e))
            return []

    def get_stock_basic_info(self, code):
        try:
            d = self._xtdata.get_instrument_detail(code)
            if d:
                return {"name": d.get("InstrumentName", code)}
        except Exception:
            pass
        return {"name": code}

    def get_turnover_rate(self, codes, start, end):
        # 本地 miniQMT 无换手接口：抛 AttributeError 触发策略 fail-open（与既有本地行为一致）
        raise AttributeError(u"local xtdata no get_turnover_rate -> fail-open")

    def get_market_data_ex(self, stock_code=None, period="1d", count=0, **kw):
        """按批次拉全市场日线，合并返回 {code: DataFrame}。"""
        if stock_code is None:
            stock_code = []
        all_codes = list(stock_code)
        merged = {}
        BATCH = 300
        t0 = time.time()
        n_batch = 0
        for i in range(0, len(all_codes), BATCH):
            batch = all_codes[i:i + BATCH]
            try:
                d = self._xtdata.get_market_data_ex([], batch, period=period, count=count)
                if d:
                    for k, v in d.items():
                        if v is not None and len(v) > 0:
                            merged[k] = v
                n_batch += 1
            except Exception as e:
                log(u"[market] batch %d fail: %s" % (i // BATCH, e))
        log(u"[market] get_market_data_ex codes=%d batches=%d got=%d dt=%.1fs"
            % (len(all_codes), n_batch, len(merged), time.time() - t0))
        return merged

    def get_account_info(self):
        raise Exception(u"local no account (not needed for screening)")

    def get_trade_detail_data(self, account, ctype, rtype):
        # 不提供反查 -> 策略走 OPTIMISTIC
        return None

    def passorder(self, *args, **kwargs):
        raise RuntimeError(u"LOCAL TEST: passorder 被调用=策略尝试真实下单，禁止！args=%s" % (args,))


def main():
    log(u"=== ATR equalweight 本地 miniQMT 验证 ===")
    log(u"time: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 0) 顶层 xtdata 别名注入：构建版里是 `import xtdata`，本地实际包是 xtquant.xtdata
    try:
        from xtquant import xtdata as _xtdata
        import sys as _sys
        _sys.modules["xtdata"] = _xtdata
        log(u"[alias] sys.modules['xtdata'] -> xtquant.xtdata OK")
    except Exception as e:
        log(u"[alias] 注入失败: %s" % e)

    # 1) 加载等权构建模块（GBK -> exec）
    raw = open(BUILD_FILE, "rb").read()
    src = raw.decode("gbk")
    ns = {}
    exec(compile(src, "strategy_atr_lowvol_equalweight", "exec"), ns)
    log(u"[module] build loaded OK, functions: %s" % sorted(
        [k for k in ns if callable(ns[k]) and not k.startswith("__")])[:8])

    # 2) 配置加载
    ns["_load_config"]()
    log(u"[config] N_HOLD=%d MAX_PRICE=%s STRATEGY_CAPITAL=%d FREQ=%s ATR<%.2f"
        % (ns["_N_HOLD"], ns["_MAX_PRICE"], ns["_STRATEGY_CAPITAL"], ns["_REBALANCE_FREQ"], ns["_ATR_THRESHOLD"]))

    # 3) 选股（空持仓）
    ns["_g_my_codes"] = {}
    C = LocalContext()
    t0 = time.time()
    try:
        selected = ns["_run_screening"](C)
        log(u"[screening] 用时 %.1fs, 入选 %d 只" % (time.time() - t0, len(selected or [])))
    except Exception as e:
        import traceback
        log(u"[screening] 异常: %s" % e)
        log(u"[screening] traceback: %s" % traceback.format_exc())
        selected = []
        dump_result()
        return

    # 4) 输出入选明细
    log(u"--- 入选 %d 只（含 max_price 过滤后）---" % len(selected))
    # 统计 max_price 排除规模（全市场真实价>=50 的只数，来自策略拉取的全市场数据）
    try:
        all_data = ns["_g_all_data"]
        hi = 0
        tot = 0
        for code, df in all_data.items():
            if df is None or len(df) == 0:
                continue
            tot += 1
            try:
                if float(df["close"].iloc[-1]) >= 50.0:
                    hi += 1
            except Exception:
                pass
        log(u"[max_price] 全市场 %d 只中 真实价>=50 元 有 %d 只（会被过滤）" % (tot, hi))
    except Exception as e:
        log(u"[max_price] 统计失败: %s" % e)
    prices = {}
    for code in selected:
        try:
            d = C.get_market_data_ex([code], period="1d", count=2)
            if d and code in d and len(d[code]) > 0:
                prices[code] = float(d[code]["close"].iloc[-1])
        except Exception:
            pass
    for code in selected:
        name = C.get_stock_basic_info(code).get("name", code)
        px = prices.get(code, 0)
        log(u"  %s %s 现价=%.2f %s" % (code, name, px, u"OK(<50)" if 0 < px < 50 else u"!!价>=50"))

    # 5) 止损评估（空仓 -> 应返回空）
    try:
        stops = ns["_evaluate_interim_stops"](C, {})
        log(u"[stops] 空仓止损评估返回 %d 条（应为0）" % len(stops))
    except Exception as e:
        log(u"[stops] 异常: %s" % e)

    # 6) 复选：关 ROE 门控重跑（本地 miniQMT 无财务数据 -> 原逻辑会把空结果当'过滤全部'，
    #    为验证选股管线，这里模拟 fail-open 关掉 ROE）
    log(u"")
    log(u"=== 复选：QUALITY_GATE=0（本地无财务数据，模拟 ROE fail-open）===")
    ns["_QUALITY_GATE"] = 0
    ns["_g_hold_pool_cache"] = None
    ns["_g_hold_pool_cache_date"] = ""
    ns["_g_my_codes"] = {}
    try:
        selected2 = ns["_run_screening"](C)
        log(u"[screening2] 入选 %d 只" % len(selected2 or []))
        prices2 = {}
        for code in (selected2 or []):
            try:
                d = C.get_market_data_ex([code], period="1d", count=2)
                if d and code in d and len(d[code]) > 0:
                    prices2[code] = float(d[code]["close"].iloc[-1])
            except Exception:
                pass
        for code in (selected2 or []):
            name = C.get_stock_basic_info(code).get("name", code)
            px = prices2.get(code, 0)
            log(u"  %s %s 现价=%.2f %s" % (code, name, px,
                u"OK(<50)" if 0 < px < 50 else u"!!价>=50(异常)"))
    except Exception as e:
        import traceback
        log(u"[screening2] 异常: %s" % e)
        log(u"[screening2] tb: %s" % traceback.format_exc())

    log(u"[done] 验证完成")
    dump_result()


if __name__ == "__main__":
    main()
