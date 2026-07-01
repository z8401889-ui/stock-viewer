#!/usr/bin/env python3
"""Apply all recommendation logic upgrades to app.py"""

from pathlib import Path

APP = Path(__file__).resolve().parent / "app.py"
orig = APP.read_text(encoding="utf-8")

# ---- Patch 1: Add SMI and CCI calculation functions before "def _indicator(prices):" ----
patch1_marker = "\n\ndef _indicator(prices):"
new_funcs = """
def _smi(highs, lows, closes, p_k=14, p_d=3, p_smooth=3):
    \"\"\"Stochastic Momentum Index - measures momentum relative to midpoint\"\"\"
    n = len(closes)
    if n < p_k + p_d:
        return [None] * n, [None] * n
    mid = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    smi_val = [None] * n
    # Compute raw SMI: (close - midpoint_of_range) / (range/2) * 100
    for i in range(p_k - 1, n):
        hh = max(mid[i - p_k + 1:i + 1])
        ll = min(mid[i - p_k + 1:i + 1])
        rng = hh - ll
        if rng == 0:
            smi_val[i] = 0
        else:
            smi_val[i] = ((mid[i] - (hh + ll) / 2) / (rng / 2)) * 100
    # Smooth with EMA
    smi_s = _sma(smi_val, p_smooth)
    # Signal line = SMA of smoothed SMI
    sig = _sma(smi_s, p_d)
    return smi_s, sig


def _cci(highs, lows, closes, period=20):
    \"\"\"Commodity Channel Index - measures deviation from typical price\"\"\"
    n = len(closes)
    if n < period:
        return [None] * n
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    cci = [None] * n
    for i in range(period - 1, n):
        sma_tp = sum(tp[i - period + 1:i + 1]) / period
        md = sum(abs(t - sma_tp) for t in tp[i - period + 1:i + 1]) / period
        if md == 0:
            cci[i] = 0
        else:
            cci[i] = (tp[i] - sma_tp) / (0.015 * md)
    return cci

"""
orig = orig.replace(patch1_marker, new_funcs + "\n\ndef _indicator(prices):", 1)
print("Patch 1: Added SMI/CCI functions")

# ---- Patch 2: Add fields to new_fdata return dict ----
patch2_marker = 'return {\n        "pe": pe,'
new_fdata = '''return {
        "pe": pe,
        "dividend_yield": round(dividend_yield, 4) if dividend_yield is not None else None,
        "pb": round(pb, 4) if pb is not None else None,
        "roe": round(roe, 4) if roe is not None else None,
        "debt_ratio": round(debt_ratio, 4) if debt_ratio is not None else None,
        "cashflow_ratio": round(cashflow_ratio, 4) if cashflow_ratio is not None else None,
        "peg": round(peg, 4) if peg is not None else None,'''
orig = orig.replace(patch2_marker, new_fdata, 1)
print("Patch 2: Added new fdata fields")

# ---- Patch 3: Add parse for ROE, Debt Ratio, Cashflow Ratio, PEG in new_fdata ----
patch3_marker = 'dividend_yield = _safe_float(d.get("DIVIDENDYIELD"))'
new_parse1 = '''dividend_yield = _safe_float(d.get("DIVIDENDYIELD"))
    roe = _safe_float(d.get("ROE"))
    debt_ratio = _safe_float(d.get("DEBTRATIO"))
    cashflow_ratio = _safe_float(d.get("CASHFLOWRATIO"))
    peg = _safe_float(d.get("PEG"))'''
orig = orig.replace(patch3_marker, new_parse1, 1)
print("Patch 3: Added ROE/debt/cashflow/PEG parsing")

# ---- Patch 4: Calculate CCI/SMI in calc_indicators ----
patch4_marker = "boll_up, boll_mid, boll_low, boll_bw = _bollinger(closes, 20, 2)"
new_bollinger = "boll_up, boll_mid, boll_low, boll_bw = _bollinger(closes, 20, 2)\n    smi_s_arr, smi_sig_arr = _smi(highs, lows, closes, 14, 3, 3)\n    cci_arr = _cci(highs, lows, closes, 20)"
orig = orig.replace(patch4_marker, new_bollinger, 1)
print("Patch 4: Added SMI/CCI calculation")

