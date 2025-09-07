# tv-to-binance – Automated Trading Example
Webhook to send TradingView alerts to Binance

This repository contains sample code for the automated connection between **TradingView Alerts**, a **Python Flask web service**, and the **Binance API**.  
It is intended solely for **educational and demonstration purposes** as part of my Bitcoin and crypto consulting services.



## ⚠️ Disclaimer

- This repository does **not constitute financial or investment advice**.
- I do **not trade on behalf of third parties** and do **not manage assets**.
- Each user is **solely responsible** for the setup, operation, and use of the software provided here.  
- The code is for **illustrative purposes only** and is provided **without warranty or liability**.
- By using this code, the user acknowledges that they assume full responsibility for all actions associated with their own accounts and API keys.



## Usage

1. Create a TradingView account and define your own alerts
2. Create a Binance account and Binance API key in your own account with Reading and Spot & Margin Trading restrictions
3. Create a Render account and setup a new web service with this Git repository as Source Code



## Purpose

The goal is to **demonstrate in a practical manner** to clients how automated trading can be implemented technically as part of my consulting services.  
The focus is on **education and self-empowerment**—not on the execution of trades by me.



## Example alert

TradingView alert message:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_pct": "0.0007",
  "amount": "7",
  "client_secret": "-your client secret-"
}

Legend:
- action: returns the string "buy" or "sell" for the executed order
- symbol: returns the trading pair, e.g. BTCUSDT
- buy_pct: defines the percentage of the total USDT balance to be used for a buy order
- amount: defines the amount to be used for a buy order
- client_secret: defines your personally defined client secret

Rule:
- If the action is "buy", exactly one of the fields "buy_pct" or "amount" must be provided.
  - If both are provided → the payload is rejected.
  - If neither is provided → the payload is rejected.
  - If only one is provided → the payload is valid.
- If the action is "sell", the total asset balance is sold

