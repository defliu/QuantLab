# coding: utf-8
"""把 deploy_predict_g2.py 的路径常量改写后复制到 %TEMP%/g2tmp，用于候选回填验证。

任一替换匹配不到即 AssertionError（防生产脚本漂移后测试变成假绿）。
"""
import io
import os
import sys

HERE = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
T = os.path.join(os.environ["TEMP"], "g2tmp")

REPS = [
    ('import data_config as DC',
     'sys.path.insert(0, r"%s")\nimport data_config as DC' % HERE),
    ('SNAP = os.path.join(LIVE, "g2_latest_features.parquet")',
     'SNAP = os.environ["G2_VERIFY_SNAP"]'),
    ('SELECT = os.path.join(DATA, "selections", "g2")',
     'SELECT = os.path.join(os.environ["TEMP"], "g2tmp", "selections_g2")'),
    ('LIVE_LOG = os.path.join(REAL, "paper_forward_live.csv")',
     'LIVE_LOG = os.path.join(os.environ["TEMP"], "g2tmp", "paper_forward_live.csv")'),
    ('AB_LOG = os.path.join(REAL, "paper_forward_ab_v3enh.csv")',
     'AB_LOG = os.path.join(os.environ["TEMP"], "g2tmp", "paper_forward_ab_v3enh.csv")'),
    ('ENS_LOG = os.path.join(REAL, "paper_forward_ens.csv")',
     'ENS_LOG = os.path.join(os.environ["TEMP"], "g2tmp", "paper_forward_ens.csv")'),
    ('ENS_SELECT = os.path.join(DATA, "selections", "g2_ens")',
     'ENS_SELECT = os.path.join(os.environ["TEMP"], "g2tmp", "selections_g2_ens")'),
]


def main():
    src = io.open(os.path.join(HERE, "deploy_predict_g2.py"), encoding="utf-8").read()
    out = src
    for a, b in REPS:
        assert a in out, "MISS: " + a
        out = out.replace(a, b)
    os.makedirs(T, exist_ok=True)
    dst = os.path.join(T, "deploy_predict_g2_verify.py")
    io.open(dst, "w", encoding="utf-8").write(out)
    print("patched ->", dst)
    print("replacements:", len(REPS))


if __name__ == "__main__":
    sys.exit(main())
