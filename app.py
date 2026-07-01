"""
股票行情浏览网页 - 新浪数据源
Flask 后端，代理新浪实时行情 API 并解析返回 JSON
推荐股票从东方财富全A股API动态计算，不再使用硬编码列表
"""
import re
import os
import json
import threading
import requests
import baostock as bs
import time as _time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# ============= 股票代码配置（制表页） =============
STOCK_LIST = [
    # 指数
    {"code": "s_sh000001", "name": "上证指数", "type": "index"},
    {"code": "s_sz399001", "name": "深证成指", "type": "index"},
    {"code": "s_sz399006", "name": "创业板指", "type": "index"},
    {"code": "s_sz399005", "name": "中小板指", "type": "index"},
    {"code": "s_sh000688", "name": "科创50", "type": "index"},
    {"code": "s_sh000300", "name": "沪深300", "type": "index"},
    # 金融
    {"code": "sh600036", "name": "招商银行", "type": "stock"},
    {"code": "sh601398", "name": "工商银行", "type": "stock"},
    {"code": "sh601318", "name": "中国平安", "type": "stock"},
    {"code": "sz000001", "name": "平安银行", "type": "stock"},
    {"code": "sh600030", "name": "中信证券", "type": "stock"},
    # 白酒消费
    {"code": "sh600519", "name": "贵州茅台", "type": "stock"},
    {"code": "sz000858", "name": "五粮液", "type": "stock"},
    {"code": "sz000568", "name": "泸州老窖", "type": "stock"},
    # 新能源
    {"code": "sz300750", "name": "宁德时代", "type": "stock"},
    {"code": "sz002594", "name": "比亚迪", "type": "stock"},
    {"code": "sh601012", "name": "隆基绿能", "type": "stock"},
    # 科技
    {"code": "sz002415", "name": "海康威视", "type": "stock"},
    {"code": "sz000725", "name": "京东方A", "type": "stock"},
    {"code": "sh688981", "name": "中芯国际", "type": "stock"},
    {"code": "sz002230", "name": "科大讯飞", "type": "stock"},
    # 医药
    {"code": "sh600276", "name": "恒瑞医药", "type": "stock"},
    {"code": "sz300760", "name": "迈瑞医疗", "type": "stock"},
    {"code": "sz000538", "name": "云南白药", "type": "stock"},
    # 地产基建
    {"code": "sh600048", "name": "保利发展", "type": "stock"},
    {"code": "sz000002", "name": "万科A", "type": "stock"},
    # 汽车
    {"code": "sh601127", "name": "赛力斯", "type": "stock"},
    {"code": "sz000625", "name": "长安汽车", "type": "stock"},
    # 其他热门
    {"code": "sz300059", "name": "东方财富", "type": "stock"},
    {"code": "sh601888", "name": "中国中免", "type": "stock"},
    {"code": "sz002475", "name": "立讯精密", "type": "stock"},
    {"code": "sh600900", "name": "长江电力", "type": "stock"},
]

# ============= 推荐股票：全市场扫描（东方财富 API） =============
EASTMONEY_ALL_STOCKS_URL = (
    "http://80.push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=6000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f2,f3,f4,f5,f6,f9,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f37,f39,f42,f45,f49,f58,f84,f85,f115,f167,f168,f177,f184,f185,f186"
)
EASTMONEY_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
RECOMMEND_CACHE = {"data": {"long_term": [], "short_term": []}, "ts": 0, "date": ""}
# 缓存策略：每天扫描一次，当天内复用缓存。可通过 ?force=1 强制重新扫描
RECOMMEND_CACHE_DAILY_KEY = ""  # 存储日期字符串，如 "2026-06-28"

# ===== AI 模拟交易状态 =====
TRADING_STATE = {
    "cash": 1_000_000.0,
    "holdings": {},
    "history": [],
    "total_invested": 0.0,
    "last_decision": "",
}
TRADING_STATE_FILE = os.path.join(os.path.dirname(__file__), "trading_state.json")


