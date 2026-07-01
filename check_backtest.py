import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'C:\Users\Administrator\Desktop\stock_viewer\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if 'backtest' in l.lower():
        print(f"{i+1}: {l.rstrip()[:140]}")