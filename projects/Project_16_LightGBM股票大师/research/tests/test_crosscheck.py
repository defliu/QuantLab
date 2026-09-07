# coding=utf-8
"""交叉验证机制单元测试（T-20260831-005）。

覆盖 review_full.py 的：
  - _read_crosscheck(date)   读 9:25 写的数据源交叉验证文件
  - _crosscheck_warns(cc, code)  汇总某候选不一致指标预警

运行（需 numpy/pandas 环境，miniqmt venv）：
  cd D:/QuantLab/projects/Project_16_LightGBM股票大师
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_crosscheck.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache")
sys.path.insert(0, PROJECT_ROOT)

import review_full  # noqa: E402

PASS = 0
FAIL = 0


def _check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def _make_cc():
    """典型 crosscheck dict：一只 F2 不一致、一只行情+板块不一致、一只全一致。"""
    return {
        "300456.SZ": {
            "fund_flow": {"ok": False, "sources": {"wudao": -125000000, "mx": None},
                          "note": "两源方向不一致或缺失"},
            "quote": {"ok": True, "sources": {"tencent": 40.26, "sina": 40.26}},
            "sector": {"ok": True},
        },
        "600522.SH": {
            "fund_flow": {"ok": True, "sources": {"wudao": 1000000, "mx": 900000}},
            "quote": {"ok": False, "sources": {"tencent": 15.0, "sina": 15.8},
                      "note": "最新价差>1%"},
            "sector": {"ok": False, "note": "涨幅差>1pp"},
        },
        "000001.SZ": {
            "fund_flow": {"ok": True, "sources": {"wudao": 500000, "mx": 510000}},
            "quote": {"ok": True, "sources": {"tencent": 10.0, "sina": 10.02}},
            "sector": {"ok": True},
        },
    }


def _write_cc(date, cc, corrupt=False):
    """向 data/cache 写入测试 crosscheck 文件，返回路径。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "crosscheck_%s.json" % date)
    if corrupt:
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json!!")
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date, "stocks": cc}, f, ensure_ascii=False)
    return path


def test_read_crosscheck():
    print("[1] _read_crosscheck")
    # 1a 文件不存在 -> None
    _check("不存在日期返回 None", review_full._read_crosscheck("20990101") is None)
    # 1b 正常文件 -> 返回 stocks dict
    p = _write_cc("20260901", _make_cc())
    cc = review_full._read_crosscheck("20260901")
    _check("正常文件返回 dict", isinstance(cc, dict) and "300456.SZ" in cc)
    _check("返回 stocks 而非整文件", "stocks" not in cc)
    os.remove(p)
    # 1c 损坏文件 -> None
    p = _write_cc("20260902", {}, corrupt=True)
    _check("损坏文件返回 None", review_full._read_crosscheck("20260902") is None)
    os.remove(p)


def test_crosscheck_warns():
    print("[2] _crosscheck_warns")
    cc = _make_cc()
    # 2a 候选不在 cc -> []
    _check("未知候选返回空", review_full._crosscheck_warns(cc, "999999.SZ") == [])
    # 2b cc=None -> []
    _check("cc=None 返回空", review_full._crosscheck_warns(None, "300456.SZ") == [])
    # 2c F2 资金不一致
    w = review_full._crosscheck_warns(cc, "300456.SZ")
    _check("F2资金不一致预警 1 条", len(w) == 1)
    _check("预警含 F2资金 前缀", w and w[0].startswith("F2资金:"))
    _check("预警含 note 信息", w and "两源方向不一致" in w[0])
    # 2d 行情 + 板块双不一致
    w = review_full._crosscheck_warns(cc, "600522.SH")
    _check("行情+板块双预警 2 条", len(w) == 2)
    _check("含 行情 预警", any(x.startswith("行情:") for x in w))
    _check("含 F5板块 预警", any(x.startswith("F5板块:") for x in w))
    # 2e 全一致 -> []
    _check("全一致返回空", review_full._crosscheck_warns(cc, "000001.SZ") == [])


def test_crosscheck_warns_missing_sub():
    print("[3] _crosscheck_warns 容错")
    # 3a 指标子项缺失（None）-> 不报错、返回 []
    cc = {"300456.SZ": {"fund_flow": None, "quote": {}, "sector": {"ok": True}}}
    _check("缺失子项容错返回空", review_full._crosscheck_warns(cc, "300456.SZ") == [])
    # 3b 候选存在但全空 dict
    cc = {"300456.SZ": {}}
    _check("空 dict 返回空", review_full._crosscheck_warns(cc, "300456.SZ") == [])


def test_crosscheck_warns_order():
    print("[4] _crosscheck_warns 顺序")
    cc = {"X.SZ": {"fund_flow": {"ok": False, "note": "a"}, "quote": {"ok": False, "note": "b"}, "sector": {"ok": False, "note": "c"}}}
    w = review_full._crosscheck_warns(cc, "X.SZ")
    _check("预警顺序 F2资金→行情→F5板块", w == ["F2资金:a", "行情:b", "F5板块:c"])


if __name__ == "__main__":
    test_read_crosscheck()
    test_crosscheck_warns()
    test_crosscheck_warns_missing_sub()
    test_crosscheck_warns_order()
    print("\n结果: %d PASS, %d FAIL" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