def _save_trading_state():
    try:
        s = dict(TRADING_STATE)
        s["last_decision"] = _now_str()
        with open(TRADING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_trading_state():
    try:
        if os.path.exists(TRADING_STATE_FILE):
            with open(TRADING_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            TRADING_STATE["cash"] = saved.get("cash", 1_000_000.0)
            TRADING_STATE["holdings"] = saved.get("holdings", {})
            TRADING_STATE["history"] = saved.get("history", [])
            TRADING_STATE["total_invested"] = saved.get("total_invested", 0.0)
            TRADING_STATE["last_decision"] = saved.get("last_decision", "")
    except Exception:
        pass


_load_trading_state()


def _get_today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _cache_is_valid():
    """当天已经扫描过则缓存有效（同一交易日内不重复扫描）"""
    return RECOMMEND_CACHE_DAILY_KEY == _get_today_str() and bool(RECOMMEND_CACHE["data"]["long_term"])


def _update_cache(data):
    global RECOMMEND_CACHE, RECOMMEND_CACHE_DAILY_KEY
    RECOMMEND_CACHE = {"data": data, "ts": _time.time(), "date": _get_today_str()}
    RECOMMEND_CACHE_DAILY_KEY = _get_today_str()


def _safe_raw_float(raw, default=None):
    """安全转换为 float，排除 '-' 等无效值"""
    try:
        if raw is None or raw == "-" or raw == "":
            return default
        return float(raw)
    except (ValueError, TypeError):
        return default


def fetch_all_a_stocks():
    """从东方财富拉取全部 A 股行情（一次调用），返回 list[dict]
    
    新增强化字段说明：
    - f9: 动态市盈率(dynamic PE)
    - f24: 60日涨跌幅
    - f25: 5日涨跌幅
    - f37: ROE
    - f39: 市盈率(静态)
    - f42: 市净率
    - f45: 净利润同比增长率(%)
    - f49: 每股收益
    - f58: 每股经营现金流
    - f84: 总负债率(%)
    - f85: 每股净资产
    - f115: PE(TTM)
    - f168: 换手率
    - f177: 5日换手率
    - f184: 总市值(元)
    - f185: 净利润(元)
    - f186: 营业总收入(元)
    """
    try:
        resp = requests.get(EASTMONEY_ALL_STOCKS_URL, headers=EASTMONEY_HEADERS, timeout=15)
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        stocks = []
        for item in items:
            code = item.get("f12", "")
            market = item.get("f13", 0)
            prefix = "sz" if market == 0 else "sh"
            full_code = prefix + code
            name = item.get("f14", "")

            # 基础行情
            price = _safe_raw_float(item.get("f2"))
            change = _safe_raw_float(item.get("f4"))
            change_pct = _safe_raw_float(item.get("f3"))
            volume = _safe_raw_float(item.get("f5"))
            amount = _safe_raw_float(item.get("f6"))
            high = _safe_raw_float(item.get("f15"))
            low = _safe_raw_float(item.get("f16"))
            open_ = _safe_raw_float(item.get("f17"))
            prev_close = _safe_raw_float(item.get("f18"))

            # 估值指标
            pe_dynamic = _safe_raw_float(item.get("f9"))        # 动态PE
            pe_ttm = _safe_raw_float(item.get("f115"))          # PE TTM
            pb = _safe_raw_float(item.get("f23", 0))            # PB
            if pb is not None:
                pb = pb  # 注意: 东方财富的 PB 可能也需要 /100

            # 财务质量指标
            roe = _safe_raw_float(item.get("f37"))              # ROE (%)
            eps = _safe_raw_float(item.get("f49"))              # 每股收益
            net_profit = _safe_raw_float(item.get("f185"))      # 净利润(元)

            # 市值
            market_cap = _safe_raw_float(item.get("f20"))       # 总市值
            float_market_cap = _safe_raw_float(item.get("f21")) # 流通市值

            # 动量指标
            chg_60d = _safe_raw_float(item.get("f24"))          # 60日涨跌幅
            chg_5d = _safe_raw_float(item.get("f25"))           # 5日涨跌幅

            # 换手率
            turnover_rate = _safe_raw_float(item.get("f168"))   # 换手率(%)
            turnover_5d = _safe_raw_float(item.get("f177"))     # 5日换手率

            # 新增财务质量字段
            profit_growth = _safe_raw_float(item.get("f45"))    # 净利润同比增长率(%)
            revenue_growth = _safe_raw_float(item.get("f46"))   # 营收增长率(%)
            debt_ratio = _safe_raw_float(item.get("f84"))       # 总负债率(%)
            bvps = _safe_raw_float(item.get("f85"))             # 每股净资产
            ocf_per_share = _safe_raw_float(item.get("f58"))    # 每股经营现金流
            revenue = _safe_raw_float(item.get("f186"))         # 营业总收入(元)

            stock = {
                "code": full_code,
                "name": name,
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "volume": volume,
                "amount": amount,
                "high": high,
                "low": low,
                "open": open_,
                "prev_close": prev_close,
                # 估值
                "pe_dynamic": pe_dynamic,
                "pe_ttm": pe_ttm,
                "pe": pe_ttm,  # 向后兼容：保留 pe 字段，使用 TTM PE
                "pb": pb,
                # 财务
                "roe": roe,
                "eps": eps,
                "net_profit": net_profit,
                # 市值
                "market_cap": market_cap,
                "float_market_cap": float_market_cap,
                # 动量
                "chg_60d": chg_60d,
                "chg_5d": chg_5d,
                # 换手率
                "turnover_rate": turnover_rate,
                "turnover_5d": turnover_5d,
                # 新增财务质量
                "profit_growth": profit_growth,
                "revenue_growth": revenue_growth,
                "debt_ratio": debt_ratio,
                "bvps": bvps,
                "ocf_per_share": ocf_per_share,
                "revenue": revenue,
            }
            stocks.append(stock)
        return stocks
    except Exception as e:
        print(f"[ERROR] 拉取全A股失败: {e}")
        return []


# ============= 技术指标计算函数 =============

def _sma(data, period):
    """简单移动平均"""
    if len(data) < period:
        return [None] * len(data)
    result = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(data[i - period + 1:i + 1]) / period)
    return result


def _ema(data, period):
    """指数移动平均"""
    if len(data) < period:
        return [None] * len(data)
    result = [None] * len(data)
    multiplier = 2.0 / (period + 1.0)
    result[period - 1] = sum(data[:period]) / period
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def _rsi(close_prices, period=14):
    """RSI 相对强弱指标"""
    if len(close_prices) < period + 1:
        return [None] * len(close_prices)
    gains = [0] * len(close_prices)
    losses = [0] * len(close_prices)
    rsi = [None] * period
    for i in range(1, period + 1):
        diff = close_prices[i] - close_prices[i - 1]
        if diff > 0:
            gains[i] = diff
        else:
            losses[i] = -diff
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    for i in range(period, len(close_prices)):
        diff = close_prices[i] - close_prices[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100.0 - 100.0 / (1.0 + rs))
    return rsi


def _kdj(highs, lows, closes, n=9, m1=3, m2=3):
    """KDJ 指标，返回: (k_values, d_values, j_values)"""
    length = len(closes)
    if length < n:
        return ([None] * length, [None] * length, [None] * length)
    k_vals, d_vals, j_vals = [None] * length, [None] * length, [None] * length
    prev_k, prev_d = 50.0, 50.0
    for i in range(n - 1, length):
        highest = max(highs[i - n + 1:i + 1])
        lowest = min(lows[i - n + 1:i + 1])
        rsv = ((closes[i] - lowest) / (highest - lowest)) * 100 if highest != lowest else 50.0
        prev_k = prev_k * (1 - 1.0 / m1) + rsv / m1
        prev_d = prev_d * (1 - 1.0 / m2) + prev_k / m2
        k_vals[i] = round(prev_k, 2)
        d_vals[i] = round(prev_d, 2)
        j_vals[i] = round(3 * prev_k - 2 * prev_d, 2)
    return k_vals, d_vals, j_vals


def _atr(highs, lows, closes, period=14):
    """ATR 平均真实波幅 (波动率)"""
    if len(closes) < period + 1:
        return [None] * len(closes)
    tr = [0] * len(closes)
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr_vals = [None] * period
    avg = sum(tr[1:period + 1]) / period
    atr_vals.append(round(avg, 4))
    for i in range(period + 1, len(closes)):
        avg = (avg * (period - 1) + tr[i]) / period
        atr_vals.append(round(avg, 4))
    return atr_vals


def _macd(close_prices, fast=12, slow=26, signal=9):
    """MACD 指标，返回: (dif, dea, macd_hist)"""
    ema_fast = _ema(close_prices, fast)
    ema_slow = _ema(close_prices, slow)
    dif = []
    for i in range(len(close_prices)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif.append(round(ema_fast[i] - ema_slow[i], 4))
        else:
            dif.append(None)
    valid_start = next((i for i in range(len(close_prices)) if dif[i] is not None), len(close_prices))
    valid_dif = dif[valid_start:]
    dea_ema = _ema(valid_dif, signal)
    dea = [None] * valid_start + [round(v, 4) if v is not None else None for v in dea_ema]
    macd_hist = []
    for i in range(len(close_prices)):
        if dif[i] is not None and dea[i] is not None:
            macd_hist.append(round((dif[i] - dea[i]) * 2, 4))
        else:
            macd_hist.append(None)
    return dif, dea, macd_hist


def _bollinger(close_prices, period=20, std_mult=2):
    """布林带，返回: (upper, middle, lower, bandwidth)"""
    ma = _sma(close_prices, period)
    upper, lower, bandwidth = [None] * len(close_prices), [None] * len(close_prices), [None] * len(close_prices)
    for i in range(period - 1, len(close_prices)):
        window = close_prices[i - period + 1:i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        upper[i] = round(mean + std_mult * std, 4)
        lower[i] = round(mean - std_mult * std, 4)
        if ma[i]:
            bandwidth[i] = round((upper[i] - lower[i]) / ma[i] * 100, 2)
    return upper, ma, lower, bandwidth


def _last(arr):
    """获取数组最后一个非 None 值"""
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def fetch_kline_for_score(code):
    """拉取单只股票近200日 K 线（用于技术指标计算）"""
    try:
        bs_code = code[:2] + "." + code[2:]  # sz688578 → sz.688578
        bs.login()
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )
        data_list = []
        while (rs.error_code == '0') and rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()
        if not data_list or len(data_list) < 60:
            return None
        dates, opens, highs, lows, closes, volumes, amounts = [], [], [], [], [], [], []
        for row in data_list:
            if row[1] == '' or row[2] == '' or row[4] == '':
                continue
            dates.append(row[0])
            opens.append(float(row[1]))
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
            volumes.append(float(row[5]) if row[5] != '' else 0)
            amounts.append(float(row[6]) if row[6] != '' else 0)
        if len(closes) < 60:
            return None
        return {
            "code": code, "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": closes, "volumes": volumes, "amounts": amounts,
        }
    except Exception as e:
        print(f"[KL] {code} K线拉取失败: {e}")
        return None


def _smi(highs, lows, closes, p_k=14, p_d=3, p_smooth=3):
    """Stochastic Momentum Index - measures momentum relative to midpoint"""
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
    # Smooth with SMA
    smi_s = _sma(smi_val, p_smooth)
    # Signal line = SMA of smoothed SMI
    sig = _sma(smi_s, p_d)
    return smi_s, sig


def _cci(highs, lows, closes, period=20):
    """Commodity Channel Index - measures deviation from typical price"""
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


def compute_tech_factors(kl):
    """基于K线数据计算所有技术指标因子，返回 dict
    
    趋势+动量+成交量+布林带综合评分
    """
    closes = kl["closes"]
    highs = kl["highs"]
    lows = kl["lows"]
    volumes = kl["volumes"]
    amounts = kl["amounts"]
    n = len(closes)

    ma5_arr = _sma(closes, 5)
    ma10_arr = _sma(closes, 10)
    ma20_arr = _sma(closes, 20)
    ma60_arr = _sma(closes, 60)
    dif_arr, dea_arr, macd_h = _macd(closes)
    rsi_arr = _rsi(closes, 14)
    k_arr, d_arr, j_arr = _kdj(highs, lows, closes, 9, 3, 3)
    atr_arr = _atr(highs, lows, closes, 14)
    boll_up, boll_mid, boll_low, boll_bw = _bollinger(closes, 20, 2)
    smi_s_arr, smi_sig_arr = _smi(highs, lows, closes, 14, 3, 3)
    cci_arr = _cci(highs, lows, closes, 20)

    ma5 = _last(ma5_arr)
    ma10 = _last(ma10_arr)
    ma20 = _last(ma20_arr)
    ma60 = _last(ma60_arr)
    dif = _last(dif_arr)
    dea = _last(dea_arr)
    macd_hist_val = _last(macd_h)
    rsi = _last(rsi_arr)
    kj_k = _last(k_arr)
    kj_d = _last(d_arr)
    kj_j = _last(j_arr)
    smi_s = _last(smi_s_arr)
    smi_sig = _last(smi_sig_arr)
    cci = _last(cci_arr)
    atr_now = _last(atr_arr)
    bb_up = _last(boll_up)
    bb_low = _last(boll_low)
    bb_mid = _last(boll_mid)
    bb_bw = _last(boll_bw)

    price = closes[-1]
    avg_vol_20 = sum(volumes[-21:-1]) / 20 if n >= 21 and sum(volumes[-21:-1]) > 0 else volumes[-1]
    vol_ratio = round(volumes[-1] / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
    today_amount = amounts[-1]

    # 连续放量天数
    consec_up = 0
    for i in range(n - 1, max(n - 7, 0), -1):
        if volumes[i] > volumes[i - 1] * 1.1:
            consec_up += 1
        else:
            break

    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "macd_dif": round(dif, 4) if dif else None,
        "macd_dea": round(dea, 4) if dea else None,
        "macd_hist": round(macd_hist_val, 4) if macd_hist_val is not None else None,
        "rsi": round(rsi, 1) if rsi else None,
        "kdj_k": kj_k, "kdj_d": kj_d, "kdj_j": kj_j,
        "atr": round(atr_now, 4) if atr_now else None,
        "bb_upper": round(bb_up, 2) if bb_up else None,
        "bb_lower": round(bb_low, 2) if bb_low else None,
        "bb_mid": round(bb_mid, 2) if bb_mid else None,
        "bb_width": round(bb_bw, 2) if bb_bw else None,
        "smi_s": round(smi_s, 2) if smi_s is not None else None,
        "smi_sig": round(smi_sig, 2) if smi_sig is not None else None,
        "cci": round(cci, 2) if cci is not None else None,
        "bb_bw": round(bb_bw, 2) if bb_bw else None,
        "vol_ratio": vol_ratio,
        "consec_up": consec_up,
        "price": price,
    }


def score_stock(s, mode="long"):
    """A股多因子评分体系（专业版·100分制）

    8 因子权重:
      趋势 15% | 动量 10% | 成交量 10% | 资金 20%
      基本面 15% | 估值 10% | 市场情绪 10% | 风险 10%

    mode='long'  → 估值/基本面权重上调
    mode='short' → 趋势/资金/动量权重上调

    返回: dict { total, grade, factors, tech }
    """
    try:
        price = float(s.get("price") or 0)
        chg = float(s.get("change_pct") or 0)
        amount = float(s.get("amount") or 0)
        pe = float(s.get("pe") or 0)
        pb = float(s.get("pb") or 0)
        roe = float(s.get("roe") or 0)
        eps = float(s.get("eps") or 0)
        market_cap = float(s.get("market_cap") or 0)
        chg_60d = float(s.get("chg_60d") or 0)
        chg_5d = float(s.get("chg_5d") or 0)
        turnover = float(s.get("turnover_rate") or 0)
        turnover_5d = float(s.get("turnover_5d") or 0)
    except (ValueError, TypeError):
        return {"total": 0, "grade": "D", "factors": {}, "tech": {}}

    name = s.get("name", "")
    if any(kw in name for kw in ["ST", "退", "*ST"]):
        return {"total": -100, "grade": "D", "factors": {"risk": -100}, "tech": {}}
    if price is None or price <= 0 or amount is None or amount <= 0:
        return {"total": 0, "grade": "D", "factors": {}, "tech": {}}

    tech = s.get("_tech") or {}

    # =========== 一、趋势因子 (15分) ===========
    trend_score = 0.0
    if tech:
        if tech.get("ma5") and price > tech["ma5"]:
            trend_score += 2
        if tech.get("ma10") and price > tech["ma10"]:
            trend_score += 2
        if tech.get("ma20") and price > tech["ma20"]:
            trend_score += 3
        if tech.get("ma60") and price > tech["ma60"]:
            trend_score += 4
        if tech.get("ma5") and tech.get("ma10") and tech.get("ma20"):
            if tech["ma5"] > tech["ma10"] > tech["ma20"]:
                trend_score += 4
    else:
        if 0 < chg_60d <= 25:
            trend_score += 5
        if 0 < chg_5d <= 10:
            trend_score += 3
    trend_score = min(trend_score, 15)

    # =========== 二、动量因子 (10分) ===========
    mom_score = 0.0
    if tech:
        dif = tech.get("macd_dif")
        dea = tech.get("macd_dea")
        if dif is not None and dea is not None and dif > dea:
            mom_score += 3
        rsi = tech.get("rsi")
        if rsi and 55 <= rsi <= 75:
            mom_score += 2
        if tech.get("kdj_k") and tech.get("kdj_d") and tech["kdj_k"] > tech["kdj_d"]:
            mom_score += 2
    if chg_60d > 0 and price > 0:
        max_60_adj = price / (1 + chg_60d / 100) * 1.25
        if price >= max_60_adj:
            mom_score += 2
    if not tech:
        if 1 <= chg_5d <= 10:
            mom_score += 3
        if 0 < chg <= 5:
            mom_score += 1
    mom_score = min(mom_score, 10)

    # =========== 三、成交量因子 (10分) ===========
    vol_score = 0.0
    if amount > 1e9:
        vol_score += 3
    elif amount > 5e8:
        vol_score += 2
    elif amount > 2e8:
        vol_score += 1
    if 3 <= turnover <= 15:
        vol_score += 3
    elif 1 <= turnover < 3:
        vol_score += 2
    vr = tech.get("vol_ratio")
    if vr and vr > 1.5:
        vol_score += 2
    elif turnover > 0 and turnover_5d and turnover_5d > 0:
        if turnover / turnover_5d > 1.5:
            vol_score += 2
    if tech and tech.get("consec_up", 0) >= 3:
        vol_score += 2
    vol_score = min(vol_score, 10)

    # =========== 四、资金因子 (20分) ===========
    fund_score = 0.0
    if amount > 2e9:
        fund_score += 6
    elif amount > 1e9:
        fund_score += 4
    elif amount > 5e8:
        fund_score += 2
    if 1.5 <= turnover <= 10:
        fund_score += 5
    elif 0.5 <= turnover < 1.5:
        fund_score += 2
    if 2 <= chg <= 9:
        fund_score += 4
    elif 0 < chg < 2:
        fund_score += 2
    if 3 <= chg_5d <= 15:
        fund_score += 4
        if tech and tech.get("consec_up", 0) >= 4:
            fund_score += 1
    fund_score = min(fund_score, 20)

    # =========== 五、基本面因子 (15分) ===========
    funda_score = 0.0
    if roe > 0:
        if roe > 20:
            funda_score += 3
        elif roe > 15:
            funda_score += 2
        elif roe > 8:
            funda_score += 1
    if eps and eps > 0:
        if eps > 2:
            funda_score += 3
        elif eps > 1:
            funda_score += 2
        elif eps > 0.3:
            funda_score += 1
    if market_cap > 0:
        if market_cap > 1e11:
            funda_score += 3
        elif market_cap > 5e10:
            funda_score += 2
        elif market_cap > 1e10:
            funda_score += 1
    if 5 <= chg_60d <= 30:
        funda_score += 3
    if pe <= 0 and roe <= 0:
        funda_score -= 3
    funda_score = min(funda_score, 15)

    # =========== 六、估值因子 (10分) ===========
    val_score = 2.0  # 基准分：避免全场归零
    if pe > 0:
        if pe < 10:
            val_score += 5
        elif pe < 18:
            val_score += 4
        elif pe < 25:
            val_score += 3
        elif pe < 50:
            val_score += 1
        else:
            val_score -= 1
        # 极端PE大幅扣分
        if pe > 150:
            val_score -= 3
        elif pe > 80:
            val_score -= 1
    if pb and pb > 0:
        if pb < 1:
            val_score += 3
        elif pb < 2:
            val_score += 2
        elif pb < 5:
            val_score += 1
        elif pb > 10:
            val_score -= 1
        if pb > 30:
            val_score -= 2  # 严重高估
    val_score = max(0, min(val_score, 10))

    # =========== 七、市场情绪因子 (10分) ===========
    sent_score = 0.0
    if 2 <= chg <= 5:
        sent_score += 3
    elif 0 < chg < 2:
        sent_score += 1
    if amount > 1e9 and 1 <= chg <= 6:
        sent_score += 3
    if 10 <= chg_60d <= 40:
        sent_score += 2
    if turnover > 5 and chg > 1:
        sent_score += 2
    sent_score = min(sent_score, 10)

    # =========== 八、风险因子 (10分，扣分制) ===========
    risk_score = 10.0
    if any(kw in name for kw in ["ST", "退"]):
        risk_score -= 10
    if pe and pe > 200:
        risk_score -= 4
    if pb and pb > 20:
        risk_score -= 2
    if market_cap and market_cap < 5e9:
        risk_score -= 2
    if chg_60d > 80:
        risk_score -= 4
    if chg_60d < -40:
        risk_score -= 3
    if turnover and turnover > 25:
        risk_score -= 2
    if amount < 5e7:
        risk_score -= 4
    risk_score = max(risk_score, 0)

    # =========== 九、技术择时因子 (10分，基于SMI/CCI/布林带) ===========
    timing_score = 5.0  # 基准分
    if tech:
        smi_s = tech.get("smi_s")
        cci_val = tech.get("cci")
        bb_upper = tech.get("bb_upper")
        bb_lower = tech.get("bb_lower")
        bb_mid = tech.get("bb_mid")
        rsi_val = tech.get("rsi")
        bb_bw = tech.get("bb_bw")

        if mode == "long":
            # 长期：偏好低位/超卖区域的买入时机
            if smi_s is not None and smi_s < -30:
                timing_score += 2.5  # SMI深度超卖，好买点
            elif smi_s is not None and smi_s < -10:
                timing_score += 1.5
            if cci_val is not None and cci_val < -100:
                timing_score += 2.0  # CCI超卖
            elif cci_val is not None and cci_val < -50:
                timing_score += 1.0
            if bb_lower and price and price <= bb_lower * 1.03:
                timing_score += 2.0  # 价格在布林下轨附近，低估
            elif bb_mid and price and price <= bb_mid:
                timing_score += 1.0  # 价格在中轨以下
            # 惩罚：高位追涨
            if smi_s is not None and smi_s > 40:
                timing_score -= 2.0
            if cci_val is not None and cci_val > 150:
                timing_score -= 2.0
            if bb_upper and price and price >= bb_upper:
                timing_score -= 2.5  # 上轨以上，太贵
            if rsi_val and rsi_val > 80:
                timing_score -= 2.0
        else:
            # 短期：偏好强势但不过热的动量信号
            if smi_s is not None and -25 <= smi_s <= 25:
                timing_score += 1.0  # SMI在中性区，趋势可持续
            elif smi_s is not None and 25 < smi_s <= 45:
                timing_score += 2.0  # 温和强势
            if cci_val is not None and 0 < cci_val <= 120:
                timing_score += 1.5  # CCI温和看涨
            elif cci_val is not None and 120 < cci_val <= 180:
                timing_score += 0.5  # 偏强但有回调风险
            # 布林带宽度：收窄预示突破
            if bb_bw is not None:
                if bb_bw < 3:
                    timing_score += 1.5  # 带宽极窄，蓄势待发
                elif bb_bw < 5:
                    timing_score += 0.5
            # MA排列确认
            ma5 = tech.get("ma5")
            ma10 = tech.get("ma10")
            ma20 = tech.get("ma20")
            if ma5 and ma10 and ma20 and price:
                if price > ma5 and ma5 > ma10:
                    timing_score += 1.5  # 短中期均线多头
                if ma5 > ma10 > ma20:
                    timing_score += 1.0  # 多头排列完美
            # 惩罚：极端过热
            if smi_s is not None and smi_s > 50:
                timing_score -= 2.5
            if cci_val is not None and cci_val > 200:
                timing_score -= 3.0
            if rsi_val and rsi_val > 85:
                timing_score -= 2.5
            if bb_upper and price and price >= bb_upper * 1.02:
                timing_score -= 2.0  # 突破上轨过多，有回调风险

    timing_score = max(0, min(timing_score, 10))

    # ===== 权重分配 =====
    if mode == "long":
        weights = {
            "trend": 0.10, "momentum": 0.07, "volume": 0.07, "fund": 0.14,
            "fundamental": 0.20, "valuation": 0.14, "sentiment": 0.07,
            "risk": 0.11, "timing": 0.10,
        }
    else:
        weights = {
            "trend": 0.15, "momentum": 0.12, "volume": 0.10, "fund": 0.20,
            "fundamental": 0.07, "valuation": 0.05, "sentiment": 0.10,
            "risk": 0.06, "timing": 0.15,
        }

    raw = {
        "trend": trend_score, "momentum": mom_score, "volume": vol_score,
        "fund": fund_score, "fundamental": funda_score, "valuation": val_score,
        "sentiment": sent_score, "risk": risk_score,
        "timing": round(timing_score, 1),
    }

    # 修正后的评分公式：每个因子先归一化(除以满分)，再乘以权重百分比，累加得百分制总分
    total = round(
        (raw["trend"] / 15) * weights["trend"] * 100 +
        (raw["momentum"] / 10) * weights["momentum"] * 100 +
        (raw["volume"] / 10) * weights["volume"] * 100 +
        (raw["fund"] / 20) * weights["fund"] * 100 +
        (raw["fundamental"] / 15) * weights["fundamental"] * 100 +
        (raw["valuation"] / 10) * weights["valuation"] * 100 +
        (raw["sentiment"] / 10) * weights["sentiment"] * 100 +
        (raw["risk"] / 10) * weights["risk"] * 100 +
        (raw["timing"] / 10) * weights["timing"] * 100, 1
    )

    if total >= 90:
        grade = "S"
    elif total >= 80:
        grade = "A"
    elif total >= 70:
        grade = "B"
    elif total >= 60:
        grade = "C"
    else:
        grade = "D"

    return {
        "total": total, "grade": grade, "factors": raw,
        "tech": {
            "rsi": tech.get("rsi"), "macd_dif": tech.get("macd_dif"),
            "kdj_k": tech.get("kdj_k"), "kdj_j": tech.get("kdj_j"),
            "atr": tech.get("atr"), "vol_ratio": tech.get("vol_ratio"),
            "ma5": tech.get("ma5"), "ma20": tech.get("ma20"),
            "bb_upper": tech.get("bb_upper"), "bb_lower": tech.get("bb_lower"),
            "smi_s": tech.get("smi_s"), "cci": tech.get("cci"),
            "bb_width": tech.get("bb_width"),
        }
    }


def calc_trade_prices(s):
    """计算建议买入价、卖出价、止损价"""
    try:
        price = float(s.get("price") or 0)
        pe_raw = s.get("pe")
        pb_raw = s.get("pb")
        if not price or price <= 0:
            return {"buy_price": None, "sell_price": None, "stop_loss": None}

        pe = float(pe_raw) if pe_raw and pe_raw != "-" else None
        pb = float(pb_raw) if pb_raw and pb_raw != "-" else None

        # 基于PE估值计算公允买入价
        buy_from_pe = None
        buy_from_pb = None

        if pe and pe > 0:
            eps = price / pe
            fair_pe = 15  # 公允PE
            buy_from_pe = round(eps * fair_pe, 2)

        if pb and pb > 0:
            bvps = price / pb
            fair_pb = 1.5  # 公允PB
            buy_from_pb = round(bvps * fair_pb, 2)

        # 综合买入价
        if buy_from_pe and buy_from_pb:
            buy_price = round((buy_from_pe + buy_from_pb) / 2, 2)
        elif buy_from_pe:
            buy_price = buy_from_pe
        elif buy_from_pb:
            buy_price = buy_from_pb
        else:
            buy_price = round(price * 0.95, 2)

        # 买入价不应高于当前价太多（最多当前价*1.05），也不应低于当前价*0.7
        buy_price = max(buy_price, round(price * 0.70, 2))
        buy_price = min(buy_price, round(price * 1.05, 2))

        # 卖出目标价：买入价 + 20%~30% 盈利空间
        sell_price = round(buy_price * 1.25, 2)

        # 止损价：买入价 - 7%
        stop_loss = round(buy_price * 0.93, 2)

        return {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "stop_loss": stop_loss,
            "fair_pe_based": buy_from_pe,
            "fair_pb_based": buy_from_pb,
        }
    except (ValueError, TypeError, ZeroDivisionError):
        return {"buy_price": None, "sell_price": None, "stop_loss": None}


def gen_recommend_stocks():
    """生成推荐列表：
    
    长期推荐：多因子价值评分 + 估值优先 + 盈利能力 + 大盘过滤
    短期推荐：多因子动量评分 + 换手率 + 成交额 + 趋势优先
    """
    all_stocks = fetch_all_a_stocks()
    if not all_stocks:
        return {"long_term": [], "short_term": []}

    # ===== 两阶段评分：Phase 1 全市场基本面初筛；Phase 2 top候选拉K线 =====
    for s in all_stocks:
        s["_score_long"] = score_stock(s, mode="long")
        s["_score_short"] = score_stock(s, mode="short")

    # ===== 长期推荐：价值投资导向 =====
    # Phase 1: 宽松初筛，基础基本面过滤，让更多标的进入候选池
    long_candidates = []
    for s in all_stocks:
        pe = s.get("pe")
        pb = s.get("pb")
        mcap = s.get("market_cap")
        roe = s.get("roe")
        long_total = s["_score_long"].get("total", 0)
        fund_score = s["_score_long"].get("factors", {}).get("fundamental", 0)
        val_score = s["_score_long"].get("factors", {}).get("valuation", 0)
        # 宽松初筛：只要有基本面和估值就进入候选
        if (long_total >= 18
                and pe is not None and pe > 0
                and pb is not None and pb > 0
                and mcap is not None and mcap > 1_000_000_000
                and roe is not None and roe > 0):
            long_candidates.append(s)
        elif (fund_score >= 5
                and val_score >= 2
                and pe is not None and pe > 0
                and pb is not None and pb > 0
                and mcap is not None and mcap > 1_000_000_000
                and roe is not None and roe > 0):
            long_candidates.append(s)

    long_candidates.sort(
        key=lambda x: (
            x["_score_long"].get("total", 0),
            x["_score_long"].get("factors", {}).get("fundamental", 0),
            x["_score_long"].get("factors", {}).get("valuation", 0),
            float(x.get("market_cap") or 0),
            float(x.get("roe") or 0),
        ),
        reverse=True
    )

    # Phase 2: 对长期top 50拉K线做技术指标评分（扩大候选池）
    long_top = long_candidates[:50]
    for s in long_top:
        code = s.get("code", "")
        if code:
            kl = fetch_kline_for_score(code)
            if kl:
                s["_tech"] = compute_tech_factors(kl)
                s["_score_long"] = score_stock(s, mode="long")  # 用 K 线数据重新评分

    # Phase 3: K线强化后重新过滤（降档阈值，确保K线正常后有足够推荐）
    long_final = []
    for s in long_top:
        pe = s.get("pe")
        pb = s.get("pb")
        mcap = s.get("market_cap")
        roe = s.get("roe")
        long_total = s["_score_long"].get("total", 0)
        fund_score = s["_score_long"].get("factors", {}).get("fundamental", 0)
        val_score = s["_score_long"].get("factors", {}).get("valuation", 0)
        # 主通道：总分>=36（K线正常后技术因子有分）
        if (long_total >= 36
                and pe is not None and pe > 0
                and pb is not None and pb > 0
                and mcap is not None and mcap > 1_000_000_000
                and roe is not None and roe > 0):
            long_final.append(s)
        # 备选通道：K线拉取失败的，用Phase1评分兜底
        elif (long_total >= 25
                and fund_score >= 5
                and val_score >= 3
                and pe is not None and pe > 0
                and pb is not None and pb > 0
                and mcap is not None and mcap > 1_000_000_000
                and roe is not None and roe > 0):
            long_final.append(s)

    long_final.sort(
        key=lambda x: (
            x["_score_long"].get("total", 0),
            x["_score_long"].get("factors", {}).get("fundamental", 0),
            x["_score_long"].get("factors", {}).get("valuation", 0),
            float(x.get("market_cap") or 0),
            float(x.get("roe") or 0),
        ),
        reverse=True
    )
    long_term = long_final[:12]

    # ===== 短期推荐：动量/资金导向 =====
    # Phase 1: 宽松初筛
    short_candidates = []
    for s in all_stocks:
        amount = s.get("amount")
        turnover = s.get("turnover_rate")
        price = s.get("price")
        short_total = s["_score_short"].get("total", 0)
        trend_score = s["_score_short"].get("factors", {}).get("trend", 0)
        fund_score = s["_score_short"].get("factors", {}).get("fund", 0)
        # 宽松初筛
        if (short_total >= 15
                and amount is not None and amount > 30_000_000
                and turnover is not None and turnover > 0
                and price is not None and price > 0):
            short_candidates.append(s)
        elif (turnover is not None and turnover > 0
                and price is not None and price > 0
                and amount is not None and amount > 30_000_000):
            short_candidates.append(s)

    short_candidates.sort(
        key=lambda x: (
            x["_score_short"].get("total", 0),
            x["_score_short"].get("factors", {}).get("momentum", 0),
            x["_score_short"].get("factors", {}).get("fund", 0),
            float(x.get("turnover_rate") or 0),
            float(x.get("chg_5d") or 0),
        ),
        reverse=True
    )

    # Phase 2: 对短期top 60拉K线做技术指标评分（扩大候选池）
    short_top = short_candidates[:60]
    for s in short_top:
        code = s.get("code", "")
        if code:
            kl = fetch_kline_for_score(code)
            if kl:
                s["_tech"] = compute_tech_factors(kl)
                s["_score_short"] = score_stock(s, mode="short")

    # Phase 3: K线强化后重新过滤（降档阈值）
    short_final = []
    for s in short_top:
        amount = s.get("amount")
        turnover = s.get("turnover_rate")
        price = s.get("price")
        short_total = s["_score_short"].get("total", 0)
        trend_score = s["_score_short"].get("factors", {}).get("trend", 0)
        fund_score = s["_score_short"].get("factors", {}).get("fund", 0)
        # 主通道：总分>=32
        if (short_total >= 32
                and amount is not None and amount > 50_000_000
                and turnover is not None and turnover > 0
                and price is not None and price > 0):
            short_final.append(s)
        # 备选通道：K线拉取失败的兜底
        elif (short_total >= 25
                and trend_score >= 3
                and fund_score >= 8
                and amount is not None and amount > 50_000_000
                and turnover is not None and turnover > 0
                and price is not None and price > 0):
            short_final.append(s)

    short_final.sort(
        key=lambda x: (
            x["_score_short"].get("total", 0),
            x["_score_short"].get("factors", {}).get("momentum", 0),
            x["_score_short"].get("factors", {}).get("fund", 0),
            float(x.get("turnover_rate") or 0),
            float(x.get("chg_5d") or 0),
        ),
        reverse=True
    )

    # 去重
    long_codes = {s["code"] for s in long_term}
    short_term = [s for s in short_final if s["code"] not in long_codes][:12]

    # 附加交易价格 & 评分（向后兼容）
    for s in long_term:
        s["_trade"] = calc_trade_prices(s)
        s["_score"] = s["_score_long"].get("total", 0)
    for s in short_term:
        s["_trade"] = calc_trade_prices(s)
        s["_score"] = s["_score_short"].get("total", 0)

    return {
        "long_term": long_term,
        "short_term": short_term,
    }


def fetch_sina_quotes(codes):
    """从新浪财经获取实时行情"""
    code_str = ",".join(codes)
    url = f"http://hq.sinajs.cn/list={code_str}"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "gbk"
        return resp.text
    except Exception as e:
        print(f"Fetch error: {e}")
        return ""


def parse_sina_data(raw_text):
    """解析新浪返回的原始数据，统一转为结构化字典"""
    results = []
    lines = raw_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.match(r'var\s+hq_str_(\w+)="(.*)"\s*;?$', line)
        if not match:
            continue
        code = match.group(1)
        data = match.group(2)
        if not data:
            continue
        fields = data.split(",")

        code_upper = code.upper()
        is_index = code_upper.startswith("S_")

        if is_index:
            if len(fields) < 6:
                continue
            parsed = {
                "code": code,
                "name": fields[0],
                "type": "index",
                "price": _safe_float(fields[1]),
                "change": _safe_float(fields[2]),
                "change_pct": _safe_float(fields[3]),
                "volume": _safe_int(fields[4]),
                "amount": _safe_float(fields[5]),
            }
        else:
            if len(fields) < 32:
                continue
            parsed = {
                "code": code,
                "name": fields[0],
                "type": "stock",
                "open": _safe_float(fields[1]),
                "prev_close": _safe_float(fields[2]),
                "price": _safe_float(fields[3]),
                "high": _safe_float(fields[4]),
                "low": _safe_float(fields[5]),
                "bid": _safe_float(fields[6]),
                "ask": _safe_float(fields[7]),
                "volume": _safe_int(fields[8]),
                "amount": _safe_float(fields[9]),
                "buy1_vol": _safe_int(fields[10]),
                "buy1_price": _safe_float(fields[11]),
                "buy2_vol": _safe_int(fields[12]),
                "buy2_price": _safe_float(fields[13]),
                "buy3_vol": _safe_int(fields[14]),
                "buy3_price": _safe_float(fields[15]),
                "buy4_vol": _safe_int(fields[16]),
                "buy4_price": _safe_float(fields[17]),
                "buy5_vol": _safe_int(fields[18]),
                "buy5_price": _safe_float(fields[19]),
                "sell1_vol": _safe_int(fields[20]),
                "sell1_price": _safe_float(fields[21]),
                "sell2_vol": _safe_int(fields[22]),
                "sell2_price": _safe_float(fields[23]),
                "sell3_vol": _safe_int(fields[24]),
                "sell3_price": _safe_float(fields[25]),
                "sell4_vol": _safe_int(fields[26]),
                "sell4_price": _safe_float(fields[27]),
                "sell5_vol": _safe_int(fields[28]),
                "sell5_price": _safe_float(fields[29]),
                "date": fields[30],
                "time": fields[31],
            }
            if parsed["prev_close"] and parsed["prev_close"] != 0:
                parsed["change"] = round(parsed["price"] - parsed["prev_close"], 3)
                parsed["change_pct"] = round(
                    (parsed["price"] - parsed["prev_close"]) / parsed["prev_close"] * 100, 2
                )
            else:
                parsed["change"] = 0
                parsed["change_pct"] = 0

        results.append(parsed)
    return results


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def calc_analysis_signal(item):
    """基于 EM 数据计算分析信号（item 内已含 pe/pb）"""
    score = 0
    reasons = []
    pe = item.get("pe")
    pb = item.get("pb")

    if pe is not None:
        try:
            pe = float(pe)
        except (ValueError, TypeError):
            pe = None
    if pb is not None:
        try:
            pb = float(pb)
        except (ValueError, TypeError):
            pb = None

    if pe is not None:
        if pe < 12:
            score += 2
            reasons.append(f'PE低({pe:.1f})')
        elif pe < 20:
            score += 1
        elif pe < 30:
            score += 0
        elif pe < 50:
            score -= 1
            reasons.append(f'PE偏高({pe:.1f})')
        else:
            score -= 2
            reasons.append(f'PE过高({pe:.1f})')

    if pb is not None:
        if pb < 1.2:
            score += 1
            reasons.append(f'PB低({pb:.2f})')
        elif pb < 3:
            score += 0
        else:
            score -= 1
            reasons.append(f'PB偏高({pb:.2f})')

    change_pct = item.get('change_pct', 0)
    if change_pct > 5:
        score -= 1
        reasons.append('当日大涨')
    elif change_pct < -5:
        score += 1
        reasons.append('当日大跌')

    amount = item.get('amount', 0)
    if amount > 5000000000:
        reasons.append('成交活跃')

    if score >= 3:
        signal, label, color = 'STRONG_BUY', '强烈买入', '#00d4aa'
    elif score >= 1:
        signal, label, color = 'BUY', '建议买入', '#4caf50'
    elif score >= 0:
        signal, label, color = 'HOLD', '持有观望', '#f0c040'
    elif score >= -2:
        signal, label, color = 'SELL', '建议卖出', '#ff9d5c'
    else:
        signal, label, color = 'STRONG_SELL', '强烈卖出', '#f85149'

    return {
        'signal': signal,
        'score': score,
        'label': label,
        'color': color,
        'pe': pe,
        'pb': pb,
        'reasons': reasons
    }


# ============= 路由 =============

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, stock_list=STOCK_LIST)


@app.route("/api/all")
def api_all():
    codes = [s["code"] for s in STOCK_LIST]
    raw = fetch_sina_quotes(codes)
    data = parse_sina_data(raw)
    code_map = {s["code"]: s for s in STOCK_LIST}
    for item in data:
        if item["code"] in code_map:
            item["display_name"] = code_map[item["code"]]["name"]
            item["type"] = code_map[item["code"]]["type"]
    return jsonify({"data": data, "update_time": _now_str()})


@app.route("/api/recommend")
def api_recommend():
    force = request.args.get("force", "0") == "1"
    if not force and _cache_is_valid():
        return jsonify(RECOMMEND_CACHE["data"])

    result = gen_recommend_stocks()
    # 补充 display_name 和 analysis
    long_term = result.get("long_term", [])
    short_term = result.get("short_term", [])

    for item in long_term + short_term:
        item["display_name"] = item.get("name", "--")
        item["type"] = "stock"
        item["analysis"] = calc_analysis_signal(item)
        # 确保价格数值正确
        item["price"] = item.get("price") or 0
        item["change"] = item.get("change") or 0
        item["change_pct"] = item.get("change_pct") or 0

    output = {
        "long_term": long_term,
        "short_term": short_term,
        "update_time": _now_str()
    }
    output["cache_date"] = _get_today_str()
    _update_cache(output)
    return jsonify(output)


@app.route("/api/query")
def api_query():
    codes_param = request.args.get("codes", "")
    if not codes_param:
        return jsonify({"error": "请提供股票代码，如 ?codes=sh600036,sz000001"}), 400
    codes = [c.strip() for c in codes_param.split(",") if c.strip()]
    if not codes:
        return jsonify({"error": "代码格式无效"}), 400
    raw = fetch_sina_quotes(codes)
    data = parse_sina_data(raw)
    return jsonify({"data": data, "update_time": _now_str()})


@app.route("/api/kline")
def api_kline():
    code = request.args.get("code", "")
    if not code:
        return jsonify({"error": "请提供股票代码"}), 400
    code = code.strip()
    match = re.match(r'^(s[hz])(\d{6})$', code, re.IGNORECASE)
    if not match:
        return jsonify({"error": f"代码格式无效: {code}"}), 400
    prefix = match.group(1).lower()
    num = match.group(2)
    bs_code = f"{prefix}.{num}"

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    try:
        bs.login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"
        )
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()

        if not data_list:
            return jsonify({"error": f"未获取到 {bs_code} 的K线数据"}), 404

        ohlc = []
        volumes = []
        for row in data_list:
            date_str = row[0]
            open_p = float(row[1]) if row[1] else 0
            high_p = float(row[2]) if row[2] else 0
            low_p = float(row[3]) if row[3] else 0
            close_p = float(row[4]) if row[4] else 0
            vol = float(row[5]) if row[5] else 0
            if close_p == 0:
                continue
            ohlc.append([date_str, open_p, close_p, low_p, high_p])
            volumes.append(vol)

        closes = [item[2] for item in ohlc]

        def calc_ma(data, period):
            result = [None] * len(data)
            for i in range(len(data)):
                if i >= period - 1:
                    window = data[i - period + 1:i + 1]
                    result[i] = round(sum(window) / period, 2)
            return result

        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)
        ma60 = calc_ma(closes, 60)

        return jsonify({
            "code": bs_code,
            "data": ohlc,
            "volumes": volumes,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
        })
    except Exception as e:
        try:
            bs.logout()
        except:
            pass
        return jsonify({"error": f"K线数据查询失败: {str(e)}"}), 500