# ---- Patch 5: Read SMI/CCI last values ----
patch5_marker = "kj_j = _last(j_arr)"
new_kj_read = "kj_j = _last(j_arr)\n    smi_s = _last(smi_s_arr)\n    smi_sig = _last(smi_sig_arr)\n    cci = _last(cci_arr)"
orig = orig.replace(patch5_marker, new_kj_read, 1)
print("Patch 5: Read SMI/CCI last values")

# ---- Patch 6: Add SMI/CCI to returned dict ----
patch6_marker = '"bb_mid": round(bb_mid, 2) if bb_mid else None,'
new_bb_line = '"bb_mid": round(bb_mid, 2) if bb_mid else None,\n        "bb_width": round(bb_bw, 2) if bb_bw else None,\n        "smi_s": round(smi_s, 2) if smi_s is not None else None,\n        "smi_sig": round(smi_sig, 2) if smi_sig is not None else None,\n        "cci": round(cci, 2) if cci is not None else None,'
orig = orig.replace(patch6_marker, new_bb_line, 1)
print("Patch 6: Added SMI/CCI to indicator dict")

# ---- Patch 7: Add new field variables in score_stock() ----
patch7_marker = 'ma200 = ind.get("ma200")'
new_score_vars = 'ma200 = ind.get("ma200")\n    smi_s = ind.get("smi_s")\n    smi_sig = ind.get("smi_sig")\n    pb = fdata.get("pb")\n    roe = fdata.get("roe")\n    debt_ratio = fdata.get("debt_ratio")\n    cashflow_ratio = fdata.get("cashflow_ratio")\n    peg = fdata.get("peg")\n    bvps = fdata.get("bvps")'
orig = orig.replace(patch7_marker, new_score_vars, 1)
print("Patch 7: Added new vars in score_stock")

# ---- Patch 8: Add fundamental factor (growth + debt + cashflow) after industry_score ----
patch8_marker = "score += industry_score"
fundamental_block = """score += industry_score

    # === Fundamental quality factor (contributes 0~35 points) ===
    fund_score = 0
    fund_detail = []

    # Revenue/Profit Growth (0~10)
    if growth_3y is not None and growth_3y > 15:
        fund_score += 10
        fund_detail.append("growth_3y>15%")
    elif growth_3y is not None and growth_3y > 5:
        fund_score += 5
        fund_detail.append("growth_3y>5%")

    # ROE (0~10)
    if roe is not None and roe > 15:
        fund_score += 10
        fund_detail.append("ROE>15%")
    elif roe is not None and roe > 5:
        fund_score += 5
        fund_detail.append("ROE>5%")
    elif roe is not None and roe < 0:
        fund_score -= 5
        fund_detail.append("ROE<0%")

    # Debt ratio (0~8)
    if debt_ratio is not None and debt_ratio < 30:
        fund_score += 8
        fund_detail.append("debt<30%")
    elif debt_ratio is not None and debt_ratio < 60:
        fund_score += 3
        fund_detail.append("debt<60%")
    elif debt_ratio is not None and debt_ratio > 80:
        fund_score -= 5
        fund_detail.append("debt>80%")

    # Cashflow ratio (0~7)
    if cashflow_ratio is not None and cashflow_ratio > 1.5:
        fund_score += 7
        fund_detail.append("cf_ratio>1.5")
    elif cashflow_ratio is not None and cashflow_ratio > 1.0:
        fund_score += 4
        fund_detail.append("cf_ratio>1.0")
    elif cashflow_ratio is not None and cashflow_ratio < 0.5:
        fund_score -= 3
        fund_detail.append("cf_ratio<0.5")

    score += max(min(fund_score, 35), -10)
    if fund_detail:
        details.append("fund(%s:+%d)" % (",".join(fund_detail), max(min(fund_score, 35), -10)))"""
orig = orig.replace(patch8_marker, fundamental_block, 1)
print("Patch 8: Added fundamental quality factor")

