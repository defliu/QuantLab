import json
data = json.load(open(r'D:\QuantLab\projects\Project_13_529主升浪\results\v3_1overN_scan.json', 'r', encoding='utf-8'))
n12 = [r for r in data if 'n12_h60_s12' in r['name']]
for r in n12:
    print(f'{r["name"]:35s} cagr={r["cagr"]*100:6.2f}% total={r["total_return"]*100:6.1f}% mdd={r["max_drawdown"]*100:7.2f}% sharpe={r["sharpe"]:.3f} trades={r["n_trades"]}')
