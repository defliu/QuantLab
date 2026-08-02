# coding=utf-8
"""Check what financial fields the strategy needs"""
import re
import pandas as pd

# Check strategy file
with open('E:/QuantLab/scripts/strategy_mfic_sim.py', 'r', encoding='utf-8') as f:
    content = f.read()

fin_refs = re.findall(r'fd\.get\("(\w+)"\)', content)
print("Strategy needs these financial fields:")
for field in sorted(set(fin_refs)):
    print("  -", field)

# Check CSV columns
csv = pd.read_csv('D:/QMT_POOL/mfic_fin_data.csv', encoding='gbk', nrows=5)
print("\nCSV current columns:")
for c in csv.columns:
    print("  -", c)

print("\n--- Summary ---")
needed = set(fin_refs)
has = set(csv.columns)
missing = needed - has
extra = has - needed
if missing:
    print("Missing from CSV:", missing)
if extra:
    print("Extra in CSV:", extra)
if not missing:
    print("CSV has all required fields!")
