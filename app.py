import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import sqlite3
import datetime
import pytz
from openai import OpenAI

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Robinhood Sim v4", layout="wide", page_icon="💸")

# Custom CSS
st.markdown("""
    <style>
    .big-font { font-size: 50px !important; font-weight: 600; color: #00c805; }
    .negative { color: #ff5000; }
    .metric-label { font-size: 14px; color: #696969; }
    </style>
    """, unsafe_allow_html=True)

# --- CREDENTIALS ---
if "XAI_API_KEY" in st.secrets:
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
else:
    st.error("⚠️ XAI_API_KEY is missing!")
    st.stop()

client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

# --- DATABASE ---
DB_FILE = "robinhood_v4.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio
                 (ticker TEXT PRIMARY KEY, shares REAL, avg_cost REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS balance
                 (id INTEGER PRIMARY KEY, cash REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (timestamp TEXT, equity REAL)''')
    
    # Initialize cash if new
    c.execute('SELECT cash FROM balance WHERE id=1')
    if c.fetchone() is None:
        c.execute('INSERT INTO balance (id, cash) VALUES (1, 10000.00)')
    conn.commit()
    conn.close()

def get_cash():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT cash FROM balance WHERE id=1')
    res = c.fetchone()
    conn.close()
    return res[0] if res else 10000.00

def update_cash(amount):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE balance SET cash = ? WHERE id=1', (amount,))
    conn.commit()
    conn.close()

def add_trade(ticker, shares, price, side):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT shares, avg_cost FROM portfolio WHERE ticker=?', (ticker,))
    row = c.fetchone()
    
    if side == "BUY":
        if row:
            curr_shares, curr_avg = row
            total_cost = (curr_shares * curr_avg) + (shares * price)
            new_shares = curr_shares + shares
            new_avg = total_cost / new_shares
            c.execute('UPDATE portfolio SET shares=?, avg_cost=? WHERE ticker=?', (new_shares, new_avg, ticker))
        else:
            c.execute('INSERT INTO portfolio (ticker, shares, avg_cost) VALUES (?, ?, ?)', (ticker, shares, price))
    elif side == "SELL":
        if row:
            curr_shares, curr_avg = row
            new_shares = curr_shares - shares
            if new_shares <= 0:
                c.execute('DELETE FROM portfolio WHERE ticker=?', (ticker,))
            else:
                c.execute('UPDATE portfolio SET shares=? WHERE ticker=?', (new_shares, ticker))
    conn.commit()
    conn.close()

def get_portfolio():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql('SELECT * FROM portfolio', conn)
    conn.close()
    return df

def log_history(equity):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    last_log = c.execute('SELECT timestamp FROM history ORDER BY timestamp DESC LIMIT 1').fetchone()
    now = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d %H:%M")
    
    should_log = True
    if last_log:
        last_time = last_log[0]
        # This was the error spot - fixed indentation below
        if now[:13] == last_time[:13]: 
            should_log = False
            
    if should_log:
        c.execute('INSERT INTO history (timestamp, equity) VALUES (?, ?)', (now, equity))
        conn.commit()
    conn.close()

init_db()

# --- DATA FETCHING (OPTIMIZED) ---
@st.cache_data(ttl=3600)
def load_ticker_list():
    """Fetches list of tickers, cached for 1 hour."""
    try:
        url = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/all/all_tickers.txt"
        response = requests.get(url)
        if response.status_code == 200:
            clean_list = [t.split(",")[0].strip() for t in response.text.splitlines() if t]
            clean_list.sort()
            priorities = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "AMD", "PLTR", "COIN", "GME", "JPM", "KO", "PEP", "O", "SCHD"]
            for p in reversed(priorities):
                if p in clean_list:
                    clean_list.remove(p)
                    clean_list.insert(0, p)
            return clean_list
    except:
        return ["NVDA", "TSLA", "AAPL", "MSFT", "KO", "JPM"]

ALL_TICKERS = load_ticker_list()

def get_stock_data(ticker):
    """Safe individual fetcher."""
    try:
        stock = yf.Ticker(ticker)
        return stock.fast_info['last_price'], stock.history(period="1mo")['Close']
    except:
        return 0.0, pd.Series()

def fetch_portfolio_prices(tickers):
    """Downloads all portfolio prices in one go."""
    if not tickers:
        return {}
    try:
        data = yf.download(tickers, period="1d", group_by='ticker', progress=False)
        prices = {}
        for t in tickers:
            try:
                if len(tickers) == 1:
                    price = data['Close'].iloc[-1]
                else:
                    price = data[t]['Close'].iloc[-1]
                prices[t] = price
            except:
                prices[t] = 0.0
        return prices
    except:
        return {}

def get_dividend_info(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        div_yield = info.get('dividendYield', 0)
        return (div_yield * 100) if div_yield else 0.0
    except:
        return 0.0

@st.cache_data(ttl=3600)
def analyze_gem(ticker):
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        root = ET.fromstring(response.content)
        headlines = [f"- {i.find('title').text}" for i in root.findall('.//item')[:5] if i.find('title') is not None]
        
        if not headlines: return 0.0, "No news found."
        
        prompt = f"""
        Analyze sentiment for {ticker} (-1.0 to 1.0) based on:
        {chr(10).join(headlines)}
        Output: SCORE: [num] REASON: [text]
        """
        response = client.chat.completions.create(model="grok-4-1-fast-reasoning", messages=[{"role": "user", "content": prompt}])
        content = response.choices[0].message.content
        
        score = 0.0
        reason = "Analysis failed"
        if "SCORE:" in content: score = float(content.split("SCORE:")[1].split()[0])
        if "REASON:" in content: reason = content.split("REASON:")[1].strip()
        return score, reason
    except:
        return 0.0, "Error analyzing news."

# --- APP LOGIC ---
if 'ticker_input' not in st.session_state: st.session_state.ticker_input = "NVDA"

# 1. HEADER
pf = get_portfolio()
cash = get_cash()
portfolio_val = 0.0

if not pf.empty:
    ticker_list = pf['ticker'].tolist()
    price_map = fetch_portfolio_prices(ticker_list)
    
    # Map prices safely
    pf['Current Price'] = pf['ticker'].map(price_map)
    pf['Value'] = pf['shares'] * pf['Current Price']
    portfolio_val = pf['Value'].sum()

total_equity = cash + portfolio_val
all_time_return = total_equity - 10000 
sign = "+" if all_time_return >= 0 else ""

col_h1, col_h2 = st.columns([2, 1])
with col_h1:
    st.markdown('<p class="metric-label">Total Investing Value</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="big-font">${total_equity:,.2f}</p>', unsafe_allow_html=True)
    st.caption(f"{sign}${all_time_return:,.2f} All Time Return")

with col_h2:
    st.metric("Buying Power", f"${cash:,.2f}")

log_history(total_equity)

# 2. TABS
tab_trade, tab_div, tab_ai = st.tabs(["⚡ Trade", "💸 Dividend Playground", "💎 AI Scanner"])

with tab_trade:
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Order Ticket")
        selected_ticker = st.selectbox("Search Symbol", options=ALL_TICKERS, index=0)
        current_price, price_history = get_stock_data(selected_ticker)
        
        if current_price > 0:
            st.markdown(f"**{selected_ticker}** | ${current_price:,.2f}")
            shares = st.number_input("Shares", min_value=1, value=10)
            cost = current_price * shares
            st.caption(f"Est. Cost: ${cost:,.2f}")
            
            b1, b2 = st.columns(2)
            if b1.button("Buy", type="primary", use_container_width=True):
                fresh_cash = get_cash()
                if fresh_cash >= cost:
                    update_cash(fresh_cash - cost)
                    add_trade(selected_ticker, shares, current_price, "BUY")
                    st.toast(f"✅ Bought {shares} {selected_ticker}")
                    st.rerun()
                else:
                    st.error("Insufficient Funds")
            
            if b2.button("Sell", use_container_width=True):
                fresh_cash = get_cash()
                if not pf.empty and selected_ticker in pf['ticker'].values:
                    owned = pf[pf['ticker'] == selected_ticker]['shares'].values[0]
                    if owned >= shares:
                        update_cash(fresh_cash + cost)
                        add_trade(selected_ticker, shares, current_price, "SELL")
                        st.toast(f"✅ Sold {shares} {selected_ticker}")
                        st.rerun()
                    else:
                        st.error(f"You only own {owned} shares.")
                else:
                    st.error("You don't own this.")
    
    with c2:
        if not price_history.empty:
            st.line_chart(price_history, height=300)
        if not pf.empty:
            st.subheader("Positions")
            st.dataframe(
                pf[['ticker', 'shares', 'avg_cost', 'Current Price', 'Value']],
                column_config={
                    "avg_cost": st.column_config.NumberColumn("Avg Cost", format="$%.2f"),
                    "Current Price": st.column_config.NumberColumn("Price", format="$%.2f"),
                    "Value": st.column_config.NumberColumn("Equity", format="$%.2f"),
                },
                use_container_width=True,
                hide_index=True
            )

with tab_div:
    st.header("🔮 Dividend What-If Machine")
    d_col1, d_col2 = st.columns([1, 2])
    with d_col1:
        div_ticker = st.selectbox("Pick Stock", ["KO", "JPM", "O", "SCHD", "PEP", "T", "VZ", "MO", "AAPL", "MSFT"])
        real_yield = get_dividend_info(div_ticker)
        current_price_div = yf.Ticker(div_ticker).fast_info['last_price']
        
        st.write(f"**{div_ticker}** | Price: ${current_price_div:.2f} | Yield: {real_yield:.2f}%")
        shares_owned = st.number_input("Shares Owned", value=100)
        user_yield = st.slider("Yield %", 0.0, 15.0, real_yield, 0.1)
        
    with d_col2:
        annual = (shares_owned * current_price_div) * (user_yield / 100)
        monthly = annual / 12
        m1, m2 = st.columns(2)
        m1.metric("Annual Income", f"${annual:,.2f}")
        m2.metric("Monthly Income", f"${monthly:,.2f}")
        chart_data = pd.DataFrame({"Type": ["Monthly", "Annual"], "Amount": [monthly, annual]})
        st.bar_chart(chart_data.set_index("Type"), color="#00c805")

with tab_ai:
    st.subheader("💎 Opportunity Scanner")
    st.caption("AI results are cached for 1 hour to prevent rate limits.")
    if st.button("Scan Market", type="primary"):
        movers = ["NVDA", "TSLA", "PLTR", "MSTR", "AMD"] 
        cols = st.columns(len(movers))
        for i, t in enumerate(movers):
            with cols[i]:
                with st.spinner(f"{t}..."):
                    score, reason = analyze_gem(t)
                    color = "green" if score > 0 else "red"
                    st.markdown(f"**{t}** :{color}[{score}]")
                    st.caption(reason)