# ---- Patch 9: Add valuation factor (BVPS/PB/PEG/Earnings Yield) ----
patch9_marker = 'details.append("reversal(high5d>35):+15")'
val_block = """
    # === Valuation factor (contributes 0~25 points) ===
    val_score = 0
    val_detail = []

    # BVPS / PB evaluation
    if bvps is not None and pb is not None and bvps > 0 and pb > 0:
        if pb < 1.0:
            val_score += 10
            val_detail.append("PB<1.0")
        elif pb < 2.0:
            val_score += 6
            val_detail.append("PB<2.0")
        elif pb < 4.0:
            val_score += 3
            val_detail.append("PB<4.0")
        elif pb > 10:
            val_score -= 3
            val_detail.append("PB>10")

    # PEG evaluation
    if peg is not None:
        if peg < 0.8:
            val_score += 8
            val_detail.append("PEG<0.8")
        elif peg < 1.5:
            val_score += 4
            val_detail.append("PEG<1.5")
        elif peg > 3:
            val_score -= 3
            val_detail.append("PEG>3")

    # Earnings yield evaluation
    if pe is not None and pe > 0:
        ey = 100 / pe
        if ey > 8:
            val_score += 7
            val_detail.append("EY>8%")
        elif ey > 5:
            val_score += 3
            val_detail.append("EY>5%")
        elif ey < 2:
            val_score -= 3
            val_detail.append("EY<2%")

    score += max(min(val_score, 25), -8)
    if val_detail:
        details.append("value(%s:+%d)" % (",".join(val_detail), max(min(val_score, 25), -8)))"""
orig = orig.replace(patch9_marker + "\n", patch9_marker + "\n" + val_block, 1)
print("Patch 9: Added valuation factor")

# ---- Patch 10: Remove old PE valuation block ----
old_pe_block = '    # PE 估值评价 (0~10)\n    if pe is not None and pe != 0:\n        if pe < 15:\n            score += 10\n            details.append("PE<15:+10")\n        elif pe < 25:\n            score += 6\n            details.append("PE<25:+6")\n'
if old_pe_block in orig:
    orig = orig.replace(old_pe_block, "", 1)
    print("Patch 10: Removed old PE valuation block")
else:
    print("Patch 10: Old PE block not found (may already be removed)")

# ---- Patch 11: Upgrade risk factor ----
patch11_marker = '# 风险因子 (最近10天信号矛盾减分)\n    if atr is not None:\n        risk_score = _risk_factor(closes, 10)\n        score += risk_score\n        if risk_score < 0:\n            details.append(f"risk(conflict:{risk_score})")'
new_risk = '# 风险因子 (最近10天信号矛盾减分 + PEG高估减分)\n    if atr is not None:\n        risk_score = _risk_factor(closes, 10)\n        if peg is not None and peg > 4:\n            risk_score -= 4\n            details.append("risk(PEG>4:-4)")\n        if debt_ratio is not None and debt_ratio > 80:\n            risk_score -= 4\n            details.append("risk(debt>80%:-4)")\n        score += risk_score\n        if risk_score < 0:\n            details.append(f"risk(conflict:{risk_score})")'
orig = orig.replace(patch11_marker, new_risk, 1)
print("Patch 11: Upgraded risk factor")

