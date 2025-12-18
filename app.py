import streamlit as st
import yfinance as yf
import pandas as pd
from openai import OpenAI

# --- PAGE CONFIG ---
st.set_page_config(page_title="Wife's Trading Sim", layout="wide")
st.title("📈 Swing Trader Simulator")

# --- SETUP CREDENTIALS ---
# We look for the key in Streamlit Secrets
# If it's missing, we warn the user instead of crashing immediately
if "XAI_API_KEY" in st.secrets:
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
else:
    st.error("⚠️ XAI_API_KEY is missing in Secrets! Please add it in Streamlit Cloud settings.")
    st.stop()

client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

# --- INITIALIZE STATE (The "Memory") ---
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Shares", "Avg_Cost"])
if 'cash' not in st.session_state:
    st.session_state.cash = 10000.00 # Starting Cash

# --- FUNCTIONS ---
def get_stock_price(ticker):
    """Gets real-time price from Yahoo Finance"""
    try:
        stock = yf.Ticker(ticker)
        # Fast info is often faster/more reliable for "last price" than history()
        return stock.fast_info['last_price']
    except:
        return None

def get_grok_analysis(ticker):
    """Fetches Yahoo News and asks Grok for sentiment"""
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news:
            return "No recent news found for this stock."
            
        headlines = [f"- {n['title']}" for n in news[:5]]
        news_text = "\n".join(headlines)
        
        prompt = f"""
        You are a swing trading expert. Analyze these headlines for {ticker} and give a sentiment rating.
        
        Headlines:
        {news_text}
        
        Output format:
        **Sentiment:** [BULLISH/BEARISH/NEUTRAL]
        **Reason:** [One concise sentence]
        """
        
        response = client.chat.completions.create(
            model="grok-beta",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"

# --- SIDEBAR: TRADING ---
st.sidebar.header("💰 Trading Desk")
st.sidebar.metric("Cash Available", f"${st.session_state.cash:,.2f}")

ticker_input = st.sidebar.text_input("Ticker Symbol", value="AAPL").upper()
shares_input = st.sidebar.number_input("Shares", min_value=1, value=10)

col1, col2 = st.sidebar.columns(2)

if col1.button("BUY"):
    current_price = get_stock_price(ticker_input)
    if current_price:
        cost = current_price * shares_input
        if st.session_state.cash >= cost:
            st.session_state.cash -= cost
            new_trade = pd.DataFrame([{"Ticker": ticker_input, "Shares": shares_input, "Avg_Cost": current_price}])
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, new_trade], ignore_index=True)
            st.sidebar.success(f"Bought {shares_input} {ticker_input}!")
        else:
            st.sidebar.error("Not enough cash!")
    else:
        st.sidebar.error("Invalid Ticker")

if col2.button("SELL"):
    # Simple sell logic: Remove matching rows (LIFO style for simplicity)
    mask = st.session_state.portfolio['Ticker'] == ticker_input
    if mask.any():
        current_price = get_stock_price(ticker_input)
        revenue = current_price * shares_input
        st.session_state.cash += revenue
        
        # Remove the latest entry for this ticker
        idx_to_drop = st.session_state.portfolio[mask].index[-1]
        st.session_state.portfolio = st.session_state.portfolio.drop(idx_to_drop)
        st.sidebar.success(f"Sold position in {ticker_input}!")
    else:
        st.sidebar.error("You don't own this stock!")

# --- MAIN DASHBOARD ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("📊 Portfolio Performance")
    if not st.session_state.portfolio.empty:
        df = st.session_state.portfolio.copy()
        
        # Add live price column (Safe apply)
        def safe_price(t):
            p = get_stock_price(t)
            return p if p else 0.0
            
        df['Current Price'] = df['Ticker'].apply(safe_price)
        df['Market Value'] = df['Shares'] * df['Current Price']
        df['P/L'] = df['Market Value'] - (df['Shares'] * df['Avg_Cost'])
        
        # Format for display
        display_df = df.copy()
        display_df['Current Price'] = display_df['Current Price'].map('${:,.2f}'.format)
        display_df['Avg_Cost'] = display_df['Avg_Cost'].map('${:,.2f}'.format)
        display_df['Market Value'] = display_df['Market Value'].map('${:,.2f}'.format)
        display_df['P/L'] = display_df['P/L'].map('${:,.2f}'.format)

        st.dataframe(display_df, use_container_width=True)
        
        total_value = st.session_state.cash + df['Market Value'].sum()
        st.metric("Total Account Value", f"${total_value:,.2f}", delta=f"{total_value-10000:.2f}")
    else:
        st.info("No active trades. Buy stocks in the sidebar!")

with c2:
    st.subheader("🤖 Grok Sentiment")
    if ticker_input:
        if st.button(f"Analyze {ticker_input}"):
            with st.spinner("Grok is reading the news..."):
                analysis = get_grok_analysis(ticker_input)
                st.markdown(analysis)
