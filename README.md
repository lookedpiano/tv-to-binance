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

1. Create a TradingView account and define your own alerts. 
2. Create a Binance account and API key with **Read** + **Spot/Margin Trade** permissions  
3. Create a Render account and deploy:
   - This Flask app as a web service  
   - A Managed Redis instance



## Purpose

The goal is to **demonstrate in a practical manner** how automated trading works from a technical perspective.  
The focus is on **education and self-empowerment**, not on trade execution as a service.



## Example TradingView Alerts

Buy with fixed quote amount (e.g. spend 1000 USDT) or sell 80% of your base asset (e.g. 80% of ADA holdings)
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_quote_amount": "1000",
  "sell_base_pct": "0.8",
  "client_secret": "-your client secret-"
}

Buy 34% of quote balance (e.g of available USDT) OR sell exactly 100 of a base asset
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_quote_pct": "0.34",
  "sell_base_amount": "100",
  "client_secret": "-your client secret-"
}

Buy exactly 77 units of the base asset (e.g. ADAs) OR sell enough base to receive 150 USDT worth of quote
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_base_amount": "77",
  "sell_quote_amount": "150",
  "client_secret": "-your client_secret-"
}

## Legend

Fields common to both BUY and SELL
- action – TradingView's BUY or SELL
- symbol – Trading pair symbol (e.g. BTCUSDT, ADABTC, ETHBTC, SOLUSDC, etc.)
- client_secret – Your personal authentication secret

BUY fields
Choose exactly one of:
- buy_quote_pct - Fraction of your quote asset balance to spend.
- buy_quote_amount - Exact amount of quote asset to spend.
- buy_base_amount - Exact number of base asset units to buy.

SELL fields
Choose exactly one of:
- sell_base_pct - Sell a fraction of your base asset
- sell_base_amount - Sell an exact base asset amount
- sell_quote_amount - Sell enough base to receive a target amount of quote asset.

Rules:
- Exactly one field must be supplied per side
- No mixing multiple BUY fields
- No mixing multiple SELL fields
- Missing required fields → webhook rejected



## Summary Table: Six Valid Trading Inputs
| Field Name        | Side | Meaning                             | Example                  |
| ----------------- | ---- | ----------------------------------- | ------------------------ |
| buy_quote_amount  | BUY  | Spend fixed amount of quote asset   | Buy BTC with 100 USDT    |
| buy_quote_pct     | BUY  | Spend % of quote balance            | Buy BTC with 50% of USDT |
| buy_base_amount   | BUY  | Buy fixed base amount               | Buy 5 ADA                |
| sell_base_amount  | SELL | Sell fixed base amount              | Sell 0.05 BTC            |
| sell_base_pct     | SELL | Sell % of base holdings             | Sell 80% of ADA          |
| sell_quote_amount | SELL | Sell enough base to receive X quote | Sell BTC worth 20 USDT   |



## Redis Cache Endpoints
🔓 Public Endpoints
Endpoint                  Method  Description
/cache/prices             GET     Returns all cached prices (price_cache hash).
/cache/prices/count	      GET     Returns the number of cached symbols in price_cache.
/cache/prices/<symbol>    GET     Returns the cached mid-price for a specific symbol.
/cache/filters            GET     Returns cached symbol filters (minQty, minNotional, etc.) for all pairs.
/cache/filters/<symbol>   GET     Returns cached filters for a specific symbol.
/cache/summary            GET     Returns a structured summary of cache state.

🔐 Admin-Protected Endpoint
Endpoint                  Method  Description
/cache/balances           GET     Returns cached Binance account balances (from account_balances key).
/health-check             GET     General health probe endpoint.
/cache/balances           GET     Fetch balances from cache.
/cache/refresh/balances   POST    Fetch and cache balances from Binance via REST.
/cache/refresh/filters    POST    Fetch and cache trading filters from Binance via REST.
/cache/orders             GET     Return recent cached order logs.
/dashboard                GET     Render the dashboard (deprecated resp. fallback).

To securely access these endpoints, simply **visit the 21mio dashboard** or include your admin key in the request header. For example:
curl -H "X-Admin-Key: <ADMIN_API_KEY>" https://<your-web-service-name>.onrender.com/cache/balances