# ---- Patch 12: Long-term Phase1 screening upgrade ----
patch12_marker = '    elif period == "long" and ind is not None:\n        # 长期推荐 Phase1 筛选: 价格>MA60, 价格<MA90, 月线阳线, 月线放量\n        ma60 = ind.get("ma60")\n        close = ind.get("close")\n        vol_up = ind.get("vol_up_trend")\n        if close and ma60:\n            close_val = safe_get(close)\n            ma60_val = safe_get(ma60)\n            if close_val > ma60_val:\n                score += 8\n                details.append("Price>MA60:+8")\n        # 连续4周期月线阳线\n        if monthly_green > 3:\n            score += 8\n            details.append("monthly_green>3:+8")\n        # 月线放量\n        if vol_up:\n            score += 4\n            details.append("vol_up:+4")'
new_long_phase1 = '    elif period == "long" and ind is not None:\n        # 长期推荐 Phase1 筛选: 价格>MA60, 月线阳线, 月线放量, MACD金叉, KDJ金叉\n        ma60 = ind.get("ma60")\n        close = ind.get("close")\n        vol_up = ind.get("vol_up_trend")\n        macd_dif = ind.get("macd_dif")\n        macd_dea = ind.get("macd_dea")\n        kdj_k = ind.get("kdj_k")\n        kdj_d = ind.get("kdj_d")\n        cci_val = ind.get("cci")\n        smi_s_val = ind.get("smi_s")\n        smi_sig_val = ind.get("smi_sig")\n\n        # 价格>MA60 多头排列\n        if close and ma60:\n            close_val = safe_get(close)\n            ma60_val = safe_get(ma60)\n            if close_val > ma60_val:\n                score += 8\n                details.append("Price>MA60:+8")\n        # 连续4周期月线阳线\n        if monthly_green > 3:\n            score += 8\n            details.append("monthly_green>3:+8")\n        # 月线放量\n        if vol_up:\n            score += 4\n            details.append("vol_up:+4")\n        # MACD金叉 (DIF上穿DEA)\n        if macd_dif is not None and macd_dea is not None:\n            if macd_dif > macd_dea:\n                score += 5\n                details.append("MACD_golden:+5")\n        # KDJ金叉 (K上穿D)\n        if kdj_k is not None and kdj_d is not None:\n            if kdj_k > kdj_d:\n                score += 4\n                details.append("KDJ_golden:+4")\n        # CCI > -50 (脱离超卖区)\n        if cci_val is not None and cci_val > -50:\n            score += 4\n            details.append("CCI>-50:+4")\n        elif cci_val is not None and cci_val < -100:\n            score -= 4\n            details.append("CCI<-100:-4")\n        # SMI金叉\n        if smi_s_val is not None and smi_sig_val is not None:\n            if smi_s_val > smi_sig_val:\n                score += 4\n                details.append("SMI_golden:+4")'
orig = orig.replace(patch12_marker, new_long_phase1, 1)
print("Patch 12: Upgraded long-term Phase1 screening")

# ---- Patch 13: Short-term Phase1 screening upgrade ----
patch13_marker = '    elif period == "short" and ind is not None:\n        # 短期推荐 Phase1 筛选: 放量阳线 (vol>prev*1.2, close>open), 未远离MA10, \n        # 高换手率\n        vol = ind.get("vol")\n        vol_prev = ind.get("prev_vol")\n        close = ind.get("close")\n        open_p = ind.get("open")\n        ma10 = ind.get("ma10")\n        turnover = ind.get("turnover_rate")\n        if vol and vol_prev and close and open_p:\n            close_val = safe_get(close)\n            open_val = safe_get(open_p)\n            if vol > vol_prev * 1.2 and close_val > open_val:\n                score += 10\n                details.append("Vol_up+Yang:+10")\n        if close and ma10:\n            close_val2 = safe_get(close)\n            ma10_val = safe_get(ma10)\n            if close_val2 <= ma10_val * 1.05:\n                score += 6\n                details.append("NearMA10:+6")\n        if turnover and turnover > 5:\n            score += 4\n            details.append("HighTurnover:+4")'
new_short_phase1 = '    elif period == "short" and ind is not None:\n        # 短期推荐 Phase1 筛选: 放量阳线 (vol>prev*1.2, close>open), 未远离MA5/MA10,\n        # 高换手率, MACD金叉, RSI 40~70, CCI>0, KDJ金叉\n        vol = ind.get("vol")\n        vol_prev = ind.get("prev_vol")\n        close = ind.get("close")\n        open_p = ind.get("open")\n        ma5_val = ind.get("ma5")\n        ma10 = ind.get("ma10")\n        turnover = ind.get("turnover_rate")\n        rsi_val = ind.get("rsi")\n        macd_dif = ind.get("macd_dif")\n        macd_dea = ind.get("macd_dea")\n        kdj_k = ind.get("kdj_k")\n        kdj_d = ind.get("kdj_d")\n        cci_val = ind.get("cci")\n        smi_s_val = ind.get("smi_s")\n        smi_sig_val = ind.get("smi_sig")\n\n        # 放量阳线\n        if vol and vol_prev and close and open_p:\n            close_val = safe_get(close)\n            open_val = safe_get(open_p)\n            if vol > vol_prev * 1.2 and close_val > open_val:\n                score += 10\n                details.append("Vol_up+Yang:+10")\n        # 未远离MA5/MA10 (乖离率<5%)\n        if close and ma10:\n            close_val2 = safe_get(close)\n            ma10_val = safe_get(ma10)\n            if close_val2 <= ma10_val * 1.05:\n                score += 6\n                details.append("NearMA10:+6")\n        if close and ma5_val:\n            close_val3 = safe_get(close)\n            ma5_val_n = safe_get(ma5_val)\n            if close_val3 <= ma5_val_n * 1.03:\n                score += 3\n                details.append("NearMA5:+3")\n        # 高换手率\n        if turnover and turnover > 5:\n            score += 4\n            details.append("HighTurnover:+4")\n        # MACD金叉\n        if macd_dif is not None and macd_dea is not None:\n            if macd_dif > macd_dea:\n                score += 4\n                details.append("MACD_golden_s:+4")\n        # RSI在40~70区间\n        if rsi_val is not None:\n            if 40 <= rsi_val <= 70:\n                score += 4\n                details.append("RSI_40-70:+4")\n            elif rsi_val > 80:\n                score -= 3\n                details.append("RSI>80:-3")\n        # CCI > 0 (强势)\n        if cci_val is not None and cci_val > 0:\n            score += 3\n            details.append("CCI>0:+3")\n        # KDJ金叉\n        if kdj_k is not None and kdj_d is not None:\n            if kdj_k > kdj_d:\n                score += 4\n                details.append("KDJ_golden_s:+4")\n        # SMI金叉\n        if smi_s_val is not None and smi_sig_val is not None:\n            if smi_s_val > smi_sig_val:\n                score += 3\n                details.append("SMI_golden_s:+3")'
orig = orig.replace(patch13_marker, new_short_phase1, 1)
print("Patch 13: Upgraded short-term Phase1 screening")

