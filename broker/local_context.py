# coding=utf-8
"""LocalContext — 本地 miniQMT 适配器（全局共用）
把 QMT 策略里的 C.* 调用映射到本地 xtdata，用于本地快速验证。
用法见 broker/local_validate_demo.py 或各项目下的 local_validate.py"""
import sys
import time
import importlib.util


# ============ 连接管理 ============
_connected = False
_trader = None
_session_id = int(time.time())


def connect_data():
    """连接行情数据服务（58610）"""
    global _connected
    if _connected:
        return True
    from xtquant import xtdata
    xtdata.connect()
    _connected = True
    return True


def connect_trader(path=r"E:\国金QMT交易端模拟\userdata_mini", account_id="67014907"):
    """连接交易服务（58600），返回 (trader, account) 或 (None, None)"""
    global _trader
    if _trader is not None:
        return _trader, _trader._account
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount

    class _CB(XtQuantTraderCallback):
        def on_disconnected(self): pass
        def on_stock_trade(self, t): pass
        def on_order_error(self, e): pass

    trader = XtQuantTrader(path, _session_id)
    trader.register_callback(_CB())
    trader.start()
    rc = trader.connect()
    if rc != 0:
        return None, None
    acc = StockAccount(account_id)
    trader.subscribe(acc)
    _trader = trader
    trader._account = acc
    return trader, acc


# ============ LocalContext ============
class LocalContext:
    """本地 miniQMT 上下文，映射 C.* 调用到 xtdata

    策略代码里所有 C.xxx(...) 调用通过此类接住。
    本地不支持的接口抛 NotImplementedError，触发策略 fail-open。
    """

    def __init__(self):
        from xtquant import xtdata
        self._xtdata = xtdata

    def get_stock_list_in_sector(self, sector):
        """获取板块股票列表"""
        return self._xtdata.get_stock_list_in_sector(sector)

    def get_market_data_ex(self, stock_code=None, period="1d", count=60,
                           start_time="", end_time="", **kwargs):
        """获取行情数据，返回 {code: DataFrame}"""
        return self._xtdata.get_market_data_ex(
            [], stock_code, period=period, count=count,
            start_time=start_time, end_time=end_time
        )

    def get_instrument_detail(self, code):
        """获取合约详情"""
        return self._xtdata.get_instrument_detail(code)

    def get_stock_name(self, code):
        """获取股票名称"""
        try:
            d = self._xtdata.get_instrument_detail(code)
            if d:
                return d.get("instrument_name", "") or ""
        except Exception:
            pass
        return ""

    def get_stock_basic_info(self, code):
        """获取股票基本信息"""
        try:
            d = self._xtdata.get_instrument_detail(code)
            if d:
                return {"name": d.get("instrument_name", "") or ""}
        except Exception:
            pass
        return None

    # ---- 本地不支持的接口，抛异常触发 fail-open ----
    def get_turnover_rate(self, codes, start, end):
        raise NotImplementedError("xtdata 无 get_turnover_rate -> fail-open")

    def get_trade_detail_data(self, *a, **k):
        raise NotImplementedError("本地无持仓反查 -> fail-open")


# ============ 策略加载工具 ============
def load_strategy_source(src_path):
    """加载 QMT 策略源码（处理 GBK 头但实际 UTF-8 的编码问题）
    返回导入的模块对象"""
    raw = open(src_path, "rb").read()
    # 尝试 UTF-8 解码（大多数策略源码实际是 UTF-8）
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    # 修正文件头避免告警
    text = text.replace("# coding=gbk", "# coding=utf-8", 1)
    text = text.replace("# coding=gbk", "# coding=utf-8", 1)  # 处理重复头

    import os
    tmp_path = os.path.join(os.path.dirname(src_path), "_local_tmp.py")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)

    spec = importlib.util.spec_from_file_location("_local_tmp", tmp_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    try:
        os.remove(tmp_path)
    except Exception:
        pass
    return mod
