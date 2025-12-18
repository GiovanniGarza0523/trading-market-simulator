import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import sqlite3
import datetime
import pytz
from openai import OpenAI

# --- PAGE CONFIGURATION (Robinhood Style) ---
st.set_page_config(page_title="Trading App", layout="wide", page_icon="💸")

# Custom CSS for that clean mobile-app look
st.markdown("""
    <style>
    .big-font { font-size: 50px !important; font-weight: 600; color: #00c805; }
    .negative { color: #ff5000; }
    .metric-label { font-size: 14px; color: #696969; }
    </style>
    """, unsafe_allow_html=True)

# --- SETUP CREDENTIALS ---
if "XAI_API_KEY" in st.secrets:
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
else:
    st.error("⚠️ XAI_API_KEY is missing! Please add it to your Streamlit Secrets.")
    st.stop()

client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

# --- DATABASE ENGINE ---
DB_FILE = "robinhood_v1.db"

def init_db():
    """Initialize the database tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio
                 (ticker TEXT PRIMARY KEY, shares REAL, avg_cost REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS balance
                 (id INTEGER PRIMARY KEY, cash REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (timestamp TEXT, equity REAL)''')
    
    # Set starting cash if new
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
    """Logs the portfolio value to the history table."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Only log if the last log was > 1 hour ago (prevent database spam)
    last_log = c.execute('SELECT timestamp FROM history ORDER BY timestamp DESC LIMIT 1').fetchone()
    now = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d %H:%M")
    
    should_log = True
    if last_log:
        last_time = last_log[0]
        # FIXED: Added the missing bracket below
        if now[:13] == last_time[:13]: 
            should_log = False
            
    if should_log:
        c.execute('INSERT INTO history (timestamp, equity) VALUES (?, ?)', (now, equity))
        conn.commit()
    conn.close()

def get_history():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql('SELECT * FROM history', conn)
    conn.close()
    return df

# Initialize DB on load
init_db()

# --- CACHED TICKER LIST ---
@st.cache_data(ttl=24*3600)
def load_ticker_list():
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

# --- HELPER FUNCTIONS ---
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        return stock.fast_info['last_price'], stock.history(period="1mo")['Close']
    except:
        return 0.0, pd.Series()

def get_dividend_info(ticker):
    """Fetches real dividend yield"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        div_yield = info.get('dividendYield', 0)
        if div_yield is None: div_yield = 0
        return div_yield * 100 # Convert to %
    except:
        return 0.0

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

# --- APP LAYOUT ---
if 'ticker_input' not in st.session_state: st.session_state.ticker_input = "NVDA"

# 1. HEADER (Robinhood Style)
pf = get_portfolio()
cash = get_cash()
portfolio_val = 0.0

if not pf.empty:
    pf['Current Price'] = pf['ticker'].apply(lambda t: yf.Ticker(t).fast_info['last_price'])
    pf['Value'] = pf['shares'] * pf['Current Price']
    portfolio_val = pf['Value'].sum()

total_equity = cash + portfolio_val
daily_change = total_equity - 10000 
sign = "+" if daily_change >= 0 else ""

col_h1, col_h2 = st.columns([2, 1])
with col_h1:
    st.markdown('<p class="metric-label">Total Investing Value</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="big-font">${total_equity:,.2f}</p>', unsafe_allow_html=True)
    st.caption(f"{sign}${daily_change:,.2f} All Time")

with col_h2:
    st.metric("Buying Power", f"${cash:,.2f}")

log_history(total_equity)

# 2. MAIN TABS
tab_trade, tab_div, tab_ai = st.tabs(["⚡ Trade", "💸 Dividend Playground", "💎 AI Scanner"])

# --- TAB 1: TRADING ---
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
            st.caption(f"Estimated Cost: ${cost:,.2f}")
            
            b1, b2 = st.columns(2)
            if b1.button("Buy", type="primary", use_container_width=True):
                if cash >= cost:
                    update_cash(cash - cost)
                    add_trade(selected_ticker, shares, current_price, "BUY")
                    st.toast(f"✅ Bought {shares} {selected_ticker}")
                    st.rerun()
                else:
                    st.error("Not enough Buying Power")
            
            if b2.button("Sell", use_container_width=True):
                if not pf.empty and selected_ticker in pf['ticker'].values:
                    owned = pf[pf['ticker'] == selected_ticker]['shares'].values[0]
                    if owned >= shares:
                        update_cash(cash + cost)
                        add_trade(selected_ticker, shares, current_price, "SELL")
                        st.toast(f"✅ Sold {shares} {selected_ticker}")
                        st.rerun()
                    else:
                        st.error(f"You only have {owned} shares.")
                else:
                    st.error("You don't own this.")
    
    with c2:
        if not price_history.empty:
            st.subheader("Performance")
            st.line_chart(price_history, height=300)
            
        st.subheader("Your Positions")
        if not pf.empty:
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
        else:
            st.info("You have no stocks. Buy some on the left!")

# --- TAB 2: DIVIDEND PLAYGROUND ---
with tab_div:
    st.header("🔮 Dividend What-If Machine")
    st.markdown("Experiment with different stocks to see how much passive income you could make.")
    
    d_col1, d_col2 = st.columns([1, 2])
    
    with d_col1:
        div_ticker = st.selectbox("Pick a Stock for Dividends", ["KO", "JPM", "O", "SCHD", "PEP", "T", "VZ", "MO", "AAPL", "MSFT"])
        
        real_yield = get_dividend_info(div_ticker)
        current_price_div = yf.Ticker(div_ticker).fast_info['last_price']
        
        st.divider()
        st.write(f"**Current Data for {div_ticker}:**")
        st.write(f"Price: ${current_price_div:.2f}")
        st.write(f"Real Yield: {real_yield:.2f}%")
        
        st.divider()
        st.write("**Scenario Inputs:**")
        shares_owned = st.number_input("How many shares?", value=100)
        
        st.write("👇 **Adjust the Yield (What if...?)**")
        user_yield = st.slider("Dividend Yield %", min_value=0.0, max_value=15.0, value=real_yield, step=0.1)
        
    with d_col2:
        investment_value = shares_owned * current_price_div
        annual_income = investment_value * (user_yield / 100)
        monthly_income = annual_income / 12
        
        st.subheader("💰 Projected Income")
        m1, m2 = st.columns(2)
        m1.metric("Annual Income", f"${annual_income:,.2f}")
        m2.metric("Monthly Paycheck", f"${monthly_income:,.2f}")
        
        st.info(f"If you own **{shares_owned} shares** of **{div_ticker}** and the yield is **{user_yield}%**...")
        
        chart_data = pd.DataFrame({
            "Income Type": ["Monthly", "Annual"],
            "Amount": [monthly_income, annual_income]
        })
        st.bar_chart(chart_data.set_index("Income Type"), color="#00c805")

# --- TAB 3: AI SCANNER ---
with tab_ai:
    st.subheader("💎 Opportunity Scanner")
    if st.button("Scan Market", type="primary"):
        movers = ["NVDA", "TSLA", "PLTR", "MSTR", "AMD"] 
        cols = st.columns(len(movers))
        for i, t in enumerate(movers):
            with cols[i]:
                with st.spinner(f"Checking {t}..."):
                    score, reason = analyze_gem(t)
                    color = "green" if score > 0 else "red"
                    st.markdown(f"**{t}**")
                    st.markdown(f":{color}[**{score}**]")
                    st.caption(reason)