def _now_str():
    return datetime.now().strftime("%H:%M:%S")


# ============= 回测引擎 =============

def fetch_historical_kline_for_backtest(bs_code, start_date, end_date):
    """获取历史日K线数据（含 PE/PB），用于回测"""
    try:
        bs.login()
        # 使用 baostock 获取含 PE/PB 的日K线
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,peTTM,pbMRQ",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"  # 前复权
        )
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()

        if not data_list:
            return None

        bars = []
        for row in data_list:
            try:
                date_str = row[0]
                open_p = float(row[1]) if row[1] else 0
                high_p = float(row[2]) if row[2] else 0
                low_p = float(row[3]) if row[3] else 0
                close_p = float(row[4]) if row[4] else 0
                volume = float(row[5]) if row[5] else 0
                peTTM = float(row[6]) if row[6] and row[6] != '' else None
                pbMRQ = float(row[7]) if row[7] and row[7] != '' else None
                if close_p <= 0:
                    continue
                bars.append({
                    "date": date_str,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": volume,
                    "pe": peTTM,
                    "pb": pbMRQ,
                })
            except (ValueError, TypeError):
                continue
        return bars
    except Exception as e:
        try:
            bs.logout()
        except:
            pass
        print(f"[BACKTEST] 获取历史数据失败: {e}")
        return None


