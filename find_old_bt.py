import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'C:\Users\Administrator\Desktop\stock_viewer\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if 'backtest-section' in l.lower() and 'recommend' not in l.lower() and '<div class="backtest-section">' in l:
        print(f"L{i+1}: {l.rstrip()[:150]}")
    if 'backtest-panel' in l.lower() and 'id="backtestPanel"' in l:
        print(f"L{i+1}: {l.rstrip()[:150]}")
    if 'backtest-quick-btns' in l.lower():
        print(f"L{i+1}: {l.rstrip()[:150]}")