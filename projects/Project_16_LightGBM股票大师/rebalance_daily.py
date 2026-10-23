# coding: utf-8
"""方案A·每日换仓执行层：持仓 vs 当日评分 top2 → 卖被PK的、买新晋的（等权对齐，策略资金池为基准）。

逻辑（每日 9:45，开盘15分钟后资金流稳定）：
  1) 读当日完整版清单 data/selections/<date>_selection_full.csv
  2) 目标持仓 = 清单中 total>=红线(58) 的前 TOP(2) 只（按 total 降序）
  3) 读当前策略持仓（QMT 实时，volume>0 才算持仓；成本用 open_price，缺则本地买入价补）
  4) 卖出（到期制，替代原 PK_OUT 日频翻转）：持仓掉出目标 top2 且已满 hold_days(默认5) 交易日 → 卖（数量=今日可卖 can_use_volume；T+1 锁定的今天不卖；持仓未满期限者保留，不再日频翻转）
  5) 买入（等权对齐，2026-08-24 修复）：
     - 资金池基准 = strategy_capital.json 的 capital（初始10万 + 已实现盈亏 + 策略持仓浮盈），
       收益滚动、亏损不补；买入预算绝不用账户全量资金。
     - 每只目标市值 = 资金池 × 98% ÷ 目标数。
     - 对每只 target：按"目标股数 = 目标市值÷现价(整手向下取整)"对齐，当前持有股数：
       不足 → 补仓到目标股数；超配 → 减仓到目标股数（受今日可卖限制）。
     ⚠️ 修复前 bug1：买入用了账户全量可用资金(asset.cash)，导致新晋票独吞 95% 资金
        （如 300684 单票买进 939 万，占账户 96%，远超 10 万策略资金池）。
        bug2：只给"未持有的新晋票"分配资金，已持有的 target 不补仓。
        bug3(本次修复)：超配减仓按"市值差额取整"算股数，导致减仓后剩余市值仍远超目标
        （300684 减到 5,200 股≈44万，仍超配9倍）；改为按目标股数对齐，减到目标市值。
  6) 边界：价格风控(止损/止盈/移动止盈)由 qmt_monitor 六档盯盘负责，本脚本只做评分换仓；
     新票必须 total>=红线，不足则宁缺毋滥。

用法：
  python rebalance_daily.py                    # 默认今天，dry-run
  python rebalance_daily.py --date 20260821    # 指定日期
  python rebalance_daily.py --live             # 真实换仓（先卖后买，慎用）
输出：
  data/rebalance_<date>.json          （--live 真实换仓执行结果，含 executed_live 幂等标记）
  data/rebalance_<date>.dryrun.json   （dry-run 计划，防覆盖当日 live 执行记录）
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, date, timedelta

import qmt_config as C
import strategy_capital as SC
import position_filter as PF
import is_trade_day as ITD

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)

REDLINE = 58.0
TOP_N = 5  # 2026-09-21 拍板 TOP_N 2→5（依据《持仓只数最优论证_20260921.md》）
HOLD_DAYS = 5  # 持有期满交易日数（到期制卖出，替代原 PK_OUT 日频翻转；T-20260904-001）
CAP_FILE = os.path.join(os.path.dirname(C.TRADE_LOG), "strategy_capital.json")


def load_strategy_capital():
    """读取策略资金池 = 初始10万 + 已实现盈亏 + 策略持仓浮盈（strategy_capital.json）。

    策略只用 START_CAPITAL 建仓，收益滚动进资金池，亏损不补资金；
    买入预算一律以资金池为准，绝不用账户全量资金。
    文件缺失/账号戳校验失败时回退 START_CAPITAL（校验逻辑见 qmt_config.load_capital_pool）。
    """
    data = C.load_capital_pool()
    if data:
        try:
            cap = float(data.get("capital", 0) or 0)
            if cap > 0:
                return cap
        except (TypeError, ValueError) as e:
            print(f"    !! 资金池字段异常: {e!r}，回退 START_CAPITAL")
    return float(C.START_CAPITAL)


def today_str():
    return time.strftime("%Y%m%d")


def _parse_date(s):
    """'YYYYMMDD' -> date 对象（解析失败回退今天）。"""
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError):
        return date.today()


def _build_last_buy_dates():
    """从成交记录构建 {code: 最近一次 BUY 日期(date)}，用于到期制持仓天数判定。

    无买入记录（如转入持仓）的 code 不出现在此 dict → 调用方视为已满期（held=999）。
    """
    last = {}
    for row in C.load_trade_log_rows():
        if row.get("side") != "BUY":
            continue
        code = (row.get("code") or "").strip()
        t = row.get("time") or ""
        if not code or not t:
            continue
        fmt = "%Y-%m-%d %H:%M:%S" if " " in t else "%Y-%m-%d"
        try:
            d = datetime.strptime(t[:19] if " " in t else t, fmt).date()
        except ValueError:
            continue
        if code not in last or d > last[code]:
            last[code] = d
    return last


def _load_trade_calendar():
    """加载权威交易日历 data/ashare_trade_dates.txt（YYYY-MM-DD 每行一日）。

    返回 date 集合；文件缺失/损坏返回 None（调用方回退周末近似）。
    """
    path = os.path.join(os.path.dirname(C.TRADE_LOG), "ashare_trade_dates.txt")
    days = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                days.add(datetime.strptime(s, "%Y-%m-%d").date())
    except Exception as e:
        print(f"    !! 交易日历读取失败（持有天数回退周末近似）: {e!r}")
        return None
    return days or None


_CAL_CACHE = None
_CAL_LOADED = False


def _get_trade_calendar():
    global _CAL_CACHE, _CAL_LOADED
    if not _CAL_LOADED:
        _CAL_CACHE = _load_trade_calendar()
        _CAL_LOADED = True
        if _CAL_CACHE is None:
            print("    !! 交易日历不可用（ashare_trade_dates.txt 缺失/损坏），"
                  "持有天数回退周末近似（少计→卖出延迟，方向保守）")
    return _CAL_CACHE


def _is_trade_day(d, cal):
    """单日是否 A 股交易日：权威日历优先，未覆盖日走 is_trade_day 法定节假日表兜底。

    与 rebalance_g2.py / rebalance_ens.py 的 `_count_trade_days` 完全同口径，
    保证三臂持有期语义一致（V1.x / G2 / ENS 可直接横向比较）。

    绝不再使用「只跳周末」近似——那会把国庆/春节长假计成交易日 → MATURE 提前
    触发 → 长假后首日把只持有 1 个交易日的持仓全部清仓（2026-10-08 实盘事故根因，
    见 T-20261008-004；每逢春节/国庆后首个交易日必复发）。
    """
    try:
        return bool(ITD.is_trade_day(d, cal or set()).get("is_trade_day"))
    except Exception as e:
        # 兜底模块不可用：判非交易日（少计 → 晚卖，方向保守），并 fail-loud
        print("    !! is_trade_day 不可用（持有天数按非交易日计，到期卖出延迟）: %r" % (e,))
        return False


def _trading_days_between(d0, d1):
    """d0 之后到 d1（含）的交易日数（T-20261008-004：改用权威交易日历）。

    买入日 d0 当日不可卖（T+1），从 d0+1 起算持有交易日；用于到期制判定
    （持仓满 HOLD_DAYS 交易日即触发到期卖出）。

    判定口径（逐自然日过 `_is_trade_day`）：
      周末 → 非交易日；权威日历命中 → 交易日；
      日历未覆盖（如当日尚未入历）但非节假日 → 交易日；法定节假日 → 非交易日。
    长假因此绝不被计入持有期，MATURE 只可能「按真实交易日数」触发，不再早卖。
    """
    if not d0 or not d1 or d1 <= d0:
        return 0
    cal = _get_trade_calendar()
    n, cur = 0, d0
    one = timedelta(days=1)
    while cur < d1:
        cur += one
        if _is_trade_day(cur, cal):
            n += 1
    return n


# ---- 2026-10-08 事故修复批：LIVE 互斥锁 / 逐笔落账 / 硬化退出 ----
# 事故背景（详见 全局控制台 T-20261008-003/004）：TRAE 主任务迟到 + 10:05 guard 按
# 「json 不存在」误判未执行 + TRAE agent 两次重试 → 4 个 rebalance_daily.py 并发 --live
# → 重复卖出 + 空仓误判 + 50 笔废单 + 误报 json；且 xtquant 非守护线程挂起致 3 组进程
# 挂死 8 小时、当日 5 笔真实卖出 0 行入账（人工对账补账）。
_ACTIVE_LIVE_LOCK = None


def _pid_alive(pid):
    """Windows 下判定进程是否存活；无法判定时按存活处理（保守：宁可拒跑不并发）。"""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return False
        finally:
            k32.CloseHandle(h)
    except Exception:
        return True


def _acquire_live_lock():
    """--live 互斥锁（T-20261008-005）：防多入口并发跑真实换仓。

    多入口 = TRAE 09:45 主任务 / Windows 10:05 guard 兜底 / agent 手工重试。
    锁必须放在本脚本内部（TRAE 与 guard 都是直接启动本脚本，外部锁拦不住）。
    规则：锁文件 data/rebalance_live.lock 内容 "pid\\t日期"——他进程当日存活 →
    拒跑（返回 None）；持锁进程已死 / 锁为隔日（挂死残留）→ 接管（挂死进程最多
    拖到隔日自愈）。O_CREAT|O_EXCL 原子创建，避免双进程同时通过存在性检查。
    """
    path = os.path.join(os.path.dirname(C.TRADE_LOG), "rebalance_live.lock")
    me = os.getpid()
    today = today_str()
    for _ in range(2):  # 第 2 轮仅在清理陈旧锁后重试
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{me}\t{today}\n")
            return path
        except FileExistsError:
            try:
                with open(path, encoding="utf-8") as f:
                    pid_s, day_s = (f.read().strip().split("\t") + ["", ""])[:2]
                pid = int(pid_s)
            except Exception:
                pid, day_s = -1, ""
            if pid > 0 and day_s == today and _pid_alive(pid):
                print(f"    !! [LIVE锁] 另一 LIVE 换仓进程 pid={pid} 当日运行中，"
                      f"拒绝并发（防双卖/双买，2026-10-08 事故形态）")
                return None
            print(f"    !  [LIVE锁] 清理陈旧锁（pid={pid}, 锁日={day_s or '未知'}）后接管")
            try:
                os.remove(path)
            except OSError:
                pass
        except OSError as e:
            print(f"    !! [LIVE锁] 加锁异常，拒绝 LIVE 执行（防并发）: {e!r}")
            return None
    return None


def _release_live_lock(path):
    """释放 LIVE 锁（只删自己写的锁，防误删他进程接管的锁）。"""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            pid_s = f.read().strip().split("\t")[0]
        if int(pid_s) == os.getpid():
            os.remove(path)
    except Exception:
        pass


def _book_fill_row(row):
    """成交行逐笔即时落账（T-20261008-007）。

    旧实现把成交行攒到 main() 末尾统一写：进程中途挂死/被杀（2026-10-08 实测
    xtquant 非守护线程挂起，Run B 卖出确认后 0 行入账）→ 账实脱节只能人工补账。
    现每确认一笔成交立即 append 落盘：挂死最多丢「等待中的未确认委托」，
    已确认成交绝不丢。落账失败必须显式告警（记账丢失属 P0）。
    """
    try:
        C.append_trade_rows([row])
        print(f"    [记账] {row[1]} {row[2]} {row[3]}股 @{row[4]}"
              f" -> {os.path.basename(C.TRADE_LOG)}")
    except Exception as e:
        print(f"    !! [记账] {row[1]} {row[2]} {row[3]}股 落账失败（P0！须人工补账）: {e!r}")


def _hard_exit(code=0):
    """硬化退出（T-20261008-006）：绕过解释器退出阶段的非守护线程 join。

    2026-10-08 实测：xtquant 存在非守护线程，main() 正常完成后进程仍可挂死
    （Run D 10:49 完成 main 后残留 8 小时 → 被误判「未执行」诱发后续并发）。
    显式 flush 后 os._exit 立即终止，残留线程随进程销毁。
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def _candidate_meta(date, csv_path, loader):
    """候选溯源 meta（2026-09-28 候选可审计性修复①+②，缺陷B）。

    V1.3 的 rebalance json 曾有 selection_file/candidates_file 字段（20260901/0902），
    09-03 起随脚本改版丢失（DE 排查 F5）；本次修回并升级为完整溯源（sha256 + asof 留档）。
    V1.3 候选为当日早晨生成当日消费（无跨日覆盖），溯源主要防「同日复核重跑/TRAE 任务重写」
    导致的事后失真。
    loader: exact=当日清单精确匹配 / none=清单缺失或为空。
    """
    return {
        "candidate_file": csv_path,        # 实际加载的清单文件路径
        "candidate_loader": loader,
        "candidate_data_date": date,       # V1.3 清单以运行日命名，数据日=运行日
        "candidate_rows_raw": 0,           # CSV 原始行数（红线过滤前）
        "candidate_sha256": None,          # 内容哈希（核心：防事后覆写失真）
        "candidate_asof": None,            # asof 只读留档路径（读取点字节快照）
    }