def _backtest_score(bar, ma_dict, idx):
    """回测中的个股评分（简化版，不依赖实时 PE/PB 时用价格位置打分）"""
    score = 50.0

    pe = bar.get("pe")
    pb = bar.get("pb")

    if pe and pe > 0:
        if pe < 15:
            score += 20
        elif pe < 25:
            score += 10
        elif pe > 50:
            score -= 15
    else:
        # 无 PE 时中性
        score += 5

    if pb and pb > 0:
        if pb < 1.5:
            score += 15
        elif pb < 3:
            score += 8
        elif pb > 8:
            score -= 10
    else:
        score += 3

    # 价格相对 MA 位置
    close = bar["close"]
    for ma_period, weight in [(5, 6), (10, 4), (20, 3)]:
        ma_val = ma_dict.get(ma_period, [None] * len(ma_dict.get(5, [])))
        if idx < len(ma_val) and ma_val[idx] is not None and ma_val[idx] > 0:
            ratio = close / ma_val[idx]
            if 0.98 <= ratio <= 1.02:
                score += weight
            elif ratio > 1.05:
                score -= weight * 0.5

    # 成交量放大
    if idx >= 5:
        avg_vol_5 = sum(b["volume"] for b in list(ma_dict.get("_bars", []))[max(0, idx - 5):idx]) / min(5, idx)
        if bar["volume"] > avg_vol_5 * 1.5:
            score += 5

    return score


def run_backtest(bs_code, start_date, end_date, initial_capital=1_000_000.0):
    """执行回测，返回详细结果"""
    bars = fetch_historical_kline_for_backtest(bs_code, start_date, end_date)
    if not bars or len(bars) < 60:
        return {"error": "数据不足，至少需要60个交易日的数据"}

    # 计算 MA
    closes = [b["close"] for b in bars]
    def calc_ma(data, period):
        result = [None] * len(data)
        for i in range(len(data)):
            if i >= period - 1:
                result[i] = round(sum(data[i - period + 1:i + 1]) / period, 2)
        return result

    ma_dict = {
        5: calc_ma(closes, 5),
        10: calc_ma(closes, 10),
        20: calc_ma(closes, 20),
        60: calc_ma(closes, 60),
        "_bars": bars,
    }

    # 回测状态
    cash = initial_capital
    position = 0        # 持仓股数
    avg_cost = 0.0
    trades = []
    equity_curve = []   # [{date, equity, cash, market_value}]
    daily_returns = []

    for i, bar in enumerate(bars):
        close = bar["close"]
        date = bar["date"]

        # 持仓市值
        market_value = position * close
        equity = cash + market_value
        equity_curve.append({
            "date": date,
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
        })

        # 每日收益率
        if i > 0 and equity_curve[i - 1]["equity"] > 0:
            daily_ret = (equity - equity_curve[i - 1]["equity"]) / equity_curve[i - 1]["equity"]
            daily_returns.append(daily_ret)

        # 打分
        score = _backtest_score(bar, ma_dict, i)

        # 卖出逻辑
        if position > 0:
            sell_reason = None
            pnl_pct = (close - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0

            if pnl_pct <= -5:
                sell_reason = "止损(-5%)"
            elif pnl_pct >= 15:
                sell_reason = "止盈(+15%)"
            elif score < 40:
                sell_reason = "评分过低({:.0f})".format(score)

            if sell_reason:
                amount = close * position
                pnl = amount - position * avg_cost
                cash += amount
                trades.append({
                    "date": date, "action": "卖出", "price": round(close, 3),
                    "shares": position, "amount": round(amount, 2),
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": sell_reason, "score": round(score, 1),
                })
                position = 0
                avg_cost = 0.0

        # 买入逻辑
        elif position == 0 and score >= 55:
            # 使用可用资金的 80% 买入
            buy_amount = cash * 0.8
            shares = int(buy_amount / close / 100) * 100
            if shares >= 100:
                cost = shares * close
                cash -= cost
                position = shares
                avg_cost = close
                trades.append({
                    "date": date, "action": "买入", "price": round(close, 3),
                    "shares": shares, "amount": round(cost, 2),
                    "pnl": 0, "pnl_pct": 0,
                    "reason": "评分买入({:.0f})".format(score), "score": round(score, 1),
                })

    # 最后清仓
    if position > 0:
        final_close = bars[-1]["close"]
        amount = final_close * position
        pnl = amount - position * avg_cost
        pnl_pct = (final_close - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        cash += amount
        trades.append({
            "date": bars[-1]["date"], "action": "卖出(清仓)", "price": round(final_close, 3),
            "shares": position, "amount": round(amount, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            "reason": "回测结束清仓", "score": 0,
        })
        position = 0

    final_equity = cash
    total_return = final_equity - initial_capital
    total_return_pct = (total_return / initial_capital) * 100

    # 计算收益指标
    buy_trades = [t for t in trades if "买入" in t["action"]]
    sell_trades = [t for t in trades if "卖出" in t["action"]]
    win_trades = [t for t in sell_trades if t["pnl"] > 0]
    win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0

    avg_win = sum(t["pnl"] for t in win_trades) / len(win_trades) if win_trades else 0
    avg_loss = sum(t["pnl"] for t in sell_trades if t["pnl"] <= 0) / max(len([t for t in sell_trades if t["pnl"] <= 0]), 1)

    # 最大回撤
    peak = initial_capital
    max_drawdown = 0.0
    for pt in equity_curve:
        if pt["equity"] > peak:
            peak = pt["equity"]
        dd = (peak - pt["equity"]) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    # 夏普比率（简化）
    if len(daily_returns) > 1:
        import math
        mean_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe = (mean_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # 基准收益（买入持有）
    buy_hold_ret = (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 100

    return {
        "code": bs_code,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 2),
        "buy_hold_return_pct": round(buy_hold_ret, 2),
        "total_trades": len(trades),
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "trades": trades[-30:],  # 最近30笔
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 200)],  # 最多200个点
        "data_points": len(bars),
    }


# ============= 回测 API 路由 =============

@app.route("/api/backtest")
def api_backtest():
    code = request.args.get("code", "").strip()
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    capital = request.args.get("capital", "1000000").strip()

    if not code:
        return jsonify({"error": "请提供股票代码"}), 400
    if not start:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")

    try:
        capital = float(capital)
        if capital < 10000:
            capital = 10000
    except:
        capital = 1_000_000.0

    # 解析代码格式
    match = re.match(r'^(s[hz])(\d{6})$', code, re.IGNORECASE)
    if not match:
        # 尝试从纯代码推断
        if re.match(r'^\d{6}$', code):
            if code.startswith(("6", "68", "9")):
                code = "sh" + code
            else:
                code = "sz" + code
            match = re.match(r'^(s[hz])(\d{6})$', code, re.IGNORECASE)
        if not match:
            return jsonify({"error": f"代码格式无效: {code}，请使用 sh/sz+6位代码"}), 400

    prefix = match.group(1).lower()
    num = match.group(2)
    bs_code = f"{prefix}.{num}"

    try:
        result = run_backtest(bs_code, start, end, capital)
        if "error" in result:
            return jsonify(result), 400
        result["display_code"] = code
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"回测执行失败: {str(e)}"}), 500


