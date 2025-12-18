import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from openai import OpenAI

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Trading Market Simulator", layout="wide", page_icon="📈")
st.title("📈 Trading Market Simulator")

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

# --- ADVANCED: ROBUST TICKER FETCHING ---
@st.cache_data(ttl=24*3600) # Cache this for 24 hours so it's super fast
def load_ticker_list():
    """
    Fetches a master list of ~8,000 US stocks from a public repository.
    Returns a formatted list ["AAPL - Apple Inc.", ...]
    """
    try:
        # This is a public list of NASDAQ/NYSE tickers updated frequently
        url = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/all/all_tickers.txt"
        response = requests.get(url)
        
        if response.status_code == 200:
            raw_tickers = response.text.splitlines()
            # Clean and format (Some might be just "AAPL", some might be "AAPL - Apple")
            clean_list = [t.split(",")[0].strip() for t in raw_tickers if t]
            clean_list.sort()
            
            # Add some popular ones at the top for easy access
            priorities = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "AMD", "PLTR", "COIN", "GME"]
            for p in reversed(priorities):
                if p in clean_list:
                    clean_list.remove(p)
                    clean_list.insert(0, p)
            return clean_list
    except:
        pass
        
    # Fallback if GitHub is down
    return ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AMD", "PLTR", "COIN"]

# Load the list once on startup
ALL_TICKERS = load_ticker_list()

# --- PROMPT LOGIC ---
SENTIMENT_PROMPT = """
You are a hedge fund analyst. Analyze the following news for {ticker}.
Determine the market sentiment score from -1.0 (Bearish) to 1.0 (Bullish).

News Data:
{news_text}

Output format exactly like this:
SCORE: [Number between -1.0 and 1.0]
REASON: [1 short sentence explaining why]
"""

# --- FUNCTIONS ---
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        price = stock.fast_info['last_price']
        history = stock.history(period="1mo")
        return price, history['Close']
    except:
        return 0.0, pd.Series()

def get_market_movers():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved/day_gainers?count=6"
        data = requests.get(url, headers=headers).json()
        return [q['symbol'] for q in data['finance']['result'][0]['quotes']]
    except:
        return ["NVDA", "TSLA", "PLTR", "MSTR", "COIN", "AMD"]

def get_news_from_rss(ticker):
    try:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        root = ET.fromstring(response.content)
        headlines = []
        for item in root.findall('.//item')[:5]:
            title_node = item.find('title')
            if title_node is not None:
                headlines.append(f"- {title_node.text}")
        return headlines
    except:
        return []

def analyze_gem(ticker):
    try:
        headlines = get_news_from_rss(ticker)
        if not headlines:
            stock = yf.Ticker(ticker)
            news = stock.news
            for n in news[:4]:
                title = n.get('title', n.get('headline', ''))
                if title: headlines.append(f"- {title}")
        
        if not headlines:
            return 0.0, "No news data available for sentiment."

        news_text = "\n".join(headlines)
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning", 
            messages=[{"role": "user", "content": SENTIMENT_PROMPT.format(ticker=ticker, news_text=news_text)}]
        )
        content = response.choices[0].message.content
        
        score = 0.0
        reason = "Analysis failed"
        for line in content.split('\n'):
            if "SCORE:" in line:
                try: score = float(line.split("SCORE:")[1].strip())
                except: score = 0.0
            if "REASON:" in line:
                reason = line.split("REASON:")[1].strip()
        return score, reason
    except Exception as e:
        return 0.0, f"Error: {str(e)}"

# --- INITIALIZE MEMORY ---
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Shares", "Avg_Cost"])
if 'cash' not in st.session_state:
    st.session_state.cash = 10000.00
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = []
if 'ticker_input' not in st.session_state:
    st.session_state.ticker_input = "NVDA"

# --- SIDEBAR: TRADING DESK ---
st.sidebar.header("💵 Trading Desk")
st.sidebar.metric("Cash Available", f"${st.session_state.cash:,.2f}")

# --- ROBUST SEARCH BAR ---
# Now searches 8,000+ real tickers
selected_ticker = st.sidebar.selectbox(
    "Search Market (8000+ Tickers)", 
    options=ALL_TICKERS,
    index=ALL_TICKERS.index(st.session_state.ticker_input) if st.session_state.ticker_input in ALL_TICKERS else 0
)

st.session_state.ticker_input = selected_ticker
current_price, price_history = get_stock_data(selected_ticker)

if current_price > 0:
    st.sidebar.caption(f"Current Price: **${current_price:,.2f}**")
