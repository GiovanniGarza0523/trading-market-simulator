import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from openai import OpenAI

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Trading Market Simulator", layout="wide")
st.title("📈 Trading Market Simulator")

# --- SETUP CREDENTIALS ---
if "XAI_API_KEY" in st.secrets:
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
else:
    st.error("⚠️ XAI_API_KEY is missing! Please add it to your Streamlit Secrets.")
    st.stop()

# Connect to Grok (Using the Smart & Cheap 'Reasoning' Model)
client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

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
def get_current_price(ticker):
    """Get live price for trading."""
    try:
        return yf.Ticker(ticker).fast_info['last_price']
    except:
        return 0.0

def get_market_movers():
    """Layer 1: The 'Net' (Get Yahoo Day Gainers)."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved/day_gainers?count=6"
        data = requests.get(url, headers=headers).json()
        return [q['symbol'] for q in data['finance']['result'][0]['quotes']]
    except:
        return ["NVDA", "TSLA", "PLTR", "MSTR", "COIN", "AMD"]

def analyze_gem(ticker):
    """Layer 2: The 'Brain' (Grok Reasoning)."""
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news: 
            return 0.0, "No recent news found."
        
        # --- THE FIX IS HERE ---
        # We use .get() to safely grab the title. 
        # If 'title' is missing, it tries 'headline', or defaults to "Market News"
        headlines = []
        for n in news[:4]:
            title = n.get('title', n.get('headline', 'Market News'))
            headlines.append(f"- {title}")
        
        news_text = "\n".join(headlines)
        
        # Call Grok
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning", 
            messages=[{"role": "user", "content": SENTIMENT_PROMPT.format(ticker=ticker, news_text=news_text)}]
        )
        content = response.choices[0].message.content
        
        # Parse the output
        score = 0.0
        reason = "Analysis failed"
        for line in content.split('\n'):
            if "SCORE:" in line:
                try:
                    score = float(line.split("SCORE:")[1].strip())
                except:
                    score = 0.0
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

# --- SIDEBAR: TRADING DESK ---
st.sidebar.header("💵 Trading Desk")
st.sidebar.metric("Cash Available", f"${st.session_state.cash:,.2f}")

ticker_input = st.sidebar.text_input("Ticker Symbol", value="NVDA").upper()
shares_input = st.sidebar.number_input("Shares", min_value=1, value=10)

c1, c2 = st.sidebar.columns(2)
if c1.button("BUY"):
    p = get_current_price(ticker_input)
    if p > 0:
        cost = p * shares_input
        if st.session_state.cash >= cost:
            st.session_state.cash -= cost
            new_trade = pd.DataFrame([{"Ticker": ticker_input, "Shares": shares_input, "Avg_Cost": p}])
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, new_trade], ignore_index=True)
            st.sidebar.success(f"Bought {ticker_input}!")
        else:
            st.sidebar.error("Insufficient Funds")
    else:
        st.sidebar.error("Invalid Ticker")

if c2.button("SELL"):
    mask = st.session_state.portfolio['Ticker'] == ticker_input
    if mask.any():
        p = get_current_price(ticker_input)
        rev = p * shares_input
        st.session_state.cash += rev
        st.session_state.portfolio = st.session_state.portfolio.drop(st.session_state.portfolio[mask].index[-1])
        st.sidebar.success(f"Sold {ticker_input}!")
    else:
        st.sidebar.error("No shares owned")

# --- MAIN LAYOUT ---
tab1, tab2 = st.tabs(["📊 Portfolio", "💎 Gem Scanner"])

with tab1:
    if not st.session_state.portfolio.empty:
        df = st.session_state.portfolio.copy()
        df['Current Price'] = df['Ticker'].apply(lambda t: get_current_price(t))
        df['Value'] = df['Shares'] * df['Current Price']
        df['P/L'] = df['Value'] - (df['Shares'] * df['Avg_Cost'])
        
        # Formatting
        df['Current Price'] = df['Current Price'].map('${:,.2f}'.format)
        df['Value'] = df['Value'].map('${:,.2f}'.format)
        df['P/L'] = df['P/L'].map('${:,.2f}'.format)
        
        st.dataframe(df, use_container_width=True)
        
        # Total Value Calculation
        current_holdings_val = st.session_state.portfolio['Shares'].mul(
            st.session_state.portfolio['Ticker'].apply(lambda t: get_current_price(t))
        ).sum()
        total_equity = st.session_state.cash + current_holdings_val
        st.metric("Total Account Equity", f"${total_equity:,.2f}")
    else:
        st.info("No active trades.")

with tab2:
    st.subheader("🤖 AI Sentiment Scanner")
    st.write("Finds today's top gainers and uses Grok to score them (-1 to 1).")
    
    if st.button("💎 Scan for Gems"):
        st.session_state.scan_results = []
        movers = get_market_movers()
        
        progress = st.progress(0)
        status_text = st.empty()
        
        for i, ticker in enumerate(movers):
            status_text.write(f"Analyzing {ticker}...")
            progress.progress((i + 1) / len(movers))
            
            score, reason = analyze_gem(ticker)
            st.session_state.scan_results.append({
                "Ticker": ticker,
                "Score": score,
                "Reason": reason
            })
        status_text.write("Scan Complete!")
        
    # Display Results Grid
    if st.session_state.scan_results:
        cols = st.columns(3)
        for i, item in enumerate(st.session_state.scan_results):
            with cols[i % 3]:
                score = item['Score']
                color = "green" if score > 0.3 else "red" if score < -0.3 else "gray"
                emoji = "🚀" if score > 0.5 else "🐻" if score < -0.5 else "⚖️"
                
                with st.container(border=True):
                    st.markdown(f"### {item['Ticker']} {emoji}")
                    st.markdown(f"**Score:** :{color}[{score}]")
                    st.caption(f"{item['Reason']}")
                    if st.button(f"Trade {item['Ticker']}", key=f"trade_{item['Ticker']}"):
                        st.sidebar.info(f"Ticker {item['Ticker']} copied to Trading Desk!")