# ===== AI 交易决策引擎 =====
def _ai_trade_decision(recommended):
    """AI 从推荐股票中选股，生成买卖决策"""
    decisions = {"buy": [], "sell": [], "hold": [], "summary": "", "scored": []}
    holdings = TRADING_STATE["holdings"]
    cash = TRADING_STATE["cash"]

    # 第一步：对所有推荐股票评分
    scored = []
    for stock in recommended:
        code = (stock.get("code") or "").replace("s_", "")
        name = stock.get("name", "--")
        price = (stock.get("price") or 0) or 0
        if not code or float(price) <= 0:
            continue
        price = float(price)
        score = 50.0
        reasons = []

        an = stock.get("analysis") or {}
        pe = an.get("pe")
        if pe and pe > 0:
            if pe < 15:
                score += 20
                reasons.append("低PE")
            elif pe < 25:
                score += 10
                reasons.append("PE合理")
            elif pe > 50:
                score -= 15
                reasons.append("PE偏高")

        pb = an.get("pb")
        if pb and pb > 0:
            if pb < 1.5:
                score += 15
                reasons.append("低PB")
            elif pb < 3:
                score += 8
                reasons.append("PB合理")
            elif pb > 8:
                score -= 10
                reasons.append("PB偏高")

        chg_pct = float(stock.get("change_pct") or 0)
        if 1 <= chg_pct <= 5:
            score += 8
            reasons.append("温和上涨")
        elif 0 < chg_pct < 1:
            score += 4
        elif -3 <= chg_pct < 0:
            score += 2
        elif chg_pct < -5:
            score -= 10
            reasons.append("大幅下跌")
        elif chg_pct > 8:
            score -= 5
            reasons.append("涨幅过大")

        turnover = stock.get("turnover_rate")
        if turnover and 2 <= float(turnover) <= 8:
            score += 5
            reasons.append("换手活跃")
        elif turnover and float(turnover) > 15:
            score -= 5
            reasons.append("换手过高")

        mcap = stock.get("market_cap")
        if mcap and 5e9 <= float(mcap) <= 5e10:
            score += 5
            reasons.append("中盘股")

        trade = stock.get("_trade") or {}
        buy_price = trade.get("buy_price")
        if buy_price and float(buy_price) > 0:
            if float(price) <= float(buy_price) * 1.03:
                score += 10
                reasons.append("接近建议买入价")
            elif float(price) > float(buy_price) * 1.2:
                score -= 8
                reasons.append("远离买入区间")

        scored.append({
            "code": code, "name": name, "price": price,
            "score": round(score, 1), "reasons": reasons,
            "chg_pct": chg_pct,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    decisions["scored"] = [{"code": s["code"], "name": s["name"], "score": s["score"], "reasons": s["reasons"][:3]} for s in scored[:20]]

    # 第二步：卖出决策
    for code, h in list(holdings.items()):
        sell_reason = None
        matched = [s for s in scored if s["code"] == code]
        curr_price = matched[0]["price"] if matched else h["avg_cost"]
        pnl_pct = (curr_price - h["avg_cost"]) / h["avg_cost"] * 100

        if pnl_pct <= -5:
            sell_reason = f"止损(亏损{pnl_pct:.1f}%)"
        elif pnl_pct >= 15:
            sell_reason = f"止盈(盈利{pnl_pct:.1f}%)"
        elif matched and matched[0]["score"] < 45:
            sell_reason = "评分下降，调仓换股"

        if sell_reason and curr_price > 0:
            amount = curr_price * h["shares"]
            decisions["sell"].append({
                "code": code, "name": h["name"], "shares": h["shares"],
                "price": round(curr_price, 3), "amount": round(amount, 2),
                "pnl_pct": round(pnl_pct, 2), "reason": sell_reason,
                "cost": round(h["avg_cost"], 3),
            })

    # 第三步：买入决策
    sell_amount = sum(s["amount"] for s in decisions["sell"])
    available_cash = cash + sell_amount
    max_positions = 5
    current_positions = len(holdings) - len(decisions["sell"])
    positions_needed = max_positions - current_positions

    if positions_needed > 0 and available_cash > 50000:
        per_stock_budget = min(available_cash / max_positions, 200000)
        candidates = [s for s in scored if s["code"] not in holdings and s["score"] >= 55]
        for cand in candidates[:positions_needed]:
            if per_stock_budget < 10000:
                break
            shares = int(per_stock_budget / cand["price"] / 100) * 100
            if shares < 100:
                continue
            amount = shares * cand["price"]
            if amount > available_cash:
                continue
            available_cash -= amount
            decisions["buy"].append({
                "code": cand["code"], "name": cand["name"], "shares": shares,
                "price": cand["price"], "amount": round(amount, 2),
                "score": cand["score"], "reasons": cand["reasons"],
            })

    buy_n = len(decisions["buy"])
    sell_n = len(decisions["sell"])
    if buy_n == 0 and sell_n == 0:
        decisions["summary"] = "当前持仓合理，无需调仓。"
    else:
        parts = []
        if sell_n > 0:
            parts.append(f"卖出 {sell_n} 只")
        if buy_n > 0:
            parts.append(f"买入 {buy_n} 只")
        decisions["summary"] = "AI 决策：" + "，".join(parts)
    return decisions


# ===== 交易 API 路由 =====

@app.route("/api/trading/status")
def api_trading_status():
    """获取当前交易状态（持仓、历史、资金）"""
    total_market_value = 0.0
    holdings_list = []
    for code, h in TRADING_STATE["holdings"].items():
        mv = h.get("shares", 0) * h.get("curr_price", h["avg_cost"])
        pnl = mv - h.get("shares", 0) * h["avg_cost"]
        pnl_pct = ((h.get("curr_price", h["avg_cost"]) - h["avg_cost"]) / h["avg_cost"] * 100) if h["avg_cost"] > 0 else 0
        total_market_value += mv
        holdings_list.append({
            "code": code, "name": h["name"], "shares": h["shares"],
            "avg_cost": round(h["avg_cost"], 3),
            "curr_price": round(h.get("curr_price", h["avg_cost"]), 3),
            "market_value": round(mv, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "buy_date": h.get("buy_date", ""),
        })

    total_assets = TRADING_STATE["cash"] + total_market_value
    total_pnl = total_assets - 1_000_000.0
    total_pnl_pct = (total_pnl / 1_000_000.0) * 100

    return jsonify({
        "cash": round(TRADING_STATE["cash"], 2),
        "holdings": holdings_list,
        "total_market_value": round(total_market_value, 2),
        "total_assets": round(total_assets, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "initial_capital": 1_000_000,
        "history": TRADING_STATE["history"][-50:],
        "last_decision": TRADING_STATE.get("last_decision", ""),
    })


@app.route("/api/trading/decide")
def api_trading_decide():
    """触发 AI 决策并执行交易"""
    # 获取推荐股票数据
    if not _cache_is_valid():
        gen_recommend_stocks()
    recommended = RECOMMEND_CACHE["data"].get("long_term", []) + RECOMMEND_CACHE["data"].get("short_term", [])

    decisions = _ai_trade_decision(recommended)
    now_ts = _now_str()

    # 执行卖出
    for s in decisions["sell"]:
        code = s["code"]
        if code in TRADING_STATE["holdings"]:
            h = TRADING_STATE["holdings"].pop(code)
            TRADING_STATE["cash"] += s["amount"]
            TRADING_STATE["total_invested"] -= h["shares"] * h["avg_cost"]
            TRADING_STATE["history"].append({
                "time": now_ts, "action": "卖出", "code": code, "name": s["name"],
                "shares": s["shares"], "price": s["price"], "amount": s["amount"],
                "pnl_pct": s.get("pnl_pct", 0), "reason": s["reason"],
            })

    # 执行买入
    for b in decisions["buy"]:
        code = b["code"]
        if TRADING_STATE["cash"] >= b["amount"]:
            TRADING_STATE["cash"] -= b["amount"]
            TRADING_STATE["holdings"][code] = {
                "name": b["name"], "shares": b["shares"],
                "avg_cost": b["price"], "buy_date": _get_today_str(),
                "curr_price": b["price"],
            }
            TRADING_STATE["total_invested"] += b["amount"]
            TRADING_STATE["history"].append({
                "time": now_ts, "action": "买入", "code": code, "name": b["name"],
                "shares": b["shares"], "price": b["price"], "amount": b["amount"],
                "reason": "AI评分 " + str(b.get("score", 0)),
            })

    TRADING_STATE["last_decision"] = now_ts
    _save_trading_state()

    return jsonify({
        "decisions": decisions,
        "executed": True,
        "time": now_ts,
    })


@app.route("/api/trading/reset")
def api_trading_reset():
    """重置交易状态"""
    TRADING_STATE["cash"] = 1_000_000.0
    TRADING_STATE["holdings"] = {}
    TRADING_STATE["history"] = []
    TRADING_STATE["total_invested"] = 0.0
    TRADING_STATE["last_decision"] = ""
    _save_trading_state()
    return jsonify({"message": "交易状态已重置", "cash": 1_000_000.0})


@app.route("/api/trading/tick")
def api_trading_tick():
    """实时更新持仓股票的市价"""
    holdings = TRADING_STATE["holdings"]
    if not holdings:
        return jsonify({"updated": 0})
    codes = []
    for code in holdings:
        prefix = "sh" if code.startswith("6") or code.startswith("68") else "sz"
        codes.append(prefix + code)
    if not codes:
        return jsonify({"updated": 0})
    try:
        raw = fetch_sina_quotes(codes)
        data = parse_sina_data(raw)
        for item in data:
            pure_code = (item.get("code") or "").replace("s_", "")
            if pure_code in holdings:
                holdings[pure_code]["curr_price"] = item.get("price", holdings[pure_code]["avg_cost"])
        return jsonify({"updated": len(data)})
    except Exception as e:
        return jsonify({"updated": 0, "error": str(e)})


# ============= 前端 HTML =============
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📈 实时股票行情 - 新浪数据源</title>
<style>
    :root {
        --bg: #0f1117;
        --card-bg: #1a1d2e;
        --border: #2a2d3e;
        --text: #e1e4e8;
        --text-secondary: #8b949e;
        --up: #f85149;
        --down: #3fb950;
        --header-bg: #161b22;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        background: var(--bg);
        color: var(--text);
        min-height: 100vh;
    }
    .header {
        background: var(--header-bg);
        border-bottom: 1px solid var(--border);
        padding: 12px 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        position: sticky;
        top: 0;
        z-index: 100;
        backdrop-filter: blur(10px);
    }
    .header h1 { font-size: 18px; font-weight: 600; letter-spacing: 0.5px; }
    .header-right { display: flex; align-items: center; gap: 16px; font-size: 13px; }
    .update-dot {
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #3fb950;
        animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
    .auto-refresh-badge { background: #21262d; padding: 4px 10px; border-radius: 12px; font-size: 12px; color: var(--text-secondary); }
    .refresh-btn { background: #238636; color: #fff; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 500; }
    .refresh-btn:hover { background: #2ea043; }

    .detail-panel {
        position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%) scale(0.92);
        width: 960px; max-width: 95vw; max-height: 90vh;
        background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px;
        z-index: 200; opacity: 0; pointer-events: none;
        transition: opacity 0.25s ease, transform 0.25s ease;
        overflow-y: auto; padding: 28px; box-shadow: 0 16px 48px rgba(0,0,0,0.6);
    }
    .detail-panel.open { opacity: 1; pointer-events: auto; transform: translate(-50%,-50%) scale(1); }
    .detail-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.5); z-index: 199; opacity: 0; pointer-events: none;
        transition: opacity 0.25s ease;
    }
    .detail-overlay.show { opacity: 1; pointer-events: auto; }
    .detail-panel .close-btn {
        position: absolute; top: 16px; right: 16px; width: 32px; height: 32px;
        border: 1px solid var(--border); border-radius: 50%; background: transparent;
        color: var(--text); font-size: 18px; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
    }
    .detail-panel .close-btn:hover { background: rgba(248,81,73,0.15); border-color: #f85149; }
    .detail-panel h2 { font-size: 20px; margin-bottom: 4px; }
    .detail-panel .detail-code { font-size: 12px; color: var(--text-secondary); margin-bottom: 16px; }
    .detail-panel .detail-price { font-size: 32px; font-weight: 700; margin-bottom: 4px; }
    .detail-panel .detail-change { font-size: 14px; margin-bottom: 20px; display: flex; gap: 16px; }
    .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .detail-item { background: var(--bg); border-radius: 8px; padding: 10px 12px; }
    .detail-item .label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 2px; }
    .detail-item .value { font-size: 16px; font-weight: 500; }
    .detail-item .value.up { color: var(--up); }
    .detail-item .value.down { color: var(--down); }

    .search-bar { padding: 16px 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .search-input {
        flex: 1; min-width: 200px; padding: 8px 14px;
        background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
        color: var(--text); font-size: 14px; outline: none;
    }
    .search-input:focus { border-color: #58a6ff; }
    .filter-btn {
        padding: 8px 16px; background: var(--card-bg); border: 1px solid var(--border);
        border-radius: 8px; color: var(--text-secondary); cursor: pointer; font-size: 13px;
    }
    .filter-btn.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
    .filter-btn:hover { border-color: #58a6ff; }

    .table-container { padding: 0 24px 24px; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; min-width: 800px; }
    thead th {
        padding: 10px 12px; text-align: right; color: var(--text-secondary);
        font-weight: 500; font-size: 12px; text-transform: uppercase;
        border-bottom: 1px solid var(--border); white-space: nowrap;
    }
    thead th:first-child { text-align: left; }
    thead th:nth-child(2) { text-align: left; }
    tbody td {
        padding: 10px 12px; text-align: right; border-bottom: 1px solid rgba(42,45,62,0.5);
        white-space: nowrap;
    }
    tbody td:first-child { text-align: left; }
    tbody td:nth-child(2) { text-align: left; }
    tbody tr { transition: background 0.15s; cursor: pointer; }
    tbody tr:hover { background: rgba(88,166,255,0.05); }
    .up { color: var(--up); }
    .down { color: var(--down); }
    .index-row { background: rgba(255,215,0,0.03); }
    .index-badge {
        display: inline-block; padding: 2px 6px; border-radius: 3px;
        font-size: 10px; font-weight: 600; background: #c69026; color: #000; margin-left: 4px;
    }
    .name-cell { font-weight: 500; }
    .code-cell { font-size: 12px; color: var(--text-secondary); }
    .price-cell { font-weight: 600; font-size: 15px; }

    .footer { padding: 12px 24px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text-secondary); display: flex; justify-content: space-between; }
    .custom-query { padding: 12px 24px; display: flex; gap: 8px; align-items: center; border-top: 1px solid var(--border); }
    .custom-query input { flex: 1; max-width: 400px; padding: 6px 12px; background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; }
    .custom-query input:focus { border-color: #58a6ff; }
    .custom-query button { padding: 6px 14px; background: #1f6feb; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
    .custom-query .hint { font-size: 11px; color: var(--text-secondary); }

    .chart-container { width: 100%; height: 320px; margin-top: 20px; border-top: 1px solid var(--border); padding-top: 16px; }
    .chart-loading { width: 100%; height: 320px; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); font-size: 13px; }
    .chart-error { width: 100%; height: 320px; display: flex; align-items: center; justify-content: center; color: #f85149; font-size: 13px; text-align: center; padding: 20px; }

    .recommend-section { display: flex; gap: 16px; padding: 16px 24px; }
    .rec-panel {
        flex: 1; min-width: 0;
        background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
        overflow: hidden;
    }
    .rec-panel-header {
        padding: 10px 16px; font-size: 14px; font-weight: 600;
        border-bottom: 1px solid var(--border);
        display: flex; align-items: center; justify-content: space-between;
        position: sticky; top: 0; background: var(--card-bg); z-index: 1;
    }
    .rec-panel-header.long { color: #f0c040; }
    .rec-panel-header.short { color: #ff9d5c; }
    .rec-card-list {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
        gap: 10px; padding: 12px; max-height: 480px; overflow-y: auto;
    }
    .rec-card {
        background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
        padding: 12px 14px; cursor: pointer; transition: all 0.15s;
    }
    .rec-card:hover { border-color: #58a6ff; box-shadow: 0 2px 12px rgba(88,166,255,0.1); }
    .rec-card-add { position: absolute; top: 6px; right: 6px; width: 24px; height: 24px; border-radius: 50%; border: 1px solid #58a6ff44; background: #1a1d2e; color: #58a6ff; font-size: 16px; line-height: 22px; text-align: center; cursor: pointer; padding: 0; opacity: 0; transition: opacity 0.2s; }
    .rec-card:hover .rec-card-add { opacity: 1; }
    .rec-card-add:hover { background: #58a6ff33; border-color: #58a6ff; }
    .rec-card-add.in-watchlist { color: #f0883e; border-color: #f0883e66; background: #f0883e22; }
    .rec-card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
    .rec-card-name { font-size: 14px; font-weight: 600; }
    .rec-card-code { font-size: 11px; color: var(--text-secondary); }
    .rec-card-price-row { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
    .rec-card-price { font-size: 18px; font-weight: 700; }
    .rec-card-change { font-size: 12px; }
    .rec-signal-row { margin-bottom: 8px; }
    .rec-signal-badge {
        display: inline-block; padding: 2px 8px; border-radius: 10px;
        font-size: 10px; font-weight: 700; letter-spacing: 0.3px;
    }
    .rec-trade-prices {
        display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px;
        margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border);
    }
    .rec-trade-item { text-align: center; }
    .rec-trade-label { font-size: 9px; color: var(--text-secondary); margin-bottom: 2px; text-transform: uppercase; }
    .rec-trade-value { font-size: 13px; font-weight: 600; }
    .rec-trade-value.buy { color: #4caf50; }
    .rec-trade-value.sell { color: #ff9d5c; }
    .rec-trade-value.stop { color: #f85149; }
    .rec-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
    .rec-tag { background: rgba(42,45,62,0.8); padding: 1px 6px; border-radius: 4px; font-size: 9px; white-space: nowrap; color: var(--text-secondary); }
    .rec-loading { padding: 40px; text-align: center; color: var(--text-secondary); font-size: 13px; }

    /* ===== AI 交易面板 ===== */
    .trading-section {
        margin: 0 24px 16px;
        display: flex; gap: 16px;
    }
    .trading-panel {
        flex: 1; background: var(--card-bg);
        border: 1px solid var(--border); border-radius: 12px;
        overflow: hidden;
    }
    .trading-header {
        padding: 10px 16px; font-size: 14px; font-weight: 600;
        border-bottom: 1px solid var(--border);
        display: flex; align-items: center; justify-content: space-between;
        background: var(--card-bg); position: sticky; top: 0; z-index: 1;
    }
    .trading-header h3 { font-size: 14px; }
    .trading-header .cb { color: #4caf50; }
    .trading-header .cs { color: #ff9d5c; }
    .trading-balance {
        display: flex; gap: 16px; padding: 10px 16px;
        flex-wrap: wrap; border-bottom: 1px solid var(--border);
    }
    .trading-bal-item { text-align: center; min-width: 90px; }
    .trading-bal-item .lbl { font-size: 10px; color: var(--text-secondary); }
    .trading-bal-item .val { font-size: 15px; font-weight: 600; }
    .trading-bal-item .val.up { color: var(--up); }
    .trading-bal-item .val.down { color: var(--down); }
    .trading-actions {
        padding: 8px 16px; display: flex; gap: 8px;
        border-bottom: 1px solid var(--border);
    }
    .trade-btn {
        padding: 5px 14px; border-radius: 6px; border: none;
        cursor: pointer; font-size: 12px; font-weight: 500;
    }
    .trade-btn.decide { background: #1f6feb; color: #fff; }
    .trade-btn.decide:hover { background: #388bfd; }
    .trade-btn.reset { background: transparent; color: var(--text-secondary); border: 1px solid var(--border); }
    .trade-btn.reset:hover { border-color: #f85149; color: #f85149; }
    .trade-summary { padding: 8px 16px; font-size: 12px; color: var(--text-secondary); border-bottom: 1px solid var(--border); }
    .trade-list {
        padding: 8px 12px; max-height: 240px; overflow-y: auto;
    }
    .trade-row {
        display: flex; align-items: center; gap: 8px;
        padding: 6px 8px; border-bottom: 1px solid rgba(42,45,62,0.5);
        font-size: 12px;
    }
    .trade-row:last-child { border-bottom: none; }
    .trade-row .act { font-weight: 600; min-width: 32px; }
    .trade-row .act.buy { color: var(--up); }
    .trade-row .act.sell { color: var(--down); }
    .trade-row .info { flex: 1; }
    .trade-row .info .nm { font-weight: 500; }
    .trade-row .info .cd { font-size: 10px; color: var(--text-secondary); margin-left: 6px; }
    .trade-row .info .rs { font-size: 10px; color: var(--text-secondary); }
    .trade-row .num { text-align: right; }
    .trade-row .pnl { text-align: right; min-width: 60px; font-weight: 600; }
    .trade-row .pnl.win { color: var(--up); }
    .trade-row .pnl.loss { color: var(--down); }
    .decide-panel {
        position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%);
        width: 600px; max-width: 90vw; max-height: 80vh;
        background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
        z-index: 200; padding: 20px; overflow-y: auto;
        box-shadow: 0 12px 40px rgba(0,0,0,0.6);
    }
    .decide-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.4); z-index: 199;
    }

    .watchlist-section {
        margin: 0 24px 16px;
    }
    .watchlist-panel {
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
    }
    .watchlist-header {
        padding: 10px 16px; font-size: 14px; font-weight: 600;
        border-bottom: 1px solid var(--border);
        display: flex; align-items: center; justify-content: space-between;
        background: var(--card-bg); position: sticky; top: 0; z-index: 1;
    }
    .watchlist-grid {
        display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
        gap: 8px; padding: 10px 14px; max-height: 320px; overflow-y: auto;
    }
    .watchlist-card {
        background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
        padding: 10px 12px; display: flex; align-items: center; gap: 8px;
        cursor: pointer; transition: border-color 0.15s;
    }
    .watchlist-card:hover { border-color: #58a6ff; }
    .watchlist-card-info { flex: 1; min-width: 0; }
    .watchlist-card-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .watchlist-card-code { font-size: 10px; color: var(--text-secondary); }
    .watchlist-card-remove {
        width: 20px; height: 20px; border-radius: 50%; border: 1px solid #f8514944;
        background: transparent; color: #f85149; font-size: 12px; line-height: 18px;
        text-align: center; cursor: pointer; flex-shrink: 0; padding: 0;
        opacity: 0; transition: opacity 0.15s;
    }
    .watchlist-card:hover .watchlist-card-remove { opacity: 1; }
    .watchlist-card-remove:hover { background: #f8514922; }
    .watchlist-empty { text-align: center; padding: 20px; color: var(--text-secondary); font-size: 12px; }

    @media (max-width: 900px) {
        .trading-section { flex-direction: column; }
        .recommend-section { flex-direction: column; }
        .rec-card-list { grid-template-columns: 1fr; max-height: 320px; }
        .watchlist-grid { grid-template-columns: 1fr; }
    }

    @media (max-width: 768px) {
        .header { padding: 10px 16px; }
        .header h1 { font-size: 16px; }
        .search-bar { padding: 10px 16px; }
        .table-container { padding: 0 8px 16px; }
        table { font-size: 12px; }
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
</head>
<body>

<div class="header">
    <h1>📈 实时股票行情</h1>
    <div class="header-right">
        <span class="update-dot" id="statusDot"></span>
        <span id="updateTime" style="color: var(--text-secondary);">--</span>
        <span class="auto-refresh-badge">⏱ 每5秒刷新</span>
        <button class="refresh-btn" onclick="fetchData()">🔄 立即刷新</button>
    </div>
</div>

<div class="recommend-section">
    <div class="rec-panel">
        <div class="rec-panel-header long">📈 长期推荐（估值优先）</div>
        <div class="rec-card-list" id="longList">
            <div class="rec-loading">⏳ 加载中...</div>
        </div>
    </div>
    <div class="rec-panel">
        <div class="rec-panel-header short">🔥 短期推荐（量价结合）</div>
        <div class="rec-card-list" id="shortList">
            <div class="rec-loading">⏳ 加载中...</div>
        </div>
    </div>
</div>

<div class="trading-section">
    <div class="trading-panel" id="accountPanel">
        <div class="trading-header">
            <h3>💰 模拟账户</h3>
            <span style="color:#4caf50;font-size:11px;">初始 100万</span>
        </div>
        <div class="trading-balance" id="accountBalance">
            <div class="trading-bal-item"><div class="lbl">可用资金</div><div class="val">--</div></div>
            <div class="trading-bal-item"><div class="lbl">持仓市值</div><div class="val">--</div></div>
            <div class="trading-bal-item"><div class="lbl">总资产</div><div class="val">--</div></div>
            <div class="trading-bal-item"><div class="lbl">总盈亏</div><div class="val">--</div></div>
        </div>
        <div class="trading-actions">
            <button class="trade-btn decide" onclick="aiDecide()">🤖 AI 决策选股</button>
            <button class="trade-btn reset" onclick="resetTrading()">🔄 重置账户</button>
        </div>
        <div class="trade-summary" id="lastDecision">上次决策：--</div>
        <div class="trade-list" id="holdingsList">
            <div style="padding:16px;text-align:center;color:var(--text-secondary);font-size:12px;">暂无持仓，点击 AI 决策选股</div>
        </div>
    </div>
    <div class="trading-panel" id="historyPanel">
        <div class="trading-header">
            <h3>📋 交易记录</h3>
        </div>
        <div class="trade-list" id="historyList">
            <div style="padding:16px;text-align:center;color:var(--text-secondary);font-size:12px;">暂无交易记录</div>
        </div>
    </div>

    <!-- 回测面板（基于模拟持仓） -->
    <div class="trading-panel" id="backtestPanel" style="flex: 1; min-width: 340px;">
        <div class="trading-header">
            <h3>📊 持仓回测</h3>
            <span style="font-size:10px;color:var(--text-secondary);">基于AI评分策略的持仓历史回测</span>
        </div>
        <div class="backtest-form" style="padding:10px 16px;display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;">
            <div><label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">持仓股票</label>
                <select id="btCode" style="background:#1a1d2e;border:1px solid #2a2d3e;color:#e1e4e8;padding:5px 8px;border-radius:6px;font-size:12px;min-width:120px;">
                    <option value="">-- 选择持仓股票 --</option>
                </select>
            </div>
            <div><label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">开始日期</label><input type="date" id="btStart" style="background:#1a1d2e;border:1px solid #2a2d3e;color:#e1e4e8;padding:5px 8px;border-radius:6px;width:130px;"></div>
            <div><label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">结束日期</label><input type="date" id="btEnd" style="background:#1a1d2e;border:1px solid #2a2d3e;color:#e1e4e8;padding:5px 8px;border-radius:6px;width:130px;"></div>
            <div><label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">初始资金(万)</label><input type="number" id="btCapital" value="100" min="1" max="10000" style="background:#1a1d2e;border:1px solid #2a2d3e;color:#e1e4e8;padding:5px 8px;border-radius:6px;width:90px;"></div>
            <button class="trade-btn decide" onclick="runBacktest()" id="btRunBtn" style="height:32px;">▶ 运行回测</button>
        </div>
        <div style="padding:6px 16px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--border);" id="btQuickBtns">
            <span style="font-size:10px;color:var(--text-secondary);line-height:24px;">快速回测：</span>
            <span style="font-size:11px;color:var(--text-secondary);line-height:24px;">持仓加载中...</span>
        </div>
        <div style="padding:8px 16px;font-size:10px;color:var(--text-secondary);">💡 数据来源: baostock，含PE/PB估值指标综合评分。回测期间至少需要60个交易日数据。</div>
        <div class="backtest-results" id="btResults" style="padding:12px 16px;max-height:500px;overflow-y:auto;">
            <div class="bt-empty" style="text-align:center;padding:30px;color:var(--text-secondary);font-size:12px;">选择持仓股票并点击运行回测，查看AI策略的历史表现</div>
        </div>
    </div>
</div>

<!-- 自选股板块 -->
<div class="watchlist-section" id="watchlistSection">
    <div class="watchlist-panel">
        <div class="watchlist-header">
            <span>⭐ 我的自选</span>
            <span style="font-size:11px;color:var(--text-secondary);" id="wlCount"></span>
        </div>
        <div class="watchlist-grid" id="watchlistGrid"></div>
    </div>
</div>

<!-- AI 决策弹窗 -->
<div class="decide-overlay" id="decideOverlay" style="display:none;"></div>
<div class="decide-panel" id="decidePanel" style="display:none;"></div>

<div class="search-bar">
    <input class="search-input" type="text" id="searchInput" placeholder="🔍 搜索股票名称或代码...">
    <button class="filter-btn active" onclick="setFilter('all', this)">全部</button>
    <button class="filter-btn" onclick="setFilter('index', this)">指数</button>
    <button class="filter-btn" onclick="setFilter('stock', this)">个股</button>
    <button class="filter-btn" onclick="setFilter('up', this)">📈 上涨</button>
    <button class="filter-btn" onclick="setFilter('down', this)">📉 下跌</button>
</div>

<div class="table-container">
    <table>
        <thead>
            <tr>
                <th>类型</th>
                <th>名称 / 代码</th>
                <th>最新价</th>
                <th>涨跌额</th>
                <th>涨跌幅</th>
                <th>成交量</th>
                <th>成交额</th>
                <th>今开</th>
                <th>最高</th>
                <th>最低</th>
            </tr>
        </thead>
        <tbody id="stockTableBody">
            <tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">加载中...</td></tr>
        </tbody>
    </table>
</div>

<div class="custom-query">
    <span class="hint">自定义查询：</span>
    <input type="text" id="customCodes" placeholder="如: sh600519,sz000858,s_sh000001">
    <button onclick="customQuery()">查询</button>
    <span class="hint">格式：sh/sz + 代码，指数加 s_ 前缀</span>
</div>

<div class="detail-overlay" id="detailOverlay" onclick="hideDetail()"></div>
<div class="detail-panel" id="detailPanel">
    <button class="close-btn" onclick="hideDetail()">✕</button>
    <div id="detailContent"></div>
    <div id="chartArea"></div>
</div>

<div class="footer">
    <span>数据来源：新浪财经 hq.sinajs.cn + 东方财富 push2.eastmoney.com | 仅供学习参考，不构成投资建议</span>
    <span id="recordCount">共 0 条记录</span>
</div>

<script>
    let currentFilter = 'all';
    let allData = [];
    let recommendData = { long_term: [], short_term: [] };
    let autoRefreshTimer = null;

    function setFilter(filter, btn) {
        currentFilter = filter;
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderTable();
    }

    function applyFilter(data) {
        const searchText = document.getElementById('searchInput').value.toLowerCase();
        let filtered = data;
        if (currentFilter === 'index') filtered = data.filter(d => d.type === 'index');
        if (currentFilter === 'stock') filtered = data.filter(d => d.type === 'stock');
        if (currentFilter === 'up') filtered = data.filter(d => d.change > 0);
        if (currentFilter === 'down') filtered = data.filter(d => d.change < 0);
        if (searchText) {
            filtered = filtered.filter(d =>
                (d.display_name || d.name || '').toLowerCase().includes(searchText) ||
                (d.code || '').toLowerCase().includes(searchText)
            );
        }
        return filtered;
    }

    function formatVolume(v) {
        if (!v) return '--';
        if (v >= 100000000) return (v / 100000000).toFixed(2) + '亿';
        if (v >= 10000) return (v / 10000).toFixed(0) + '万';
        return v.toLocaleString();
    }

    function formatAmount(v) {
        if (!v) return '--';
        if (v >= 100000000) return (v / 100000000).toFixed(2) + '亿';
        if (v >= 10000) return (v / 10000).toFixed(0) + '万';
        return parseFloat(v).toFixed(0);
    }

    function priceClass(v) {
        if (v > 0) return 'up';
        if (v < 0) return 'down';
        return '';
    }

    function renderTable() {
        const filtered = applyFilter(allData);
        const tbody = document.getElementById('stockTableBody');
        document.getElementById('recordCount').textContent = '共 ' + filtered.length + ' 条记录';
        if (filtered.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">暂无数据</td></tr>';
            return;
        }
        tbody.innerHTML = filtered.map(d => {
            const isIdx = d.type === 'index';
            const cls = priceClass(d.change);
            const rowClass = isIdx ? 'index-row' : '';
            const typeLabel = isIdx ? '<span class="index-badge">指数</span>' : '<span style="color:var(--text-secondary);font-size:11px;">个股</span>';
            const codeDisplay = d.code ? d.code.replace(/^s_/i, '') : '';
            const displayName = d.display_name || d.name || '--';
            return `<tr class="${rowClass}">
                <td>${typeLabel}</td>
                <td><div class="name-cell">${displayName}</div><div class="code-cell">${codeDisplay}</div></td>
                <td class="price-cell ${cls}">${(d.price || 0).toFixed(isIdx ? 2 : 2)}</td>
                <td class="${cls}">${d.change > 0 ? '+' : ''}${(d.change || 0).toFixed(isIdx ? 2 : 2)}</td>
                <td class="${cls}">${d.change_pct > 0 ? '+' : ''}${(d.change_pct || 0).toFixed(2)}%</td>
                <td>${isIdx ? formatAmount(d.volume) : formatVolume(d.volume)}</td>
                <td>${formatAmount(d.amount)}</td>
                <td>${isIdx ? '--' : (d.open || 0).toFixed(2)}</td>
                <td>${isIdx ? '--' : (d.high || 0).toFixed(2)}</td>
                <td>${isIdx ? '--' : (d.low || 0).toFixed(2)}</td>
            </tr>`;
        }).join('');
    }

    async function fetchData() {
        const dot = document.getElementById('statusDot');
        dot.style.background = '#d29922';
        try {
            const resp = await fetch('/api/all');
            const json = await resp.json();
            allData = json.data || [];
            document.getElementById('updateTime').textContent = json.update_time || '--';
            dot.style.background = '#3fb950';
            renderTable();
        } catch (e) {
            console.error('Fetch error:', e);
            dot.style.background = '#f85149';
        }
    }

    async function customQuery() {
        const codes = document.getElementById('customCodes').value.trim();
        if (!codes) return;
        try {
            const resp = await fetch('/api/query?codes=' + encodeURIComponent(codes));
            const json = await resp.json();
            if (json.data && json.data.length > 0) {
                allData = json.data.map(d => ({ ...d, display_name: d.name }));
                currentFilter = 'all';
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                document.querySelector('.filter-btn').classList.add('active');
                document.getElementById('updateTime').textContent = json.update_time || '--';
                renderTable();
            } else {
                alert('未查询到数据，请检查股票代码格式');
            }
        } catch (e) {
            alert('查询失败：' + e.message);
        }
    }

    document.getElementById('searchInput').addEventListener('input', renderTable);

    document.getElementById('stockTableBody').addEventListener('click', function(e) {
        const tr = e.target.closest('tr');
        if (!tr) return;
        const rows = Array.from(tr.parentElement.children);
        const idx = rows.indexOf(tr);
        const filtered = applyFilter(allData);
        if (idx >= 0 && idx < filtered.length) {
            showDetail(filtered[idx]);
        }
    });

    fetchData();
    fetchRecommend();
    autoRefreshTimer = setInterval(() => {
        fetchData();
    }, 5000);

    // ===== 详情面板 =====
    let klineChart = null;

    function showDetail(d) {
        const isIdx = d.type === 'index';
        const cls = priceClass(d.change);
        const codeDisplay = d.code ? d.code.replace(/^s_/i, '') : '';
        const displayName = d.display_name || d.name || '--';
        const changeSign = d.change > 0 ? '+' : '';
        const pctSign = d.change_pct > 0 ? '+' : '';

        let items = '';
        let depthHtml = '';

        if (isIdx) {
            items = `
                <div class="detail-item"><div class="label">最新价</div><div class="value ${cls}">${(d.price||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">涨跌额</div><div class="value ${cls}">${changeSign}${(d.change||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">涨跌幅</div><div class="value ${cls}">${pctSign}${(d.change_pct||0).toFixed(2)}%</div></div>
                <div class="detail-item"><div class="label">成交量(手)</div><div class="value">${formatAmount(d.volume)}</div></div>
                <div class="detail-item"><div class="label">成交额</div><div class="value">${formatAmount(d.amount)}</div></div>`;
        } else {
            depthHtml = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;">';
            depthHtml += '<div style="flex:1;min-width:180px;background:var(--bg);border-radius:8px;padding:10px;">';
            depthHtml += '<div style="color:#ff5252;font-size:12px;font-weight:600;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:4px;">卖盘</div>';
            for (let i = 5; i >= 1; i--) {
                const p = d['sell' + i + '_price'] || 0;
                const v = d['sell' + i + '_vol'] || 0;
                depthHtml += '<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:12px;">';
                depthHtml += '<span style="color:#999;">卖' + i + '</span>';
                depthHtml += '<span style="color:#ff5252;">' + (p ? p.toFixed(2) : '--') + '</span>';
                depthHtml += '<span style="color:#ccc;">' + (v ? (v/100).toFixed(0) : '--') + '手</span>';
                depthHtml += '</div>';
            }
            depthHtml += '</div>';
            depthHtml += '<div style="flex:1;min-width:180px;background:var(--bg);border-radius:8px;padding:10px;">';
            depthHtml += '<div style="color:#4caf50;font-size:12px;font-weight:600;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:4px;">买盘</div>';
            for (let i = 1; i <= 5; i++) {
                const p = d['buy' + i + '_price'] || 0;
                const v = d['buy' + i + '_vol'] || 0;
                depthHtml += '<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:12px;">';
                depthHtml += '<span style="color:#999;">买' + i + '</span>';
                depthHtml += '<span style="color:#4caf50;">' + (p ? p.toFixed(2) : '--') + '</span>';
                depthHtml += '<span style="color:#ccc;">' + (v ? (v/100).toFixed(0) : '--') + '手</span>';
                depthHtml += '</div>';
            }
            depthHtml += '</div></div>';

            items = `
                <div class="detail-item"><div class="label">最新价</div><div class="value ${cls}">${(d.price||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">涨跌额</div><div class="value ${cls}">${changeSign}${(d.change||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">涨跌幅</div><div class="value ${cls}">${pctSign}${(d.change_pct||0).toFixed(2)}%</div></div>
                <div class="detail-item"><div class="label">竞买价</div><div class="value">${(d.bid||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">竞卖价</div><div class="value">${(d.ask||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">今开</div><div class="value">${(d.open||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">昨收</div><div class="value">${(d.prev_close||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">最高</div><div class="value up">${(d.high||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">最低</div><div class="value down">${(d.low||0).toFixed(2)}</div></div>
                <div class="detail-item"><div class="label">成交量</div><div class="value">${formatVolume(d.volume)}</div></div>
                <div class="detail-item"><div class="label">成交额</div><div class="value">${formatAmount(d.amount)}</div></div>
                <div class="detail-item"><div class="label">日期</div><div class="value">${d.date||'--'}</div></div>
                <div class="detail-item"><div class="label">时间</div><div class="value">${d.time||'--'}</div></div>`;
        }

        const chartHtml = isIdx
            ? `<div class="chart-container"><div class="chart-error">指数暂不支持K线图</div></div>`
            : `<div class="chart-container" id="klineChart" style="height:400px;margin-top:20px;border-top:1px solid var(--border);padding-top:16px;"></div>`;

        document.getElementById('detailContent').innerHTML = `
            <h2>${displayName}</h2>
            <div class="detail-code">${codeDisplay} ${isIdx ? '· 指数' : '· 个股'}</div>
            <div class="detail-price ${cls}">${(d.price||0).toFixed(2)}</div>
            <div class="detail-change">
                <span class="${cls}">${changeSign}${(d.change||0).toFixed(2)}</span>
                <span class="${cls}">${pctSign}${(d.change_pct||0).toFixed(2)}%</span>
            </div>
            <div class="detail-grid">${items}</div>
            ${depthHtml}
            ${chartHtml}`;

        document.getElementById('detailPanel').classList.add('open');
        document.getElementById('detailOverlay').classList.add('show');

        if (!isIdx) {
            loadKlineChart(d.code);
        }
    }

    async function loadKlineChart(code) {
        const chartDom = document.getElementById('klineChart');
        if (!chartDom) return;
        chartDom.innerHTML = '<div class="chart-loading">⏳ 加载K线数据中...</div>';
        try {
            const resp = await fetch('/api/kline?code=' + encodeURIComponent(code));
            const json = await resp.json();
            if (json.error) {
                chartDom.innerHTML = '<div class="chart-error">' + json.error + '</div>';
                return;
            }
            const rawData = json.data;
            const volumes = json.volumes;
            const dates = rawData.map(row => row[0]);
            chartDom.innerHTML = '';
            if (klineChart) { klineChart.dispose(); klineChart = null; }
            klineChart = echarts.init(chartDom, 'dark');
            const ohlcData = rawData.map(row => [row[1], row[3], row[2], row[4]]);
            const series = [{
                name: 'K线', type: 'candlestick', data: ohlcData,
                itemStyle: { color: '#f85149', color0: '#3fb950', borderColor: '#f85149', borderColor0: '#3fb950' },
            }];
            const maConfigs = [
                { key: 'ma5', name: 'MA5', color: '#f0c040' },
                { key: 'ma10', name: 'MA10', color: '#58a6ff' },
                { key: 'ma20', name: 'MA20', color: '#bc8cff' },
                { key: 'ma60', name: 'MA60', color: '#ff9d5c' },
            ];
            maConfigs.forEach(cfg => {
                const ma = json[cfg.key];
                if (ma && ma.length > 0) {
                    const maPairs = dates.map((d, i) => ma[i] != null ? [d, ma[i]] : [d, '-']);
                    series.push({ name: cfg.name, type: 'line', data: maPairs, smooth: true, showSymbol: false, lineStyle: { color: cfg.color, width: 1 }, itemStyle: { color: cfg.color } });
                }
            });
            const option = {
                backgroundColor: '#1a1d2e',
                grid: [{ left: 50, right: 16, top: 20, height: '70%' }],
                xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2d3e' } }, axisTick: { lineStyle: { color: '#2a2d3e' } }, axisLabel: { color: '#8b949e', fontSize: 10 } },
                yAxis: { scale: true, splitLine: { lineStyle: { color: '#2a2d3e', type: 'dashed' } }, axisLabel: { color: '#8b949e', fontSize: 10 } },
                tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#1a1d2e', borderColor: '#2a2d3e', textStyle: { color: '#e1e4e8' } },
                legend: { data: ['K线', 'MA5', 'MA10', 'MA20', 'MA60'], top: 0, left: 'center', textStyle: { color: '#8b949e', fontSize: 10 }, itemWidth: 14, itemHeight: 8 },
                series: series,
            };
            klineChart.setOption(option);
            window.addEventListener('resize', () => { if (klineChart) klineChart.resize(); });
        } catch (e) {
            console.error('K线加载失败:', e);
            chartDom.innerHTML = '<div class="chart-error">K线数据加载失败: ' + e.message + '</div>';
        }
    }

    function hideDetail() {
        document.getElementById('detailPanel').classList.remove('open');
        document.getElementById('detailOverlay').classList.remove('show');
    }
    document.getElementById('detailOverlay').addEventListener('click', hideDetail);

    // ===== 推荐面板渲染 =====
    async function fetchRecommend() {
        try {
            const resp = await fetch('/api/recommend');
            const json = await resp.json();
            recommendData = json;
            renderRecPanel('longList', json.long_term || []);
            renderRecPanel('shortList', json.short_term || []);
        } catch (e) {
            console.error('推荐数据加载失败:', e);
        }
    }

    function formatMc(val) {
        if (!val) return '--';
        const v = parseFloat(val);
        if (v >= 1e8) return (v / 1e8).toFixed(0) + '亿';
        if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
        return v.toFixed(0);
    }

    function renderRecPanel(containerId, data) {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (!data || data.length === 0) {
            container.innerHTML = '<div class="rec-loading">暂无推荐数据</div>';
            return;
        }
        container.innerHTML = data.map(d => {
            const cls = (d.change_pct || 0) >= 0 ? 'up' : 'down';
            const sign = (d.change_pct || 0) >= 0 ? '+' : '';
            const codeDisplay = d.code ? d.code.replace(/^s_/i, '') : '';
            const analysis = d.analysis || {};
            const sigColor = analysis.color || '#8b949e';
            const sigLabel = analysis.label || '--';
            const trade = d._trade || {};

            // PE / PB / 市值 标签
            let tagsHtml = '';
            if (analysis.pe != null) {
                tagsHtml += '<span class="rec-tag" style="color:#78b8ff;">PE ' + analysis.pe.toFixed(1) + '</span>';
            }
            if (analysis.pb != null) {
                tagsHtml += '<span class="rec-tag" style="color:#b48aff;">PB ' + analysis.pb.toFixed(2) + '</span>';
            }
            if (d.market_cap != null) {
                tagsHtml += '<span class="rec-tag">市值 ' + formatMc(d.market_cap) + '</span>';
            }

            // 交易价格建议行
            let tradeHtml = '';
            if (trade.buy_price != null) {
                tradeHtml = `
                <div class="rec-trade-prices">
                    <div class="rec-trade-item">
                        <div class="rec-trade-label">建议买入</div>
                        <div class="rec-trade-value buy">${trade.buy_price.toFixed(2)}</div>
                    </div>
                    <div class="rec-trade-item">
                        <div class="rec-trade-label">目标卖出</div>
                        <div class="rec-trade-value sell">${trade.sell_price.toFixed(2)}</div>
                    </div>
                    <div class="rec-trade-item">
                        <div class="rec-trade-label">止损价</div>
                        <div class="rec-trade-value stop">${trade.stop_loss.toFixed(2)}</div>
                    </div>
                </div>`;
            }

            const inWatchlist = isInWatchlist(codeDisplay);
            return `<div class="rec-card" data-code="${codeDisplay}" style="position:relative;">
                <button class="rec-card-add ${inWatchlist ? 'in-watchlist' : ''}" data-code="${codeDisplay}" title="${inWatchlist ? '已加入自选' : '加入自选'}">${inWatchlist ? '✓' : '+'}</button>
                <div class="rec-card-top">
                    <span class="rec-card-name">${d.display_name || d.name || '--'}</span>
                    <span class="rec-card-code">${codeDisplay}</span>
                </div>
                <div class="rec-card-price-row">
                    <span class="rec-card-price ${cls}">${(d.price || 0).toFixed(2)}</span>
                    <span class="rec-card-change ${cls}">${sign}${(d.change_pct || 0).toFixed(2)}%</span>
                </div>
                <div class="rec-signal-row">
                    <span class="rec-signal-badge" style="background:${sigColor}22;color:${sigColor};border:1px solid ${sigColor}44">${sigLabel}</span>
                </div>
                ${tradeHtml}
                <div class="rec-tags">${tagsHtml}</div>
            </div>`;
        }).join('');
    }

    // 推荐面板点击 - 显示详情 / 加自选
    document.getElementById('longList').addEventListener('click', function(e) {
        const addBtn = e.target.closest('.rec-card-add');
        if (addBtn) {
            e.stopPropagation();
            toggleWatchlist(addBtn.dataset.code);
            return;
        }
        const card = e.target.closest('.rec-card');
        if (!card) return;
        const code = card.dataset.code;
        const stock = recommendData.long_term.find(s => (s.code || '').replace(/^s_/i, '') === code);
        if (stock) showDetail({ ...stock, type: 'stock', display_name: stock.name, code: stock.code });
    });
    document.getElementById('shortList').addEventListener('click', function(e) {
        const addBtn = e.target.closest('.rec-card-add');
        if (addBtn) {
            e.stopPropagation();
            toggleWatchlist(addBtn.dataset.code);
            return;
        }
        const card = e.target.closest('.rec-card');
        if (!card) return;
        const code = card.dataset.code;
        const stock = recommendData.short_term.find(s => (s.code || '').replace(/^s_/i, '') === code);
        if (stock) showDetail({ ...stock, type: 'stock', display_name: stock.name, code: stock.code });
    });

    // ===== AI 模拟交易前端逻辑 =====
    let lastTradeTick = 0;

    async function fetchTradingStatus() {
        try {
            const resp = await fetch('/api/trading/status');
            const data = await resp.json();
            // 更新余额面板
            const bal = document.getElementById('accountBalance');
            const pnlCls = data.total_pnl >= 0 ? 'up' : 'down';
            const pnlSign = data.total_pnl >= 0 ? '+' : '';
            bal.innerHTML = `
                <div class="trading-bal-item"><div class="lbl">可用资金</div><div class="val">¥${(data.cash||0).toLocaleString('zh-CN', {minimumFractionDigits:2})}</div></div>
                <div class="trading-bal-item"><div class="lbl">持仓市值</div><div class="val">¥${(data.total_market_value||0).toLocaleString('zh-CN', {minimumFractionDigits:2})}</div></div>
                <div class="trading-bal-item"><div class="lbl">总资产</div><div class="val">¥${(data.total_assets||0).toLocaleString('zh-CN', {minimumFractionDigits:2})}</div></div>
                <div class="trading-bal-item"><div class="lbl">总盈亏</div><div class="val ${pnlCls}">${pnlSign}${(data.total_pnl||0).toLocaleString('zh-CN', {minimumFractionDigits:2})} (${pnlSign}${(data.total_pnl_pct||0).toFixed(2)}%)</div></div>
            `;
            // 上次决策时间
            document.getElementById('lastDecision').textContent = '上次决策：' + (data.last_decision || '--');
            // 持仓列表
            const hList = document.getElementById('holdingsList');
            if (data.holdings && data.holdings.length > 0) {
                hList.innerHTML = data.holdings.map(h => {
                    const pnlCls2 = h.pnl >= 0 ? 'win' : 'loss';
                    const pnlSign2 = h.pnl >= 0 ? '+' : '';
                    return `<div class="trade-row">
                        <span class="act buy">持仓</span>
                        <span class="info"><span class="nm">${h.name}</span><span class="cd">${h.code}</span></span>
                        <span class="num">${h.shares}股</span>
                        <span class="num">成本${h.avg_cost.toFixed(2)}</span>
                        <span class="num">现价${h.curr_price.toFixed(2)}</span>
                        <span class="pnl ${pnlCls2}">${pnlSign2}${h.pnl_pct.toFixed(2)}%</span>
                    </div>`;
                }).join('');
            } else {
                hList.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-secondary);font-size:12px;">暂无持仓，点击 AI 决策选股</div>';
            }
            // 交易历史
            const histList = document.getElementById('historyList');
            if (data.history && data.history.length > 0) {
                histList.innerHTML = data.history.reverse().map(h => {
                    const isBuy = h.action === '买入';
                    const pnlStr = h.pnl_pct !== undefined ? ((h.pnl_pct>=0?'+':'')+h.pnl_pct.toFixed(2)+'%') : '';
                    return `<div class="trade-row">
                        <span class="act ${isBuy?'buy':'sell'}">${h.action}</span>
                        <span class="info"><span class="nm">${h.name}</span><span class="cd">${h.code}</span><br><span class="rs">${h.reason||''}</span></span>
                        <span class="num">${h.shares}股 @${h.price.toFixed?h.price.toFixed(2):h.price}</span>
                        <span class="pnl ${isBuy?'win':'loss'}">${pnlStr}</span>
                        <span style="font-size:10px;color:var(--text-secondary)">${h.time||''}</span>
                    </div>`;
                }).join('');
            } else {
                histList.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-secondary);font-size:12px;">暂无交易记录</div>';
            }

            // 更新回测面板的持仓列表
            populatePortfolioBacktestCodes(data.holdings);
        } catch (e) {
            console.error('交易状态加载失败:', e);
        }
    }

    async function tickHoldings() {
        // 每 5s 更新持仓市值
        if (Date.now() - lastTradeTick < 5000) return;
        lastTradeTick = Date.now();
        try {
            await fetch('/api/trading/tick');
        } catch (e) {}
    }

    async function aiDecide() {
        // 显示加载
        const overlay = document.getElementById('decideOverlay');
        const panel = document.getElementById('decidePanel');
        overlay.style.display = 'block';
        panel.style.display = 'block';
        panel.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">🤖 AI 正在分析市场数据...</div>';
        try {
            const resp = await fetch('/api/trading/decide');
            const data = await resp.json();
            const d = data.decisions || {};
            let html = '<h3 style="margin-bottom:4px;">🤖 AI 决策结果</h3>';
            html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;">' + (data.time||'') + '</div>';
            html += '<div style="padding:8px 12px;background:var(--bg);border-radius:8px;margin-bottom:12px;font-size:13px;">' + (d.summary||'') + '</div>';
            // 卖出
            if (d.sell && d.sell.length > 0) {
                html += '<div style="font-size:13px;font-weight:600;color:#f85149;margin-bottom:6px;">📤 卖出信号</div>';
                d.sell.forEach(s => {
                    html += '<div style="font-size:12px;padding:4px 8px;margin-bottom:4px;border-left:3px solid #f85149;background:rgba(248,81,73,0.05);">';
                    html += '<b>' + s.name + '</b> (' + s.code + ') ' + s.shares + '股 @' + s.price.toFixed(2);
                    html += ' <span style="color:#f85149;">' + (s.pnl_pct>=0?'+':'') + s.pnl_pct.toFixed(2) + '%</span>';
                    html += ' <span style="color:var(--text-secondary);font-size:10px;">' + s.reason + '</span></div>';
                });
            }
            // 买入
            if (d.buy && d.buy.length > 0) {
                html += '<div style="font-size:13px;font-weight:600;color:#4caf50;margin-bottom:6px;margin-top:8px;">📥 买入信号</div>';
                d.buy.forEach(b => {
                    const reasons = (b.reasons||[]).join(', ');
                    html += '<div style="font-size:12px;padding:4px 8px;margin-bottom:4px;border-left:3px solid #4caf50;background:rgba(76,175,80,0.05);">';
                    html += '<b>' + b.name + '</b> (' + b.code + ') ' + b.shares + '股 @' + b.price.toFixed(2);
                    html += ' ¥' + b.amount.toLocaleString();
                    html += ' <span style="color:#f0c040;font-size:10px;">评分' + b.score + '</span>';
                    html += ' <span style="color:var(--text-secondary);font-size:10px;">' + reasons + '</span></div>';
                });
            }
            if ((!d.sell || d.sell.length===0) && (!d.buy || d.buy.length===0)) {
                html += '<div style="text-align:center;padding:20px;color:var(--text-secondary);">当前无需调仓</div>';
            }
            html += '<div style="text-align:center;margin-top:16px;"><button class="trade-btn decide" onclick="closeDecide()">关闭</button></div>';
            panel.innerHTML = html;
            // 刷新状态
            fetchTradingStatus();
        } catch (e) {
            panel.innerHTML = '<div style="text-align:center;padding:40px;color:#f85149;">决策失败: ' + e.message + '</div>';
        }
    }

    function closeDecide() {
        document.getElementById('decideOverlay').style.display = 'none';
        document.getElementById('decidePanel').style.display = 'none';
    }
    document.getElementById('decideOverlay').addEventListener('click', closeDecide);

    async function resetTrading() {
        if (!confirm('确定要重置账户吗？所有持仓和历史记录将清空。')) return;
        try {
            await fetch('/api/trading/reset');
            fetchTradingStatus();
        } catch (e) {
            alert('重置失败: ' + e.message);
        }
    }

    // ===== 策略回测前端逻辑 =====

    function populatePortfolioBacktestCodes(holdings) {
        const select = document.getElementById('btCode');
        const quickBtns = document.getElementById('btQuickBtns');
        if (!select || !quickBtns) return;

        // 保存当前选中的值
        const currentVal = select.value;

        // 清空并重建下拉选项
        select.innerHTML = '<option value="">-- 选择持仓股票 --</option>';
        quickBtns.innerHTML = '<span style="font-size:10px;color:var(--text-secondary);line-height:24px;">快速回测：</span>';

        if (holdings && holdings.length > 0) {
            holdings.forEach(h => {
                const displayCode = h.code;
                const displayName = h.name || h.code;
                const option = document.createElement('option');
                option.value = displayCode;
                option.textContent = displayName + ' (' + displayCode + ')';
                select.appendChild(option);

                // 同时生成快速回测按钮
                const btn = document.createElement('button');
                btn.textContent = h.name || h.code;
                btn.style.cssText = 'padding:3px 10px;background:var(--bg);border:1px solid var(--border);border-radius:12px;color:var(--text-secondary);cursor:pointer;font-size:10px;';
                btn.onmouseenter = function() { this.style.borderColor = '#bc8cff'; this.style.color = '#bc8cff'; };
                btn.onmouseleave = function() { this.style.borderColor = ''; this.style.color = ''; };
                btn.onclick = function() { quickBacktest(displayCode); };
                quickBtns.appendChild(btn);
            });

            // 恢复之前选中的值
            if (currentVal && Array.from(select.options).some(o => o.value === currentVal)) {
                select.value = currentVal;
            }
        } else {
            quickBtns.innerHTML += '<span style="font-size:11px;color:var(--text-secondary);line-height:24px;">暂无持仓</span>';
        }
    }

    function formatNumShort(n) {
        if (n == null) return '--';
        if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
        if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(0) + '万';
        return Number(n).toFixed(2);
    }

    async function runBacktest() {
        const codeInput = document.getElementById('btCode').value.trim();
        const start = document.getElementById('btStart').value;
        const end = document.getElementById('btEnd').value;
        const capitalWan = parseFloat(document.getElementById('btCapital').value) || 100;
        const capital = capitalWan * 10000;
        const btn = document.getElementById('btRunBtn');
        const resultsDiv = document.getElementById('btResults');

        if (!codeInput) {
            resultsDiv.innerHTML = '<div class="bt-error">请输入股票代码</div>';
            return;
        }

        btn.disabled = true;
        btn.textContent = '⏳ 运行中...';
        resultsDiv.innerHTML = '<div class="bt-loading">⏳ 正在回测，从 baostock 获取历史数据...</div>';

        try {
            const params = new URLSearchParams({ code: codeInput, start, end, capital });
            const resp = await fetch('/api/backtest?' + params.toString());
            const data = await resp.json();

            if (data.error) {
                resultsDiv.innerHTML = '<div class="bt-error">' + data.error + '</div>';
                return;
            }

            const pnlCls = data.total_return >= 0 ? 'up' : 'down';
            const pnlSign = data.total_return >= 0 ? '+' : '';
            const bhCls = data.buy_hold_return_pct >= 0 ? 'up' : 'down';
            const bhSign = data.buy_hold_return_pct >= 0 ? '+' : '';

            let html = '<div class="bt-summary">';
            html += '<div class="bt-summary-item"><div class="lbl">初始资金</div><div class="val">' + formatNumShort(data.initial_capital) + '</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">最终权益</div><div class="val ' + pnlCls + '">' + formatNumShort(data.final_equity) + '</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">策略收益</div><div class="val ' + pnlCls + '">' + pnlSign + data.total_return_pct.toFixed(2) + '%</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">买入持有</div><div class="val ' + bhCls + '">' + bhSign + data.buy_hold_return_pct.toFixed(2) + '%</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">胜率</div><div class="val">' + data.win_rate.toFixed(1) + '%</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">最大回撤</div><div class="val down">' + data.max_drawdown_pct.toFixed(2) + '%</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">夏普比率</div><div class="val">' + data.sharpe_ratio.toFixed(2) + '</div></div>';
            html += '<div class="bt-summary-item"><div class="lbl">交易次数(买/卖)</div><div class="val">' + data.buy_count + '/' + data.sell_count + '</div></div>';
            html += '</div>';

            if (data.avg_win) {
                html += '<div style="font-size:10px;color:var(--text-secondary);margin-bottom:8px;">';
                html += '平均盈利: ¥' + data.avg_win.toFixed(0) + ' | 平均亏损: ¥' + data.avg_loss.toFixed(0);
                html += ' | 数据: ' + data.data_points + ' 根K线';
                html += '</div>';
            }

            // 交易明细
            if (data.trades && data.trades.length > 0) {
                html += '<div class="bt-detail"><div style="font-size:12px;font-weight:600;margin-bottom:6px;">📋 交易明细（最近30笔）</div>';
                html += '<table><thead><tr><th>日期</th><th>操作</th><th>价格</th><th>股数</th><th>盈亏</th><th>原因</th></tr></thead><tbody>';
                data.trades.forEach(t => {
                    const isBuy = t.action.includes('买入');
                    const actCls = isBuy ? 'buy' : 'sell';
                    const pnlCls2 = t.pnl > 0 ? 'up' : (t.pnl < 0 ? 'down' : '');
                    const pnlSign2 = t.pnl > 0 ? '+' : '';
                    html += '<tr>';
                    html += '<td>' + (t.date||'') + '</td>';
                    html += '<td><span class="act-tag ' + actCls + '">' + t.action + '</span></td>';
                    html += '<td>' + (t.price||0).toFixed(2) + '</td>';
                    html += '<td>' + (t.shares||0) + '</td>';
                    html += '<td class="' + pnlCls2 + '">' + pnlSign2 + (t.pnl||0).toFixed(0) + (t.pnl_pct ? ' (' + pnlSign2 + t.pnl_pct.toFixed(1) + '%)' : '') + '</td>';
                    html += '<td style="font-size:10px;">' + (t.reason||'') + '</td>';
                    html += '</tr>';
                });
                html += '</tbody></table></div>';
            }

            // 权益曲线图
            if (data.equity_curve && data.equity_curve.length > 1) {
                html += '<div class="bt-equity-chart" id="backtestChart"></div>';
            }

            resultsDiv.innerHTML = html;

            // 绘制权益曲线
            if (data.equity_curve && data.equity_curve.length > 1) {
                setTimeout(() => renderBacktestChart(data.equity_curve, data), 100);
            }
        } catch (e) {
            resultsDiv.innerHTML = '<div class="bt-error">回测请求失败: ' + e.message + '</div>';
        } finally {
            btn.disabled = false;
            btn.textContent = '▶ 运行回测';
        }
    }

    function renderBacktestChart(curve, meta) {
        const dom = document.getElementById('backtestChart');
        if (!dom) return;
        if (window._btChart) { window._btChart.dispose(); }
        const chart = echarts.init(dom, 'dark');
        window._btChart = chart;
        const dates = curve.map(p => p.date);
        const equity = curve.map(p => p.equity);

        const option = {
            backgroundColor: '#1a1d2e',
            grid: { left: 60, right: 20, top: 20, bottom: 30 },
            xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2d3e' } }, axisLabel: { color: '#8b949e', fontSize: 9 } },
            yAxis: {
                type: 'value',
                splitLine: { lineStyle: { color: '#2a2d3e', type: 'dashed' } },
                axisLabel: { color: '#8b949e', fontSize: 9, formatter: v => (v / 10000).toFixed(0) + '万' },
            },
            tooltip: {
                trigger: 'axis',
                backgroundColor: '#1a1d2e',
                borderColor: '#2a2d3e',
                textStyle: { color: '#e1e4e8' },
                formatter: params => {
                    const p = params[0];
                    return p.axisValue + '<br/>权益: ¥' + p.value.toLocaleString('zh-CN', { minimumFractionDigits: 0 });
                }
            },
            series: [{
                name: '权益曲线',
                type: 'line',
                data: equity,
                smooth: false,
                showSymbol: false,
                lineStyle: { color: '#bc8cff', width: 2 },
                areaStyle: { color: new echarts.graphic.LinearGradient(0,0,0,1,
                    [{offset:0, color:'rgba(188,140,255,0.3)'},{offset:1, color:'rgba(188,140,255,0.02)'}]
                )}
            }],
        };
        chart.setOption(option);
        window.addEventListener('resize', () => { if (window._btChart) window._btChart.resize(); });
    }

    function quickBacktest(code) {
        document.getElementById('btCode').value = code;
        // 默认近2年
        const now = new Date();
        const twoYrAgo = new Date(now);
        twoYrAgo.setFullYear(now.getFullYear() - 2);
        document.getElementById('btStart').value = twoYrAgo.toISOString().split('T')[0];
        document.getElementById('btEnd').value = now.toISOString().split('T')[0];
        runBacktest();
    }

    // ===== 自选股功能 =====
    const WL_KEY = 'stock_watchlist_codes';

    function getWatchlist() {
        try {
            return JSON.parse(localStorage.getItem(WL_KEY) || '[]');
        } catch(e) { return []; }
    }

    function saveWatchlist(codes) {
        localStorage.setItem(WL_KEY, JSON.stringify(codes));
    }

    function isInWatchlist(code) {
        return getWatchlist().includes(code);
    }

    function toggleWatchlist(code) {
        const codes = getWatchlist();
        const idx = codes.indexOf(code);
        if (idx >= 0) {
            codes.splice(idx, 1);
        } else {
            codes.push(code);
        }
        saveWatchlist(codes);
        // 刷新推荐面板的加号状态
        if (recommendData) {
            renderRecPanel('longList', recommendData.long_term || []);
            renderRecPanel('shortList', recommendData.short_term || []);
        }
        renderWatchlist();
    }

    function renderWatchlist() {
        const codes = getWatchlist();
        const grid = document.getElementById('watchlistGrid');
        const countEl = document.getElementById('wlCount');
        countEl.textContent = codes.length > 0 ? '共 ' + codes.length + ' 只' : '';

        if (codes.length === 0) {
            grid.innerHTML = '<div class="watchlist-empty">📭 暂无自选股<br><span style="font-size:10px;">点击推荐面板中的 <b>+</b> 按钮添加</span></div>';
            return;
        }

        // 尝试从 allData 或 recommendData 中找到价格
        const allSources = [
            ...(allData || []),
            ...(recommendData.long_term || []),
            ...(recommendData.short_term || []),
        ];

        grid.innerHTML = codes.map(code => {
            let info = allSources.find(s => {
                const c = (s.code || '').replace(/^s_/i, '');
                return c === code;
            });
            const name = (info ? (info.display_name || info.name) : code) || code;
            const price = info ? (info.price || 0) : 0;
            const chgPct = info ? (info.change_pct || 0) : 0;
            const cls = chgPct > 0 ? 'up' : (chgPct < 0 ? 'down' : '');
            const sign = chgPct > 0 ? '+' : '';
            return `<div class="watchlist-card" data-code="${code}" onclick="showWatchlistDetail('${code}')">
                <div class="watchlist-card-info">
                    <div class="watchlist-card-name">${name}</div>
                    <div class="watchlist-card-code">${code} <span class="${cls}" style="margin-left:8px;">${price > 0 ? price.toFixed(2) : '--'}</span> <span class="${cls}" style="font-size:11px;">${price > 0 ? sign + chgPct.toFixed(2) + '%' : ''}</span></div>
                </div>
                <button class="watchlist-card-remove" data-code="${code}">✕</button>
            </div>`;
        }).join('');

        // 绑定删除按钮事件
        grid.querySelectorAll('.watchlist-card-remove').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleWatchlist(this.dataset.code);
            });
        });
    }

    function showWatchlistDetail(code) {
        const allSources = [
            ...(allData || []),
            ...(recommendData.long_term || []),
            ...(recommendData.short_term || []),
        ];
        let stock = allSources.find(s => {
            const c = (s.code || '').replace(/^s_/i, '');
            return c === code;
        });
        if (stock) {
            showDetail({ ...stock, type: stock.type || 'stock', display_name: stock.display_name || stock.name, code: stock.code || code });
        } else {
            // 如果找不到，尝试查询
            const prefix = code.startsWith('6') || code.startsWith('68') ? 'sh' : 'sz';
            fetch('/api/query?codes=' + encodeURIComponent(prefix + code))
                .then(r => r.json())
                .then(json => {
                    if (json.data && json.data.length > 0) {
                        const d = json.data[0];
                        showDetail({ ...d, type: d.type || 'stock', display_name: d.name, code: d.code });
                    }
                })
                .catch(() => {});
        }
    }

    // 初始渲染自选股
    renderWatchlist();

    // 初始加载 & 定时刷新
    fetchTradingStatus();
    setInterval(() => {
        tickHoldings();
        fetchTradingStatus();
        renderWatchlist();
    }, 5000);
</script>
</body>
</html>
"""

def _auto_trade_loop():
    """后台线程：每隔 N 分钟自动执行 AI 决策"""
    INTERVAL_SECONDS = 5 * 60  # 5 分钟
    _time.sleep(30)  # 启动后等待 30 秒让缓存初始化完成
    print("[AUTO-TRADE] AI 自动交易线程已启动，间隔 {} 分钟".format(INTERVAL_SECONDS // 60))
    while True:
        try:
            # 确保推荐缓存可用
            if not _cache_is_valid():
                result = gen_recommend_stocks()
                _update_cache({
                    "long_term": result.get("long_term", []),
                    "short_term": result.get("short_term", []),
                    "update_time": _now_str()
                })
            recommended = RECOMMEND_CACHE["data"].get("long_term", []) + \
                          RECOMMEND_CACHE["data"].get("short_term", [])

            if recommended:
                decisions = _ai_trade_decision(recommended)
                # 执行卖出
                for s in decisions["sell"]:
                    code = s["code"]
                    if code in TRADING_STATE["holdings"]:
                        h = TRADING_STATE["holdings"].pop(code)
                        TRADING_STATE["cash"] += s["amount"]
                        TRADING_STATE["total_invested"] -= h["shares"] * h["avg_cost"]
                        TRADING_STATE["history"].append({
                            "time": _now_str(), "action": "卖出", "code": code,
                            "name": s["name"], "shares": s["shares"],
                            "price": s["price"], "amount": s["amount"],
                            "pnl_pct": s.get("pnl_pct", 0), "reason": s["reason"],
                        })
                # 执行买入
                for b in decisions["buy"]:
                    code = b["code"]
                    if TRADING_STATE["cash"] >= b["amount"]:
                        TRADING_STATE["cash"] -= b["amount"]
                        TRADING_STATE["holdings"][code] = {
                            "name": b["name"], "shares": b["shares"],
                            "avg_cost": b["price"], "buy_date": _get_today_str(),
                            "curr_price": b["price"],
                        }
                        TRADING_STATE["total_invested"] += b["amount"]
                        TRADING_STATE["history"].append({
                            "time": _now_str(), "action": "买入", "code": code,
                            "name": b["name"], "shares": b["shares"],
                            "price": b["price"], "amount": b["amount"],
                            "reason": "AI评分 " + str(b.get("score", 0)),
                        })
                TRADING_STATE["last_decision"] = _now_str()
                _save_trading_state()
                b_n = len(decisions["buy"])
                s_n = len(decisions["sell"])
                if b_n or s_n:
                    print(f"[AUTO-TRADE] 自动执行: 买{b_n} 卖{s_n} | {_now_str()}")
        except Exception as e:
            print(f"[AUTO-TRADE] 错误: {e}")
        _time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 50)
    print("  股票行情浏览网页 - 新浪数据源")
    print(f"  启动地址: http://0.0.0.0:{port}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 50)
    # 启动 AI 自动交易后台线程
    auto_trade_thread = threading.Thread(target=_auto_trade_loop, daemon=True)
    auto_trade_thread.start()
    app.run(host="0.0.0.0", port=port, debug=False)