# ---- Patch 14: Build long-term sort key ----
patch14_marker = '            if p == "long":\n                # 长期排序 = score + (ma60乖离率=价格贴近MA60加分) + 月线连续阳线加分\n                close_v = safe_get(ind.get("close"))\n                ma60_v = safe_get(ind.get("ma60"))\n                if close_v and ma60_v and ma60_v > 0:\n                    ma60_bias = (close_v - ma60_v) / ma60_v * 100\n                else:\n                    ma60_bias = 0'
new_long_sort = '            if p == "long":\n                # 长期排序 = score + 估值因子 + 技术趋势因子\n                # 估值因子：低PB + 低PEG + 高ROE\n                val_extra = 0\n                if pb_val is not None and pb_val > 0 and pb_val < 2:\n                    val_extra += 5\n                elif pb_val is not None and pb_val > 0 and pb_val < 4:\n                    val_extra += 2\n                if peg_val is not None and peg_val > 0 and peg_val < 1:\n                    val_extra += 5\n                elif peg_val is not None and peg_val > 0 and peg_val < 2:\n                    val_extra += 2\n                if roe_val is not None and roe_val > 15:\n                    val_extra += 4\n                # 技术趋势：MACD金叉 + CCI强势 + SMI金叉\n                trend_extra = 0\n                macd_dif_v = safe_get(ind.get("macd_dif"))\n                macd_dea_v = safe_get(ind.get("macd_dea"))\n                if macd_dif_v is not None and macd_dea_v is not None and macd_dif_v > macd_dea_v:\n                    trend_extra += 2\n                cci_sort = safe_get(ind.get("cci"))\n                if cci_sort is not None and cci_sort > 0:\n                    trend_extra += 2\n                smi_s_sort = safe_get(ind.get("smi_s"))\n                smi_sig_sort = safe_get(ind.get("smi_sig"))\n                if smi_s_sort is not None and smi_sig_sort is not None and smi_s_sort > smi_sig_sort:\n                    trend_extra += 1\n                close_v = safe_get(ind.get("close"))\n                ma60_v = safe_get(ind.get("ma60"))\n                if close_v and ma60_v and ma60_v > 0:\n                    ma60_bias = (close_v - ma60_v) / ma60_v * 100\n                else:\n                    ma60_bias = 0'
orig = orig.replace(patch14_marker, new_long_sort, 1)
print("Patch 14: Upgraded long-term sort key")

