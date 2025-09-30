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

TradingView alert message to buy with a fixed amount of 1000 USDT and to sell 80 % of the base asset:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_amount": "1000",
  "sell_pct": "0.8",
  "client_secret": "-your client secret-"
}

TradingView alert message to buy 34 % of available USDT and to sell a fixed amount of 100 of the base asset:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_pct": "0.34",
  "sell_amount": "100",
  "client_secret": "-your client secret-"
}

Legend:
- action: returns the string "buy" or "sell" for the executed order
- symbol: returns the trading pair, e.g. BTCUSDT, ETHUSDC, etc.
- buy_pct: A decimal fraction (0 < buy_pct <= 1) indicating what fraction of your available quote asset balance (e.g., USDT or USDC) should be invested in the buy order. (Example: 0.05 = invest 5 % of your available quote balance.)
- buy_amount: An explicit numeric value specifying the exact quote asset amount to invest in the buy order. Must not exceed your available quote asset balance.
- sell_pct: A decimal fraction (0 < sell_pct <= 1) indicating what fraction of your available base asset balance (e.g., ADA in ADAUSDT, ETH in ETHUSDC) should be sold. (Example: 0.25 = sell 25% of your ADA holdings.)
- sell_amount: An explicit numeric value specifying the exact base asset amount to sell. Must not exceed your available base asset balance.
- client_secret: defines your personally defined client secret

Rule:
- If the action is "buy" or "sell", exactly one of the following must be supplied in the payload:
  - a percentage field (buy_pct for BUY, sell_pct for SELL) OR
  - an amount field (buy_amount for BUY, sell_amount for SELL)
- If both are present → the payload is rejected.
- If neither is present → the payload is rejected.