else:
    st.sidebar.error("Invalid Ticker")

shares_input = st.sidebar.number_input("Shares", min_value=1, value=10)
estimated_cost = current_price * shares_input
st.sidebar.caption(f"Est. Cost: ${estimated_cost:,.2f}")

c1, c2 = st.sidebar.columns(2)
if c1.button("BUY", type="primary"):
    if current_price > 0:
        if st.session_state.cash >= estimated_cost:
            st.session_state.cash -= estimated_cost
            new_trade = pd.DataFrame([{"Ticker": selected_ticker, "Shares": shares_input, "Avg_Cost": current_price}])
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, new_trade], ignore_index=True)
            st.toast(f"✅ Bought {shares_input} {selected_ticker}!")
        else:
            st.toast("❌ Insufficient Funds")

if c2.button("SELL"):
    mask = st.session_state.portfolio['Ticker'] == selected_ticker
    if mask.any():
        rev = current_price * shares_input
        st.session_state.cash += rev
        st.session_state.portfolio = st.session_state.portfolio.drop(st.session_state.portfolio[mask].index[-1])
        st.toast(f"✅ Sold {selected_ticker}!")
    else:
        st.toast("⚠️ No shares owned")

# --- MAIN DASHBOARD ---
if not price_history.empty:
    with st.expander(f"📊 {selected_ticker} Price History (1 Month)", expanded=True):
        st.line_chart(price_history, height=250)

tab1, tab2 = st.tabs(["💼 Your Portfolio", "💎 Gem Scanner"])

with tab1:
    if not st.session_state.portfolio.empty:
        df = st.session_state.portfolio.copy()
        
        def get_price_safe(t):
            try: return yf.Ticker(t).fast_info['last_price']
            except: return 0.0
            
        df['Current Price'] = df['Ticker'].apply(get_price_safe)
        df['Market Value'] = df['Shares'] * df['Current Price']
        df['P/L ($)'] = df['Market Value'] - (df['Shares'] * df['Avg_Cost'])
        df['P/L (%)'] = (df['P/L ($)'] / (df['Shares'] * df['Avg_Cost'])) * 100
        
        st.dataframe(
            df,
            column_config={
                "Current Price": st.column_config.NumberColumn(format="$%.2f"),
                "Avg_Cost": st.column_config.NumberColumn(format="$%.2f"),
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "P/L ($)": st.column_config.NumberColumn(format="$%.2f"),
                "P/L (%)": st.column_config.NumberColumn(format="%.2f%%"),
            },
            use_container_width=True,
            hide_index=True
        )
        
        total_market_val = df['Market Value'].sum()
        total_equity = st.session_state.cash + total_market_val
        profit_loss = total_equity - 10000
        
        col_a, col_b = st.columns(2)
        col_a.metric("Total Account Equity", f"${total_equity:,.2f}", delta=f"{profit_loss:,.2f}")
        col_b.metric("Cash Position", f"${st.session_state.cash:,.2f}")
    else:
        st.info("Your portfolio is empty. Start trading in the sidebar!")

with tab2:
    st.subheader("🤖 AI Sentiment Scanner")
    st.markdown("Finds volatile stocks and uses **Grok-Reasoning** to verify if they are real opportunities.")
    
    if st.button("💎 Scan for Gems", type="primary"):
        st.session_state.scan_results = []
        movers = get_market_movers()
        
        progress = st.progress(0)
        status = st.empty()
        
        for i, ticker in enumerate(movers):
            status.text(f"Grok is reading news for {ticker}...")
            progress.progress((i + 1) / len(movers))
            
            score, reason = analyze_gem(ticker)
            st.session_state.scan_results.append({
                "Ticker": ticker,
                "Score": score,
                "Reason": reason
            })
        status.empty()
        st.success("Analysis Complete!")
        
    if st.session_state.scan_results:
        cols = st.columns(3)
        for i, item in enumerate(st.session_state.scan_results):
            with cols[i % 3]:
                score = item['Score']
                if score >= 0.5:
                    color, emoji = "green", "🚀"
                elif score <= -0.5:
                    color, emoji = "red", "📉"
                else:
                    color, emoji = "gray", "⚖️"
                
                with st.container(border=True):
                    st.markdown(f"### {item['Ticker']} {emoji}")
                    st.markdown(f"**Score:** :{color}[{score}]")
                    st.caption(item['Reason'])
                    if st.button(f"Load {item['Ticker']}", key=f"btn_{item['Ticker']}"):
                        st.session_state.ticker_input = item['Ticker']
                        st.rerun()
