import sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'C:\Users\Administrator\Desktop\stock_viewer\app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    for kw in ['tab-', 'tabBtn', 'nav-', '\"trading\"', '\"recommend\"', '\"detail\"', 'showTab', 'tradingTab', 'decideSection', 'decide-section', '<!-- 推荐', '<!-- 详情', '<!-- 模拟', '<!-- AI', '<!-- 交易', 'Trading', 'section class', 'page-section']:
        if kw.lower() in l.lower() and len(l.strip()) > 5:
            print(f"L{i+1}: {l.rstrip()[:150]}")
            break