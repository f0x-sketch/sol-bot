# sol-bot
Solana AI Arbitrage Trading Bot

This is a Solana trading bot that uses Jupiter Aggregator for swaps and OpenRouter API for AI-powered trading decisions.

## Features
- Fetches quotes from Jupiter API V6
- Executes swaps using Jupiter API V6
- Uses OpenRouter API for AI analysis
- Securely manages wallet private key via environment variable (`WGRS_PRIVATE_KEY`)
- Asynchronous operations for efficiency
- Identifies arbitrage opportunities between two target tokens, potentially via USDC.

## Prerequisites
- Python 3.9+
- Solana account with some SOL for fees and trading capital
- API keys for:
    - QuickNode (or other Solana RPC provider)
    - Helius (for WebSockets, or alternative)
    - OpenRouter API

## Setup
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/f0x-sketch/sol-bot.git
    cd sol-bot
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up environment variables:**
    Create a `.env` file in the root directory by copying the example:
    ```bash
    cp .env.example .env
    ```
    Edit the `.env` file with your actual credentials and parameters:
    ```
    # Solana RPC Endpoints
    QUICKNODE_HTTP_URL="YOUR_QUICKNODE_HTTP_ENDPOINT"
    HELIUS_WSS_URL="YOUR_HELIUS_WSS_ENDPOINT" # Or your preferred WebSocket provider

    # API Keys
    OPENROUTER_API_KEY="sk-or-v1-..." # Your OpenRouter API Key

    # Wallet (IMPORTANT: Secure this key. This is a placeholder format.)
    # For development, you might use a burner wallet.
    # NEVER commit your actual private key to the repository.
    # The .gitignore file is configured to ignore .env files.
    WGRS_PRIVATE_KEY="YOUR_WALLET_PRIVATE_KEY_BYTE_ARRAY_AS_STRING" # e.g., "[10,23,45,...,67]"

    # Trading Parameters
    TARGET_TOKEN_A_MINT="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" # Default: USDC
    TARGET_TOKEN_B_MINT="So11111111111111111111111111111111111111112" # Default: SOL
    MIN_USD_PROFIT_THRESHOLD="1.0" # Minimum estimated profit in USD to consider a trade
    ```
    **Security Note:** Ensure your `.env` file is listed in your `.gitignore` file (it is by default in this project, and `.env` itself is explicitly ignored) and never commit it to your repository, especially if it contains sensitive information like private keys.

## How It Works
The bot monitors market prices for `TARGET_TOKEN_A` and `TARGET_TOKEN_B` using the Jupiter API to identify potential arbitrage opportunities. It primarily evaluates the following scenarios to maximize one of the target tokens or achieve a USD profit:

1.  **Triangular Arbitrage via USDC:**
    *   `TARGET_TOKEN_A` → `USDC` → `TARGET_TOKEN_B`
    *   `TARGET_TOKEN_B` → `USDC` → `TARGET_TOKEN_A`
2.  **Direct Swaps (especially if one target token is USDC or for direct comparison):**
    *   `TARGET_TOKEN_A` → `TARGET_TOKEN_B`
    *   `TARGET_TOKEN_B` → `TARGET_TOKEN_A`

The goal is to find a sequence of swaps that results in a greater amount of the desired token, with the profit estimated in USD and compared against `MIN_USD_PROFIT_THRESHOLD`.

When a potential arbitrage opportunity is identified:
1.  It fetches precise quotes from Jupiter for the chosen trading path.
2.  It constructs a prompt for the OpenRouter API, detailing the potential trade (tokens, expected profit, etc.).
3.  An AI model via OpenRouter assesses the trade's viability (e.g., considering if the profit is significant enough or if there are hidden risks not captured by simple price differences).
4.  If the AI confirms the trade with a "YES" response, the bot will attempt to execute the swap(s) using the Jupiter Swap API.

**Note on Execution:** The current implementation focuses on identifying opportunities and getting AI confirmation. The actual transaction submission logic (`send_and_confirm_transaction` in `main.py`) is a critical placeholder. It needs to be fully implemented, including robust error handling, transaction signing with the `WGRS_PRIVATE_KEY`, and confirmation, before use with real funds.

## Running the Bot
```bash
python main.py
```
The bot will start logging its activities, including price checks, potential opportunities found, and AI decisions.

## Disclaimer
-   **Use at your own risk.** Trading cryptocurrencies, especially with automated bots, is highly risky.
-   This bot is for educational and experimental purposes.
-   **Never use funds you cannot afford to lose.**
-   Ensure you understand the code and the risks involved before running this bot with real capital.
-   The security of your private keys is your responsibility. Always use a separate, burner wallet for development and testing.

## TODO / Future Enhancements
-   [ ] **Full Transaction Execution:** Implement robust transaction signing and sending with error handling and retries.
-   [ ] **Advanced AI Prompts:** Improve AI prompts with more market data (e.g., recent price volatility, order book depth if accessible).
-   [ ] **Multi-Path Arbitrage:** Explore more complex arbitrage paths (e.g., A -> B -> C -> A).
-   [ ] **Dynamic Slippage Control:** Adjust slippage based on market conditions or AI output.
-   [ ] **Gas Fee Optimization:** Consider gas fees in profit calculation and execution strategy.
-   [ ] **Expanded DEX Support:** While Jupiter aggregates, direct integration with specific DEXs might offer advantages in some scenarios.
-   [ ] **Backtesting Framework:** Develop a system to test strategies against historical data.
-   [ ] **Improved Logging and Monitoring:** Integrate more comprehensive logging and potentially a dashboard.
-   [ ] **Risk Management:** Implement stricter risk controls (e.g., max trade size, daily loss limits).
