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

# Custom CSS to make it look cleaner (Robinhood vibes)
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
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio
                 (ticker TEXT PRIMARY KEY, shares REAL, avg_cost REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS balance
                 (id INTEGER PRIMARY KEY, cash REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (timestamp TEXT, equity REAL)''')
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
    # Only log if the last log was > 1 hour ago to prevent spamming DB
    last_log = c.execute('SELECT timestamp FROM history ORDER BY timestamp DESC LIMIT 1').fetchone()
    now = datetime.datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d %H:%M")
    
    should_log = True
    if last_log:
        last_time = last_log[0]
        # Simple string comparison works for ISO format
        if now[:13