def _archive_candidate_asof(date, meta, raw):
    """asof 只读留档（修复③之 3d 读取侧变体）：把本次实际读取的清单字节写入
    data/candidates_asof/<date>_<sha8>_<原名>。文件名含内容哈希 → 天然幂等，
    内容不同则共存，绝不覆盖已有留档。失败只打日志，绝不阻断换仓。"""
    try:
        adir = os.path.join(os.path.dirname(C.TRADE_LOG), "candidates_asof")
        os.makedirs(adir, exist_ok=True)
        sha8 = (meta.get("candidate_sha256") or "nasha")[:8]
        base = os.path.basename(meta.get("candidate_file") or "selection_full.csv")
        adst = os.path.join(adir, "%s_%s_%s" % (date, sha8, base))
        if not os.path.exists(adst):
            with open(adst, "wb") as f:
                f.write(raw)
        meta["candidate_asof"] = adst
    except Exception as e:
        print(f"  [asof留档] 失败（不阻断换仓）: {e}")


def load_selection(csv_path, top_n, text=None, meta=None):
    """读完整版清单，返回 total>=REDLINE 按 total 降序的前 top_n 只。
    text 参数（2026-09-28 溯源修复）：传入已读取的清单文本做单次读盘解析，
    保证 sha/内容/asof 留档与实际解析三者同源；缺省时自行读盘（向后兼容）。
    meta 参数（2026-09-29 DE 复核修正）：可选 dict，回填 candidate_rows_filtered =
    红线过滤后、top_n 截断前的候选数——修复 candidate_rows 误记 len(target)（截断后）
    导致口径与规格「过滤后候选数」及 G2/ENS 不一致的缺陷；不影响返回值。"""
    if text is None:
        with open(csv_path, encoding="utf-8-sig") as f:
            text = f.read()
    stocks = []
    for row in csv.DictReader(io.StringIO(text)):
        code = (row.get("ts_code") or "").strip()
        if not code:
            continue
        try:
            total = float(row.get("total", 0) or 0)
        except ValueError:
            total = 0.0
        if total >= REDLINE:
            stocks.append({"code": code, "name": row.get("name", ""),
                           "total": total, "prob": float(row.get("model_prob", 0) or 0)})
    stocks.sort(key=lambda s: s["total"], reverse=True)
    if meta is not None:
        meta["candidate_rows_filtered"] = len(stocks)   # 过滤后、截断前（2026-09-29 DE 复核修正口径）
    return stocks[:top_n]


