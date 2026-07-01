# debug_rec.py - Diagnose stock recommendation pipeline
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from app import fetch_all_a_stocks, score_stock

stocks = fetch_all_a_stocks()
print("Total fetched:", len(stocks))

# Print first 5 raw stocks
for s in stocks[:5]:
    print("  %s %s pe=%s pb=%s roe=%s mcap=%s amount=%s turnover_rate=%s price=%s" %
          (s.get('code','?'), s.get('name','?'),
           s.get('pe'), s.get('pb'), s.get('roe'),
           s.get('market_cap'), s.get('amount'),
           s.get('turnover_rate'), s.get('price')))

long_ok = 0
long_alt = 0
short_ok = 0
short_alt = 0
sample_scores = []

for s in stocks:
    ls = score_stock(s, 'long')
    ss = score_stock(s, 'short')
    lt = ls.get('total', 0)
    st = ss.get('total', 0)

    if len(sample_scores) < 10:
        sample_scores.append((s.get('code','?'), lt, st, ls, ss))

    # Long-term criteria
    pe_ok = s.get('pe') is not None and s.get('pe') > 0
    pb_ok = s.get('pb') is not None and s.get('pb') > 0
    mcap_ok = s.get('market_cap') is not None and s.get('market_cap') > 2000000000
    roe_ok = s.get('roe') is not None and s.get('roe') > 0
    lf = ls.get('factors', {})
    if lt >= 36 and pe_ok and pb_ok and mcap_ok and roe_ok:
        long_ok += 1
    elif lt >= 25 and lf.get('fundamental',0) >= 5 and lf.get('valuation',0) >= 3 and pe_ok and pb_ok and mcap_ok and roe_ok:
        long_alt += 1

    # Short-term criteria
    amt_ok = s.get('amount') is not None and s.get('amount') > 100000000
    to_ok = s.get('turnover_rate') is not None and s.get('turnover_rate') > 0
    pr_ok = s.get('price') is not None and s.get('price') > 0
    sf = ss.get('factors', {})
    if st >= 32 and amt_ok and to_ok and pr_ok:
        short_ok += 1
    elif st >= 25 and sf.get('trend',0) >= 3 and sf.get('fund',0) >= 8 and amt_ok and to_ok and pr_ok:
        short_alt += 1

print("\nLong-term: main=%d alt=%d" % (long_ok, long_alt))
print("Short-term: main=%d alt=%d" % (short_ok, short_alt))
print("Total long=%d short=%d" % (long_ok + long_alt, short_ok + short_alt))

print("\nSample scores (first 10):")
for code, lt, st, ls, ss in sample_scores:
    lf = ls.get('factors', {})
    sf = ss.get('factors', {})
    print("  %s: long=%.1f (fund=%.1f val=%.1f timing=%.1f) short=%.1f (trend=%.1f fund=%.1f timing=%.1f)" %
          (code, lt, lf.get('fundamental',0), lf.get('valuation',0), lf.get('timing',0),
           st, sf.get('trend',0), sf.get('fund',0), sf.get('timing',0)))