# ---- Patch 15: Update long-term final sort calculation ----
patch15_marker = 'final_long = score + monthly_green + (10 - abs(ma60_bias)) * 0.5'
new_long_final = 'final_long = score + monthly_green + (10 - abs(ma60_bias)) * 0.5 + val_extra + trend_extra'
orig = orig.replace(patch15_marker, new_long_final, 1)
print("Patch 15: Updated long-term final sort")

# ---- Patch 16: Build short-term sort key ----
patch16_marker = '            elif p == "short":\n                # 短期排序 = score + 放量加分 + 趋势强度\n                vol_extra = 0\n                vol_cur = safe_get(ind.get("vol"))\n                vol_pre = safe_get(ind.get("prev_vol"))\n                if vol_cur and vol_pre and vol_pre > 0:\n                    vol_extra = max(0, min(10, (vol_cur / vol_pre - 1) * 20))\n                trend = 0\n                macd_dif = safe_get(ind.get("macd_dif"))\n                macd_hist = safe_get(ind.get("macd_hist"))\n                if macd_dif and macd_dif > 0:\n                    trend += 3\n                if macd_hist and macd_hist > 0:\n                    trend += 3\n                final = score + vol_extra + trend'
new_short_sort = '            elif p == "short":\n                # 短期排序 = score + 放量加分 + 趋势强度 + CCI/RSI/SMI动量\n                vol_extra = 0\n                vol_cur = safe_get(ind.get("vol"))\n                vol_pre = safe_get(ind.get("prev_vol"))\n                if vol_cur and vol_pre and vol_pre > 0:\n                    vol_extra = max(0, min(10, (vol_cur / vol_pre - 1) * 20))\n                # 趋势强度\n                trend = 0\n                macd_dif_vs = safe_get(ind.get("macd_dif"))\n                macd_hist_vs = safe_get(ind.get("macd_hist"))\n                if macd_dif_vs and macd_dif_vs > 0:\n                    trend += 3\n                if macd_hist_vs and macd_hist_vs > 0:\n                    trend += 3\n                # CCI动量: >0 强势\n                cci_mom = safe_get(ind.get("cci"))\n                if cci_mom is not None and cci_mom > 0:\n                    trend += 2\n                elif cci_mom is not None and cci_mom < -50:\n                    trend -= 2\n                # RSI动量: 40~70最佳\n                rsi_mom = safe_get(ind.get("rsi"))\n                if rsi_mom is not None and 40 <= rsi_mom <= 70:\n                    trend += 2\n                elif rsi_mom is not None and rsi_mom > 80:\n                    trend -= 1\n                # SMI金叉\n                smi_s_vs = safe_get(ind.get("smi_s"))\n                smi_sig_vs = safe_get(ind.get("smi_sig"))\n                if smi_s_vs is not None and smi_sig_vs is not None and smi_s_vs > smi_sig_vs:\n                    trend += 2\n                final = score + vol_extra + trend'
orig = orig.replace(patch16_marker, new_short_sort, 1)
print("Patch 16: Upgraded short-term sort key")

# ---- Patch 17: Add fdata variable extraction for sort (pb/peg/roe) ----
patch17_marker = "            # 提取价格用于计算涨跌幅"
new_sort_extract = '            # 提取估值/财务数据用于排序\n            pb_val = safe_get(fdata.get("pb")) if fdata else None\n            peg_val = safe_get(fdata.get("peg")) if fdata else None\n            roe_val = safe_get(fdata.get("roe")) if fdata else None\n\n            # 提取价格用于计算涨跌幅'
orig = orig.replace(patch17_marker, new_sort_extract, 1)
print("Patch 17: Added fdata extraction for sort")

# ---- Write back ----
APP.write_text(orig, encoding="utf-8")
print("\nAll 17 patches applied successfully!")
print(f"Final file size: {len(orig)} chars")
print(f"Final line count: {orig.count(chr(10))} lines")