def load_positions_qmt():
    """QMT 实时持仓。返回 (positions, volumes, sellable)。

    positions: {code: open_price}  所有 volume>0 的持仓（含 T+1 锁定，用于算持有市值/判断是否持有）
    volumes:   {code: volume}       总持有量
    sellable:  {code: can_use_volume} 今日可卖量（T+1 锁定的为 0）
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            return {}, {}, {}
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(5)
        positions = trader.query_stock_positions(account)
        if not positions:
            return {}, {}, {}
        pos, volumes, sellable = {}, {}, {}
        for p in positions:
            code = getattr(p, "stock_code", "")
            total = int(getattr(p, "volume", 0) or 0)
            if not code or total <= 0:
                continue
            pos[code] = float(getattr(p, "open_price", 0) or 0)
            volumes[code] = total
            sellable[code] = int(getattr(p, "can_use_volume", 0) or 0)
        return pos, volumes, sellable
    except Exception as e:
        print(f"    !! QMT 查询持仓异常: {e!r}")
        return {}, {}, {}
    finally:
        trader.stop()


def load_positions_log():
    """从 qmt_trade_log 估算持仓。返回 (positions, volumes, sellable)。

    positions: {code: cost(0占位)}  BUY-SELL 净持仓>0
    volumes:   {code: net}          总持有量
    sellable:  {code: net}          可卖量（今日买入的 T+1 锁定为 0）
    """
    rows = C.load_trade_log_rows()
    if not rows:
        return {}, {}, {}
    bought, sold = {}, {}
    today = time.strftime("%Y-%m-%d")
    locked_codes = set()
    for row in rows:
        code = row.get("code", "")
        if not code:
            continue
        try:
            vol = int(float(row.get("vol", 0) or 0))
        except ValueError:
            vol = 0
        side = row.get("side", "")
        if side == "BUY":
            bought[code] = bought.get(code, 0) + vol
            if (row.get("time") or "").startswith(today):
                locked_codes.add(code)
        elif side == "SELL":
            sold[code] = sold.get(code, 0) + vol
    pos, volumes, sellable = {}, {}, {}
    for c, b in bought.items():
        net = b - sold.get(c, 0)
        if net > 0:
            pos[c] = 0.0
            volumes[c] = net
            sellable[c] = 0 if c in locked_codes else net
    return pos, volumes, sellable


def _connect():
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    if trader.connect() != 0:
        raise RuntimeError("连接 miniQMT 失败")
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    time.sleep(3)
    return trader, account


def _fetch_price(code):
    try:
        from xtquant import xtdata
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(0.3)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick:
            return float(tick.get("lastPrice", 0))
    except Exception as e:
        print(f"    !! 取价失败 {code}: {e}")
    return None


def _sell(trader, account, code, vol, price, remark="planA_mature"):
    from xtquant import xtconstant
    price_type = xtconstant.LATEST_PRICE if C.AUTO_SELL_PRICE_TYPE == "LATEST" else xtconstant.FIX_PRICE
    px = 0.0 if price_type == xtconstant.LATEST_PRICE else round(price * 0.995, 2)
    return trader.order_stock(account, code, xtconstant.STOCK_SELL, vol, price_type, px,
                              "traework_rebalance", remark)


def _buy(trader, account, code, vol, price):
    from xtquant import xtconstant
    price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
    return trader.order_stock(account, code, xtconstant.STOCK_BUY, vol, price_type, price,
                              "traework_rebalance", "planA_new_top")


def _mark_unresolved_price(guard, result, fill_px, px_src, ref_price, side, code, sink):
    """成交价留痕判定（T-20261002-001），本文件三处下单点共用。

    只有「记账价不是真实成交均价」（px_src != "fill_avg"）才算异常：落待补价队列 + 进告警列表。
    成交是既成事实，成交量照记；成交价缺失则 0 仅为待补标记，下游不得据此结算盈亏。
    """
    if px_src == "fill_avg":
        return
    guard.record_pending_fill(code, side, int(result.get("traded_vol", 0) or 0),
                              result.get("order_id", 0), ref_price,
                              note=f"rebalance_daily:{px_src}",
                              order_ids=result.get("order_ids"))
    sink.append(f"{code}({result.get('traded_vol', 0)}股/{px_src}@{fill_px})")


def _alert_selection_problem(date_str, sel_path, loader, live_mode):
    """T-20261009-004：候选清单缺失/不可读 → 飞书告警（best-effort，绝不影响交易路径）。

    背景（2026-10-09 事故）：当日清单缺失时本脚本静默走空仓（marker executed_live=true
    表面"成功"，candidate_loader=none 藏在 json 里无人看）→ V1.x 无候选买入一整天，
    盘后审计才发现。T-20261003-001 的「缺失不 return、卖出照常」语义不变（卖出风控
    优先级高于买入优化），本函数只补「第一时间人工可见」这一层。
    """
    problem = "缺失" if loader == "none" else "读取失败"
    mode = "LIVE" if live_mode else "DRY-RUN"
    msg = (f"[P16 V1.x 候选断流告警] {date_str} {mode} 清单{problem}: {os.path.basename(sel_path)}\n"
           f"本日【不买入】，卖出风控照常执行。"
           f"请核查 09:45 review_full 是否跑成、data/tdx_review.json 的 date 是否为今日。")
    print("!! " + msg.replace("\n", " | "))
    try:
        from qmt_bridge_client import notify_feishu
        notify_feishu(msg)
    except Exception as e:
        print(f"    (飞书告警发送失败，仅本地日志: {e})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_str(), help="清单日期 YYYYMMDD（默认今天）")
    ap.add_argument("--top", type=int, default=TOP_N,
                    help="T档或目标持仓数：0=清仓 / 1=半仓持1只 / >=2=按TOP_N满仓（上游09:45/10:05传T档，脚本内部归一)")
    ap.add_argument("--hold-days", type=int, default=HOLD_DAYS, help="持有期满交易日数（到期制卖出，替代 PK_OUT 日频翻转）")
    ap.add_argument("--live", action="store_true", help="真实换仓（默认 dry-run）")
    args = ap.parse_args()

    # --- --top 归一化（2026-09-22 修复，T-20260922-003）---
    # 上游（09:45 开盘实时复核任务 + 10:05 rebalance_daily_guard.ps1）把 --top 当「T 档」传：
    #   T=0 → --top 0（大盘破位清仓）、T=1 → --top 1（半仓持1只）、T=2 → --top 2（常态满仓）。
    # 原实现直接把 args.top 当「目标持仓数」用（选股只数 L266 + 持仓名额 L379）。
    # 当 TOP_N=2 时 T=2 传 --top 2 恰好等于持仓数，无碍；TOP_N 上调到 5（2026-09-21 拍板）后，
    # 09-22 实测 T=2 仍传 --top 2 → 选股只取 top2、且已持 2 只时新增名额 slots=0 → 换仓下达 0 单。
    # 修复：非半仓/清仓场景（args.top>=2）一律归一到 TOP_N 作「目标持仓数」，args.top 仅保留部署档位语义。
    eff_top = args.top if args.top < 2 else TOP_N

    sel_path = os.path.join(C.TRADE_LOG.rsplit("qmt_trade_log.csv", 1)[0], "selections", f"{args.date}_selection_full.csv")
    # ---- 候选溯源（2026-09-28 缺陷B修复①+②）：单次读盘 + 读取点即时 SHA256 + asof 留档 ----
    # 保证 sha/内容/留档与实际解析三者同源（R2），事后清单被覆写也能复现实盘输入。
    cand_meta = _candidate_meta(args.date, sel_path, "exact")
    _sel_text = None
    if not os.path.exists(sel_path):
        cand_meta["candidate_loader"] = "none"
        print(f"!! 找不到当日清单: {sel_path}\n   先运行 review_full 生成完整版清单")
    else:
        try:
            with open(sel_path, "rb") as f:
                _raw_sel = f.read()
            cand_meta["candidate_sha256"] = hashlib.sha256(_raw_sel).hexdigest()
            _archive_candidate_asof(args.date, cand_meta, _raw_sel)
            _sel_text = _raw_sel.decode("utf-8-sig")
            cand_meta["candidate_rows_raw"] = sum(1 for _ in csv.DictReader(io.StringIO(_sel_text)))
        except Exception as e:
            cand_meta["candidate_loader"] = "read_error"
            _sel_text = None
            print(f"!! 当日清单读取失败: {e}")
    if _sel_text is None:
        # T-20261009-004：清单缺失/读失败不再静默——飞书告警 + degraded 落档。
        # （2026-10-09 事故：缺失静默空仓一整天无人知晓；卖出照常的 T-20261003-001 语义不变。）
        _alert_selection_problem(args.date, sel_path, cand_meta.get("candidate_loader", "none"), args.live)
        cand_meta["degraded"] = ("selection_missing" if cand_meta.get("candidate_loader") == "none"
                                 else "selection_read_error")
        # T-20261003-001 修复（DE 审计 P1）：清单缺失/读取失败不再整段 return。
        # 旧写法的 early return 压在「持仓读取→成熟度→sell_plan」之上，MATURE/T0
        # 卖出被整体抑制，违反 AGENTS 硬红线「卖出风控优先级高于买入优化」——与
        # T-20261002-001（红线空仓只禁买）同语义：无清单 → target 空 → 下方
        # `if not target:` 走 buy_allowed=False（T≥1 不买）或 T=0 清仓分支，卖出照常。
        cand_meta["candidate_rows_filtered"] = 0   # load_selection 未跑，防 plan 落盘 KeyError
        target = []
    else:
        target = load_selection(sel_path, eff_top, text=_sel_text, meta=cand_meta)
    # T-20261002-001 修复（AGENTS 硬红线「卖出风控优先级高于买入优化」）：
    # 买入拦截（红线空仓）只应禁止【买入】，绝不能短路【卖出】。
    # 旧写法在此直接 return → 无候选达标时 sell_plan 永不生成，到期持仓被无限期滞留。
    # 实测事故 2026-09-29：10 只候选全未过 58 红线 → 买入侧 return →
    #   603049.SH（超期 9 日）、603237.SH（超期 8 日）本应按 MATURE 卖出却零委托。
    # 正确语义：buy_allowed=False 时继续往下走持仓/成熟度判定，卖出照常执行，
    #   仅把买入条数压到 0（见下方 buy_allowed 闸门）。
    buy_allowed = True
    if not target:
        if args.top == 0:
            # T-20260904-003 修复：T=0（沪深300≤-1.5% 大盘破位）→ 清仓信号，
            # 全部策略持仓卖出 + 0 条买入（原逻辑 target 空直接 return → 破位日不清仓）。
            print("!! T=0 大盘破位：清仓信号，全部策略持仓卖出（T-20260904-003 修复）")
        else:
            # T-20261002-001：不再 return，改置 buy_allowed=False，卖出通道保留。
            buy_allowed = False
            print(f"!! 当日清单过红线(>{REDLINE:.0f})的票为 0：本日【不买入】（宁缺毋滥），"
                  f"但卖出风控照常执行（T-20261002-001）")

    # 策略持仓定义：只认成交记录里 BUY 过的代码（账户历史持仓不归本脚本管，由清仓任务负责）
    strategy_codes = set()
    for row in C.load_trade_log_rows():
        if row.get("side") == "BUY" and row.get("code"):
            strategy_codes.add(row["code"].strip())

    # 持仓（QMT 优先 ∩ 策略持仓；QMT 失败回退日志）
    positions, volumes, sellable = load_positions_qmt()
    src = "QMT"
    if positions:
        positions = {c: v for c, v in positions.items() if c in strategy_codes}
        volumes = {c: volumes[c] for c in positions}
        sellable = {c: sellable[c] for c in positions}
        if not positions:
            src = "QMT(无策略持仓)"
    if not positions:
        positions, volumes, sellable = load_positions_log()
        src = "成交记录(估算)"
    if not positions:
        print("!! 无策略持仓（QMT 与成交记录均无），本日只做买入计划")
        positions, volumes, sellable = {}, {}, {}

    target_codes = {t["code"] for t in target}
    force_liquidate = args.top == 0   # T-20260904-003：T=0 清仓信号，全部卖出
    sell_plan = []  # 到期/调出卖出（含 reason 字段）
    hold_plan = []
    locked = []
    today_d = _parse_date(args.date)
    last_buy = _build_last_buy_dates()
    for code, cost in positions.items():
        # ---- ST 风险警示强制剔除（T-20261012-001，诚哥拍板 2026-10-12）----
        # ST 持仓不等持有期、不进 target，下个换仓日强制卖出（特征快照无 is_st 列
        # 致候选链盲区）；T+1 锁定则顺延次日（st_guard 每日重判）。
        _st_skip, _st_why = PF.st_guard(code)
        if _st_skip:
            v = sellable.get(code, 0)
            if v > 0:
                sell_plan.append({"code": code, "cost": cost, "vol": v, "reason": "ST_FORCE_EXIT"})
                print(f"    !! [ST剔除] {code} 强制卖出 {v} 股（不等持有期）: {_st_why}")
            else:
                locked.append(code)
                print(f"    !! [ST剔除] {code} {_st_why} 但 T+1 锁定可卖 0，次日换仓再卖")
            continue
        if not force_liquidate and code in target_codes:
            hold_plan.append(code)          # 仍在 top2 → 继续持有（到期也续期，避免同日卖买抖动）
            continue
        # 到期制：掉出 top2 但持仓未满 hold_days 交易日 → 保留，不再日频翻转（修复 PK_OUT train-serving skew, T-20260904-001）
        ent = last_buy.get(code)
        held = _trading_days_between(ent, today_d) if ent else 999
        if not force_liquidate and held < args.hold_days:
            hold_plan.append(code)
            continue
        v = sellable.get(code, 0)
        if v > 0:
            sell_plan.append({"code": code, "cost": cost, "vol": v,
                              "reason": "T0_LIQUIDATE" if force_liquidate else "MATURE"})
        else:
            locked.append(code)  # T+1 锁定，今天不能卖
    # 等权目标数 = 实际目标数（含已持有的 target）
    n_target = max(len(target), 1)

    # 大盘风控部署比例（WORKFLOW_DEPLOY.md 九 / 2026-08-31 实盘确认）：
    #   T=2 常态满仓：资金池×98%（T-20261003-006）；T=1 降半仓：资金池×50%（只持 1 只，金额半仓）；
    #   T=0 空仓由调用方以 --top 0 处理（本脚本 top=0 时 target 为空即不买）。
    deploy_pct = 0.50 if args.top == 1 else (1 - C.RESERVE_CASH_PCT)

    # 策略资金池（初始10万 + 已实现盈亏 + 策略持仓浮盈）；买入预算一律以此为准，绝不用账户全量资金
    capital = load_strategy_capital()
    target_value = capital * deploy_pct / n_target  # 每只 target 目标市值

    # 对每只 target 计算目标股数与当前持有股数的差（按目标股数，而非市值差额）：
    #   目标股数 = 目标市值 ÷ 现价（向下取整到整手），避免市值差额取整后仍超配；
    #   不足 → 补仓到目标股数；超配 → 减仓到目标股数（受今日可卖限制）。
    buy_orders = []   # 需买入
    trim_orders = []  # 需减仓（超配回目标）
    for t in target:
        code = t["code"]
        price = _fetch_price(code)
        if not price or price <= 0:
            print(f"    !! {code} 取价失败，跳过该票调整")
            continue
        held_vol = volumes.get(code, 0)
        held_value = held_vol * price
        target_vol = int(target_value / price / 100) * 100  # 目标股数（整手向下取整，不超目标市值）
        diff = held_vol - target_vol
        if abs(diff) < C.MIN_ORDER_VOL:
            buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                               "price": price, "total": t["total"], "action": "达标不动"})
            continue
        if diff < 0:
            # 不足 → 补仓到目标股数
            vol = -diff
            if vol >= C.MIN_ORDER_VOL:
                # 标签修正（2026-09-14 P3）：held_vol=0 是新晋建仓，不是补仓；仅动标签，不改逻辑
                buy_orders.append({"code": code, "vol": vol, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "新晋" if held_vol <= 0 else "补仓"})
            else:
                buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "新晋不足一手" if held_vol <= 0 else "补仓不足一手"})
        else:
            # 超配 → 减仓到目标股数（受今日可卖限制）
            trim_vol = min(diff, sellable.get(code, 0))
            if trim_vol >= C.MIN_ORDER_VOL:
                trim_orders.append({"code": code, "vol": trim_vol, "held_vol": held_vol, "held_value": held_value,
                                    "price": price, "total": t["total"], "action": "减仓超配"})
            else:
                buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "超配但T+1锁定/不足一手"})

    # ---- T-20260907-003 修复：持仓数上限（对齐回测 simulate「持仓始终 ≤ TOP」）----
    # 背景：09-07 到期制（T-20260904-001）首个实盘日，非 target 持仓（300413/003005）未满 N=5 被保留，
    #       买入侧无持仓数上限 + P0 校验不含存量市值 → 又买 601999/601579 → 4 只、总占用≈资金池 1.84 倍。
    # 语义：买入名额 = TOP − 卖出后仍持有的票数；补仓（已持有票，不增加持仓数）不受名额限制；
    #       新增买入（held_vol=0，会叠加持仓数）按 total 降序取前 slots 只。
    sell_codes = {s["code"] for s in sell_plan}
    n_after_sell = len([c for c in positions if c not in sell_codes])
    slots = max(0, eff_top - n_after_sell)
    _new_buys = [b for b in buy_orders if b.get("vol") and (b.get("held_vol") or 0) <= 0]
    _topups = [b for b in buy_orders if b.get("vol") and (b.get("held_vol") or 0) > 0]
    if len(_new_buys) > slots:
        print(f"    [T-20260907-003] 卖出后仍持 {n_after_sell} 只 ≥ 目标 {eff_top}，"
              f"新增买入名额仅 {slots} 只 → 仅保留最高分 {slots} 只（防超买叠加，原计划 {len(_new_buys)} 只新增）")
    _new_buys.sort(key=lambda b: -(b.get("total") or 0))
    exec_buys = _topups + _new_buys[:slots]

    # ---- T-20261002-001 修复：买入拦截闸（红线空仓只禁买，不禁卖）----
    # 与下方降级闸同语义范式：把买入条数压到 0，卖出通道（sell_plan / trim_orders）完全不动。
    # 放在 slots 计算【之后】，确保 new_buys/trims 已算完再压，避免污染 n_after_sell 语义。
    if not buy_allowed:
        exec_buys = []
        print("    !! [T-20261002-001] 买入拦截（红线空仓）：本日不买入，"
              "但到期/调出卖出照常执行 —— 卖出风控优先级高于买入优化")

    # ---- 纸面自动降级闸（2026-09-13 立，T-20260913-001 P1-8）：冻结时只卖不买 ----
    frozen = False
    try:
        import paper_forward_downgrade as _pf
        frozen = _pf.is_frozen("V1.3")
    except Exception:
        pass
    if frozen:
        exec_buys = []
        print("    !! [降级闸] V1.3 纸面前向样本外为负，冻结加仓（只卖不买）——到期/调出卖出照常，QMT 内置风控照常")

    print("=" * 64)
    print(f"[方案A 换仓计划·等权对齐] {args.date} | 清单: {os.path.basename(sel_path)}")
    print(f"  持仓来源: {src} | 当前持仓 {len(positions)} 只 | 目标 top{n_target}: {[t['code'] for t in target]}")
    print(f"  策略资金池 ≈ {capital:,.0f} 元（初始10万+盈亏滚动，不使用账户全量资金）")
    print(f"  每只目标市值 ≈ {target_value:,.0f} 元 = 资金池×{deploy_pct:.0%}÷{n_target}")
    print(f"  {'卖出(到期/调出)':<16}{'数量':<8}{'动作'}")
    for s in sell_plan:
        print(f"  {s['code']:<14}{s['vol']:<8}卖出")
    for c in locked:
        print(f"  {c:<14}{'':<8}⚠️ T+1 锁定今日不卖")
    print(f"  {'目标调整':<14}{'当前→目标':<22}{'动作'}")
    for o in exec_buys:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    for o in trim_orders:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    print("=" * 64)

    if not args.live:
        print("DRY-RUN：未产生委托。确认后加 --live 执行真实换仓。")
        # 落盘计划（dry-run 写旁路 .dryrun.json，防覆盖当日 live 执行记录/幂等标记，DE 兜底 P2）
        plan = {"date": args.date, "target": target,
                "strategy_capital": round(capital, 2), "target_value_each": round(target_value, 2),
                "tier": args.top, "eff_top": eff_top,  # tier=T档位(0/1/2)；eff_top=归一后实际持仓上限
                "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
                "trim": trim_orders, "hold": hold_plan, "locked": locked,
                # ---- 候选溯源（2026-09-28 修复①+②）----
                "selection_file": sel_path,             # 修回 09-01/0902 曾有的溯源字段（DE 排查 F5）
                "candidate_file": cand_meta["candidate_file"],
                "candidate_loader": cand_meta["candidate_loader"],
                "candidate_data_date": cand_meta["candidate_data_date"],
                "candidate_rows": cand_meta["candidate_rows_filtered"],
                "candidate_rows_raw": cand_meta["candidate_rows_raw"],
                "candidate_sha256": cand_meta["candidate_sha256"],
                "candidate_asof": cand_meta["candidate_asof"],
                "degraded": cand_meta.get("degraded", "")}   # T-20261009-004：selection_missing/read_error 断流留痕
        out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.dryrun.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
        print("    计划已保存（DRY-RUN 旁路）:", out)
        return

    # ---- LIVE 执行：先卖后买（等权对齐，资金池为基准，委托守护重试）----
    # LIVE 互斥锁（T-20261008-005）：TRAE 主任务 / guard 兜底 / agent 重试多入口
    # 并发 --live 会双卖双买（2026-10-08 事故 4 进程并发）。抢锁失败直接放弃本次执行。
    global _ACTIVE_LIVE_LOCK
    lock_path = _acquire_live_lock()
    if not lock_path:
        print("!! [LIVE锁] 获取失败，本次 LIVE 换仓放弃（防并发双卖/双买）")
        return
    _ACTIVE_LIVE_LOCK = lock_path
    import order_guard
    from xtquant import xtconstant
    trader, account = _connect()
    asset = trader.query_stock_asset(account)
    cash = float(asset.cash) if asset else 0.0
    print(f"  [LIVE] 账户可用资金 {cash:,.2f} | 策略资金池 {capital:,.0f} 元 | 每只目标市值 ≈ {target_value:,.0f} 元")
    log_rows = []
    guard_note = []  # 委托守护结果汇总
    unresolved = []  # T-20261002-001：已成交但成交价未确认的记录（需人工补价）

    # 1) 卖出到期/调出持仓（委托守护：未成撤单重试，涨跌停跳过）
    for s in sell_plan:
        price = _fetch_price(s["code"])
        if not price or price <= 0:
            print(f"    !! {s['code']} 取价失败，跳过卖出")
            continue
        reason = s.get("reason", "MATURE")
        if reason == "ST_FORCE_EXIT":
            remark = "planA_st_exit"  # T-20261012-001：ST 强制剔除卖出，独立 remark 便于对账
        elif reason == "MATURE":
            remark = "planA_mature"
        else:
            remark = "planA_pk_out"
        r = order_guard.order_with_guard(trader, account, s["code"], "SELL", s["vol"], price, remark)
        print(f"    卖出 {s['code']} {s['vol']}股 [{reason}] -> {r['note']}")
        guard_note.append(f"卖{s['code']}:{r['action']}(重试{r.get('attempts',1)}次)")
        # T-20260930-001 修复：记账门槛只看实际成交量——ok=False 不代表零成交
        # （LIMIT_SKIP/CANCELED_TIMEOUT 可带前几轮真实成交），原 `ok and traded_vol` 会漏记账本
        if r["traded_vol"] > 0:
            # T-20261002-001：记账价优先真实成交均价，回退参考价（禁止用 0 记账）
            fill_px, px_src = order_guard.resolve_fill_price(r, price, s["code"])
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL",
                             r["traded_vol"], fill_px, reason, r["order_id"]])
            _book_fill_row(log_rows[-1])  # T-20261008-007：逐笔即时落账（防挂死丢账）
            _mark_unresolved_price(order_guard, r, fill_px, px_src, price,
                                   "SELL", s["code"], unresolved)

    # 2) 减仓超配 target（先卖，回笼资金；委托守护）
    for t in trim_orders:
        r = order_guard.order_with_guard(trader, account, t["code"], "SELL", t["vol"], t["price"], "planA_trim")
        print(f"    减仓(超配) {t['code']} {t['vol']}股 -> {r['note']}")
        guard_note.append(f"减{t['code']}:{r['action']}(重试{r.get('attempts',1)}次)")
        # T-20260930-001 修复：记账门槛只看实际成交量——ok=False 不代表零成交
        # （LIMIT_SKIP/CANCELED_TIMEOUT 可带前几轮真实成交），原 `ok and traded_vol` 会漏记账本
        if r["traded_vol"] > 0:
            # T-20261002-001：记账价优先真实成交均价，回退参考价（禁止用 0 记账）
            fill_px, px_src = order_guard.resolve_fill_price(r, t["price"], t["code"])
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), t["code"], "SELL",
                             r["traded_vol"], fill_px, "TRIM_OVR", r["order_id"]])
            _book_fill_row(log_rows[-1])  # T-20261008-007：逐笔即时落账（防挂死丢账）
            _mark_unresolved_price(order_guard, r, fill_px, px_src, t["price"],
                                   "SELL", t["code"], unresolved)

    # ---- P0 资金池硬校验（2026-08-27 补强）：买入预算一律以策略资金池为硬上限 ----
    # 教训：8/24 bug1 用账户全量可用资金(asset.cash)买入 300684 939万，远超 10 万资金池。
    # 此处从策略资金池推导买入总额上限与单票上限，下单前逐笔校验，超限直接拒绝+报警（fail-loud）。
    POOL_BUY_CAP = capital * deploy_pct                     # 当日买入总额硬上限 = 资金池×部署比例（T=1 半仓 50%）
    SINGLE_CAP = target_value                               # 单票硬上限 = 资金池×部署比例÷目标数
    if not (POOL_BUY_CAP > 0 and SINGLE_CAP > 0):
        print("    !! [P0-资金池校验] 资金池<=0，拒绝全部买入（防超买）")
        POOL_BUY_CAP = SINGLE_CAP = 0.0
    placed_buy_total = 0.0  # 已放置买入金额累计
    print(f"    [P0-资金池校验] 当日买入上限 {POOL_BUY_CAP:,.0f} 元 | 单票上限 {SINGLE_CAP:,.0f} 元")

    # T-20260907-003 资金存量校验：保留持仓市值 = 卖出计划外持仓 vol × 现价（现价失败用成本兜底）。
    # 与持仓数上限互为第二道保险：即使名额逻辑漏放行，总占用（保留市值+本次买入）也不得突破资金池×deploy_pct。
    retain_value = 0.0
    for _c in positions:
        if _c in sell_codes:
            continue
        _px = _fetch_price(_c)
        if not _px or _px <= 0:
            _px = positions.get(_c) or 0.0
        retain_value += (volumes.get(_c, 0) or 0) * _px
    if retain_value > 0:
        print(f"    [T-20260907-003] 卖出后保留持仓市值 ≈ {retain_value:,.0f} 元（纳入资金池校验）")

    # 3) 补仓/买入 target（委托守护；资金池硬校验 + 持仓数上限 + 实际可用资金，防超买）
    for b in exec_buys:
        if not b.get("vol"):
            continue
        asset2 = trader.query_stock_asset(account)
        cash_now = float(asset2.cash) if asset2 else cash
        max_vol = int(cash_now / (b["price"] * 100)) * 100
        vol = min(b["vol"], max_vol)
        if vol < C.MIN_ORDER_VOL:
            print(f"    !! {b['code']} 可用资金不足一手({C.MIN_ORDER_VOL}股)，跳过")
            continue
        # ---- 低开校验（V1.4，T4）：极端低开(<=-5%)跳过；低开(-5~-3%)需放量承接才放行 ----
        # 与 G2（T3）同规则：003005 型（低开-10%跌停接刀）→ 跳过；601999 型（低开-4.9%放量翻红）→ 放行
        _ohlc = PF.fetch_ohlc(b["code"])
        # ---- ST 风险警示买入拦截（T-20261012-001）：主库快照 + 腾讯实时名称双层 ----
        # name 直用 fetch_ohlc 已取的实时简称（含 ST 前缀），免二次请求；两层皆失 fail-open。
        _st_skip, _st_why = PF.st_guard(b["code"], name=(_ohlc or {}).get("name"))
        if _st_skip:
            print(f"    !! {b['code']} ST 拦截: {_st_why}")
            guard_note.append(f"买{b['code']}:ST_BLOCK")
            continue
        if _ohlc:
            _vr = PF.gap_volume_ratio(b["code"], _ohlc["vol_lot"])
            _skip, _why = PF.gap_guard(b["code"], b["price"], _ohlc["pre_close"], _ohlc["open"], _vr)
            if _skip:
                print(f"    !! {b['code']} 低开拦截: {_why}")
                guard_note.append(f"买{b['code']}:GAP_BLOCK")
                continue
        order_amt = vol * b["price"]
        # P0 硬校验 1：单笔买入额不得超过单票上限（防单票独吞资金池）
        if order_amt > SINGLE_CAP * 1.02:
            print(f"    !! [P0-资金池校验] {b['code']} 单笔 {order_amt:,.0f} > 单票上限 {SINGLE_CAP:,.0f}，拒绝（防超买）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        # P0 硬校验 2：当日累计买入额不得超过资金池上限
        if placed_buy_total + order_amt > POOL_BUY_CAP * 1.02:
            print(f"    !! [P0-资金池校验] 累计买入 {placed_buy_total + order_amt:,.0f} > 资金池上限 {POOL_BUY_CAP:,.0f}，拒绝（防超买）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        # T-20260907-003 校验 3：总占用（保留持仓市值 + 已买 + 本单）不得超过资金池×deploy_pct
        # 修复 09-07 超买：卖出未发生时（到期制保留持仓），原校验只算"当日新增"导致突破本金
        if retain_value + placed_buy_total + order_amt > POOL_BUY_CAP * 1.02:
            print(f"    !! [T-20260907-003-存量校验] 保留持仓 {retain_value:,.0f} + 已买 {placed_buy_total:,.0f} + 本单 {order_amt:,.0f} "
                  f"> 资金池上限 {POOL_BUY_CAP:,.0f}，拒绝（防突破本金）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        placed_buy_total += order_amt
        r = order_guard.order_with_guard(trader, account, b["code"], "BUY", vol, b["price"], "planA_new_top")
        print(f"    买入 {b['code']} {vol}股 -> {r['note']}")
        guard_note.append(f"买{b['code']}:{r['action']}(重试{r.get('attempts',1)}次)")
        # T-20260930-001 修复：记账门槛只看实际成交量——ok=False 不代表零成交
        # （LIMIT_SKIP/CANCELED_TIMEOUT 可带前几轮真实成交），原 `ok and traded_vol` 会漏记账本
        if r["traded_vol"] > 0:
            # T-20261002-001：记账价优先真实成交均价，回退参考价（禁止用 0 记账）
            fill_px, px_src = order_guard.resolve_fill_price(r, b["price"], b["code"])
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), b["code"], "BUY",
                             r["traded_vol"], fill_px, b["total"], r["order_id"]])
            _book_fill_row(log_rows[-1])  # T-20261008-007：逐笔即时落账（防挂死丢账）
            _mark_unresolved_price(order_guard, r, fill_px, px_src, b["price"],
                                   "BUY", b["code"], unresolved)

    if guard_note:
        print("  [委托守护] " + " | ".join(guard_note))
    if unresolved:
        print("  [!! 成交价未确认] 以下记录已成交但成交价非真实成交均价"
              "（已落待补价队列 data/qmt_pending_fills.jsonl，需人工核对成交回报后补价；"
              "0 价仅作待补标记，不得据此结算盈亏）：" + " | ".join(unresolved))

    if log_rows:
        # T-20261008-007：成交行已在三处下单点逐笔即时落账，此处仅汇总（不再统一补写）
        print(f"    成交记录 {len(log_rows)} 笔已逐笔落账 -> {C.TRADE_LOG}")

    # 保存执行结果（9:45 任务 step4 要求 + 10:05 Windows 兜底任务的幂等标记，T-20260903-002）
    plan = {
        "date": args.date,
        "executed_live": True,
        "executed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier": args.top,          # T 档位（0=清仓/1=半仓/2=满仓），非持仓只数
        "eff_top": eff_top,        # 归一后实际持仓只数上限（T>=2 时=TOP_N，T-20260922-003 修复）
        "target": target,
        "strategy_capital": round(capital, 2),
        "target_value_each": round(target_value, 2),
        "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
        "trim": trim_orders, "hold": hold_plan, "locked": locked,
        "guard_note": guard_note,
        # ---- 候选溯源（2026-09-28 修复①+②）----
        "selection_file": sel_path,             # 修回 09-01/0902 曾有的溯源字段（DE 排查 F5）
        "candidate_file": cand_meta["candidate_file"],
        "candidate_loader": cand_meta["candidate_loader"],
        "candidate_data_date": cand_meta["candidate_data_date"],
        "candidate_rows": cand_meta["candidate_rows_filtered"],
        "candidate_rows_raw": cand_meta["candidate_rows_raw"],
        "candidate_sha256": cand_meta["candidate_sha256"],
        "candidate_asof": cand_meta["candidate_asof"],
        "degraded": cand_meta.get("degraded", ""),   # T-20261009-004：selection_missing/read_error 断流留痕
    }
    out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
    print("    执行结果已保存:", out)
    trader.stop()


if __name__ == "__main__":
    # T-20261008-006：硬化退出——xtquant 非守护线程会让解释器退出阶段永久挂起
    # （2026-10-08 Run D 实测挂死 8 小时），统一在此 flush + 释放锁 + os._exit。
    _exit_code = 0
    try:
        main()
    except SystemExit as _e:
        try:
            _exit_code = int(_e.code)
        except (TypeError, ValueError):
            _exit_code = 0 if not _e.code else 1
    except BaseException:
        import traceback
        traceback.print_exc()
        _exit_code = 1
    finally:
        _release_live_lock(_ACTIVE_LIVE_LOCK)
        _hard_exit(_exit_code)
