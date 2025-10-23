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
3. Create a Render account and set up a new web service with this Git repository as the source code, as well as a new Redis instance



## Purpose

The goal is to **demonstrate in a practical manner** to clients how automated trading can be implemented technically as part of my consulting services.  
The focus is on **education and self-empowerment**—not on the execution of trades by me.



## Example alert

TradingView alert message to buy with a fixed amount of 1000 USDT or to sell 80 % of the base asset:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_funds_amount": "1000",
  "sell_crypto_pct": "0.8",
  "client_secret": "-your client secret-"
}

TradingView alert message to buy 34 % of available USDT or to sell a fixed amount of 100 of the base asset:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_funds_pct": "0.34",
  "sell_crypto_amount": "100",
  "client_secret": "-your client secret-"
}

TradingView alert message to buy 77 ADAs directly or to sell ETH worth 150 USDT:
{
  "action": "{{strategy.order.action}}",
  "symbol": "{{ticker}}",
  "buy_crypto_amount": "77",
  "sell_funds_amount": "150",
  "client_secret": "-your client secret-"
}

Legend:
- action: Returns the string "buy" or "sell" for the executed order.
- symbol: Returns the trading pair, e.g. BTCUSDT, ETHUSDC, ADAUSDT, etc.
- buy_funds_pct: A decimal fraction (0 < buy_funds_pct ≤ 1) indicating what fraction of your available quote asset balance (e.g. USDT or USDC) should be used for the buy order (Example: 0.05 = invest 5 % of your available quote balance.).
- buy_funds_amount: An explicit numeric value specifying the exact quote asset amount (e.g. USDT) to spend on the buy order. Must not exceed your available quote asset balance.
- buy_crypto_amount: An explicit numeric value specifying the exact base asset amount to buy (e.g. buy 77 ADA or 1.2 ETH). The system automatically calculates how much quote asset (USDT, USDC, etc.) is needed for the purchase based on the current market price.
- sell_crypto_pct: A decimal fraction (0 < sell_crypto_pct ≤ 1) indicating what fraction of your available base asset (e.g. ADA in ADAUSDT, ETH in ETHUSDC) should be sold. (Example: 0.25 = sell 25 % of your ADA holdings.)
- sell_crypto_amount: An explicit numeric value specifying the exact base asset amount to sell. Must not exceed your available base asset balance.
- sell_funds_amount: An explicit numeric value specifying the exact quote asset amount you want to receive from the sale (e.g. sell ETH worth 150 USDT). The system automatically calculates how much of the base asset must be sold to reach this target.
- client_secret: Defines your personally defined client secret for authentication.

Rules:
- If the action is "buy" or "sell", exactly one of the corresponding fields must be supplied:
  - For BUY orders:
    - buy_funds_pct – percentage of quote balance to use, or
    - buy_funds_amount – exact quote amount to spend, or
    - buy_crypto_amount – exact base asset amount to buy
  - For SELL orders:
    - sell_crypto_pct – percentage of base holdings to sell, or
    - sell_crypto_amount – exact base asset amount to sell, or
    - sell_funds_amount – target quote amount to receive
- If multiple fields for the same side are present → payload is rejected.
- If none of the valid fields are provided → payload is rejected.



## ⚙️ Six trading input types
Type	              Direction Meaning	                                    Example
buy_funds_amount	  Buy	      Spend a fixed amount of funds	              Buy BTC with 100 USDT
buy_funds_pct	      Buy	      Spend a % of available funds	              Buy BTC with 50 % of USDT
buy_crypto_amount	  Buy	      Acquire a fixed number of coins	            Buy 5 ADA
sell_crypto_amount  Sell	    Sell a fixed number of coins	              Sell 0.05 BTC
sell_crypto_pct     Sell	    Sell a % of your holdings	                  Sell 80 % of ADA
sell_funds_amount	  Sell      Sell enough to get a fixed amount of funds	Sell BTC worth 20 USDT



## Redis Cache Endpoints
🔓 Public Endpoints
Endpoint                  Method  Description
/cache/prices             GET     Returns all cached prices (price_cache hash).
/cache/prices/count	      GET     Returns the number of cached symbols in price_cache.
/cache/prices/<symbol>    GET     Returns the cached mid-price for a specific symbol.
/cache/filters            GET     Returns cached symbol filters (minQty, minNotional, etc.) for all pairs.
/cache/filters/<symbol>   GET     Returns cached filters for a specific symbol.
/cache/summary            GET     Returns a structured summary of cache state:

🔐 Admin-Protected Endpoint
Endpoint                  Method  Description
/cache/balances           GET     Returns cached Binance account balances (from account_balances key).

To access this endpoint securely, you must include your admin key in the request header:
curl -H "X-Admin-Key: <ADMIN_API_KEY>" https://<your-web-service-name>.onrender.com/cache/balances
