# coding=utf-8
"""Check factor library structure"""
import os
import re

# Check alpha101
alpha101_dir = 'E:/QuantLab/backtest/factors/zoo/alpha101'
files = [f for f in os.listdir(alpha101_dir) if f.endswith('.py') and not f.startswith('__')]
print("alpha101: %d files" % len(files))

# Check first file structure
with open(os.path.join(alpha101_dir, files[0]), 'r', encoding='utf-8') as f:
    content = f.read()

alpha_id = re.search(r'ALPHA_ID\s*=\s*["\']([^"\']+)["\']', content)
has_meta = '__alpha_meta__' in content
has_compute = 'def compute(' in content

print("First file:", files[0])
print("  Has ALPHA_ID:", alpha_id is not None)
print("  Has __alpha_meta__:", has_meta)
print("  Has compute():", has_compute)
if alpha_id:
    print("  ALPHA_ID:", alpha_id.group(1))

# Check gtja191
gtja_dir = 'E:/QuantLab/backtest/factors/zoo/gtja191'
if os.path.isdir(gtja_dir):
    gtja_files = [f for f in os.listdir(gtja_dir) if f.endswith('.py') and not f.startswith('__')]
    print("\ngtja191: %d files" % len(gtja_files))
    with open(os.path.join(gtja_dir, gtja_files[0]), 'r', encoding='utf-8') as f:
        content = f.read()
    alpha_id = re.search(r'ALPHA_ID\s*=\s*["\']([^"\']+)["\']', content)
    if alpha_id:
        print("First ALPHA_ID:", alpha_id.group(1))
else:
    print("\ngtja191: NOT FOUND")

# Check registry
print("\nChecking registry...")
registry_path = 'E:/QuantLab/backtest/factors/registry.py'
if os.path.exists(registry_path):
    print("registry.py: EXISTS")
else:
    print("registry.py: NOT FOUND")
