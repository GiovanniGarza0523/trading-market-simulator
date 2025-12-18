import streamlit as st
import yfinance as yf
import pandas as pd
from openai import OpenAI

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Trading Market Simulator", layout="wide")
st.title("📈 Trading Market Simulator")

# --- SETUP CREDENTIALS ---
# Checks for the API key in Streamlit Secrets
if "XAI_API_KEY" in st.secrets:
    XAI_API_KEY = st.secrets["XAI_API_KEY"]
else:
    st.error("⚠️ XAI_API_KEY is missing! Please add it to your Streamlit Secrets.")
    st.stop()

# Connect to Grok (xAI) using the OpenAI library
client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

# --- INITIALIZE MEMORY (Session State) ---
# This keeps track of cash and stocks while the app is running
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Shares", "Avg_Cost"])
if 'cash' not in st.session_state:
    st.session_state.cash = 10000.00  # Starting Balance

# --- HELPER FUNCTIONS ---
def get_stock_price(ticker):
    """Fetches the latest real-time price from Yahoo Finance."""
    try:
        stock = yf.Ticker(ticker)
        return stock.fast_info['last_price']
    except:
        return None

def get_grok_analysis(ticker):
    """Fetches latest news and asks Grok for a swing trading sentiment."""
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news:
            return "No recent news found for this stock."
            
        # Format the news for Grok
        headlines = [f"- {n['title']}" for n in news[:5]]
        news_text = "\n".join(headlines)
        
        prompt = f"""
        You are a cynical Wall Street swing trader. Analyze these headlines for {ticker} and determine the immediate sentiment.
        
        Headlines:
        {news_text}
        
        Output format:
        **SENTIMENT:** [BULLISH / BEARISH / NEUTRAL]
        **REASONING:** [One concise, punchy sentence explaining why]
        """
        
        response = client.chat.completions.create(
            model="grok-beta",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error analyzing data: {str(e)}"

# --- SIDEBAR: TRADING DESK ---
st.sidebar.header("💵 Trading Desk")
st.sidebar.metric("Cash Available", f"${st.session_state.cash:,.2f}")

ticker_input = st.sidebar.text_input("Ticker Symbol", value="NVDA").upper()
shares_input = st.sidebar.number_input("Shares", min_value=1, value=10)

col1, col2 = st.sidebar.columns(2)

# BUY BUTTON LOGIC
if col1.button("BUY"):
    current_price = get_stock_price(ticker_input)
    if current_price:
        total_cost = current_price * shares_input
        if st.session_state.cash >= total_cost:
            st.session_state.cash -= total_cost
            
            # Record the trade
            new_trade = pd.DataFrame([{"Ticker": ticker_input, "Shares": shares_input, "Avg_Cost": current_price}])
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, new_trade], ignore_index=True)
            
            st.sidebar.success(f"Bought {shares_input} {ticker_input} @ ${current_price:.2f}")
        else:
            st.sidebar.error("Insufficient Funds!")
    else:
        st.sidebar.error("Invalid Ticker Symbol")

# SELL BUTTON LOGIC
if col2.button("SELL"):
    # Check if we own the stock
    mask = st.session_state.portfolio['Ticker'] == ticker_input
    if mask.any():
        current_price = get_
