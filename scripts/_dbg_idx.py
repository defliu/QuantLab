import pandas as pd, pyarrow.parquet as pq
D = r"D:\astock\finance"
E = r"E:\temp\行情数据"
tabs = ["balancesheet","cashflow","income","dividend","express","fina_indicator","forecast","share_change_event"]
tot=0
for t in tabs:
    dp = D+"\\"+t+".parquet"; ep = E+"\\"+t+".parquet"
    dnames = set(pq.read_schema(dp).names)
    enames = set(pq.read_schema(ep).names)
    d_only = sorted(dnames - enames); e_only = sorted(enames - dnames)
    tot += len(d_only)
    print("%-22s D=%3d E=%3d  D_only=%2d  E_only=%s" % (t, len(dnames), len(enames), len(d_only), e_only))
    if d_only:
        print("      D_only_cols = %s" % d_only)
print("TOTAL D_only =", tot)
print()
print("--- pandas 读出来对比（fina_indicator）---")
d = pd.read_parquet(D+r"\fina_indicator.parquet")
e = pd.read_parquet(E+r"\fina_indicator.parquet")
print("D pandas cols=%d  E pandas cols=%d" % (len(d.columns), len(e.columns)))
print("D index.names=%s E index.names=%s" % (list(d.index.names), list(e.index.names)))
dc=set(d.columns); ec=set(e.columns)
print("pandas D_only=%d: %s" % (len(dc-ec), sorted(dc-ec)[:10]))
print("pandas E_only=%d: %s" % (len(ec-dc), sorted(ec-dc)[:10]))
