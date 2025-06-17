import asyncio
import os
import json
import logging
import base64 # For decoding transaction strings from Jupiter
from decimal import Decimal

# Ensure you have '''pip install solders solana python-dotenv openai websockets httpx'''
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction # For Jupiter transactions
from solana.rpc.api import Client
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts, Commitment
from dotenv import load_dotenv
from openai import OpenAI # Using OpenAI library structure for OpenRouter

# --- Configuration ---
load_dotenv()

# Solana RPC and WebSocket URLs - Use your QuickNode and Helius URLs
# Ensure these are HTTP/S for the AsyncClient, WSS for dedicated WebSocket if used separately
QUICKNODE_RPC_URL = os.getenv("QUICKNODE_RPC_URL", "https://api.mainnet-beta.solana.com") # Replace with your QuickNode HTTP endpoint
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com") # Replace with your Helius HTTP endpoint
# Choose one primary RPC for general client. Helius can offer richer APIs.
SOLANA_HTTP_RPC_URL = HELIUS_RPC_URL # Or QUICKNODE_RPC_URL
# SOLANA_WS_RPC_URL = os.getenv("SOLANA_WS_RPC_URL", "wss://api.mainnet-beta.solana.com/") # If direct WebSocket use is needed beyond basic price polling

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
# CRITICAL: Private key placeholder.
# Store your private key as a comma-separated list of bytes in your .env file.
# Example: WALLET_PRIVATE_KEY_PLACEHOLDER="1,2,3,4,5,..."
# DO NOT COMMIT YOUR ACTUAL PRIVATE KEY TO ANY VERSION CONTROL.
WALLET_PRIVATE_KEY_PLACEHOLDER = os.getenv("WALLET_PRIVATE_KEY_PLACEHOLDER", "YOUR_PRIVATE_KEY_BYTES_AS_STRING_HERE")

# Arbitrage Configuration
TOKEN_PAIRS_TO_MONITOR = [
    # SOL / USDC
    {"name": "SOL/USDC", "token_a_mint": "So11111111111111111111111111111111111111112", "token_a_decimals": 9, "token_b_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "token_b_decimals": 6, "trade_amount_token_a": Decimal("0.1")}, # Trade 0.1 SOL
    # Add more pairs, e.g., USDC/USDT, ensuring mint addresses and decimals are correct
    # {"name": "USDC/USDT", "token_a_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "token_a_decimals": 6, "token_b_mint": "Es9vMFrzaCERmJfrF4H2FGMmdnsQefdWNoyCyomXWcBA", "token_b_decimals": 6, "trade_amount_token_a": Decimal("10")}, # Trade 10 USDC
]
MIN_PROFIT_THRESHOLD_USD = Decimal("0.05") # Minimum expected profit in USD (after estimated fees)
SLIPPAGE_BPS = 50 # Default slippage tolerance: 0.5% (50 BPS)
JUPITER_API_VERSION = "v6" # Check and use the latest Jupiter API version

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Initialize Solana Clients & AI Client ---
try:
    async_http_client = AsyncClient(SOLANA_HTTP_RPC_URL, commitment=Commitment("confirmed"))
    # http_client = Client(SOLANA_HTTP_RPC_URL) # Sync client if needed for some operations
except Exception as e:
    logging.error(f"Failed to initialize Solana AsyncClient: {e}. Check RPC URL.")
    exit(1)

if OPENROUTER_API_KEY:
    openrouter_client = OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
else:
    logging.warning("OPENROUTER_API_KEY not found. AI assessment will be skipped.")
    openrouter_client = None

# --- Wallet Setup ---
payer_keypair = None
if WALLET_PRIVATE_KEY_PLACEHOLDER and "YOUR_PRIVATE_KEY" not in WALLET_PRIVATE_KEY_PLACEHOLDER:
    try:
        private_key_bytes = bytes(map(int, WALLET_PRIVATE_KEY_PLACEHOLDER.split(',')))
        payer_keypair = Keypair.from_bytes(private_key_bytes)
        logging.info(f"Wallet loaded. Public Key: {payer_keypair.pubkey()}")
    except Exception as e:
        logging.error(f"Failed to load wallet from WALLET_PRIVATE_KEY_PLACEHOLDER: {e}. Ensure it's a comma-separated byte array string (e.g., '1,2,3,...'). Bot cannot execute trades.")
else:
    logging.warning("WALLET_PRIVATE_KEY_PLACEHOLDER is not set or is using the default placeholder. Bot cannot execute real trades.")

# --- Jupiter API Interaction (Requires `httpx` library: pip install httpx) ---
JUPITER_API_BASE_URL = f"https://quote-api.jup.ag/{JUPITER_API_VERSION}"
# You MUST install httpx: pip install httpx
import httpx # Import here to ensure it's clear it's a new dependency
jupiter_http_client = httpx.AsyncClient()


async def get_jupiter_quote(input_mint_str: str, output_mint_str: str, amount_wei: int, slippage_bps: int = SLIPPAGE_BPS):
    """Gets a quote from Jupiter API."""
    url = f"{JUPITER_API_BASE_URL}/quote"
    params = {
        "inputMint": input_mint_str,
        "outputMint": output_mint_str,
        "amount": amount_wei,
        "slippageBps": slippage_bps,
        "onlyDirectRoutes": "false", # Consider all routes for best price
    }
    try:
        response = await jupiter_http_client.get(url, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors
        return response.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"Error fetching Jupiter quote ({input_mint_str}->{output_mint_str}): {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        logging.error(f"Request error fetching Jupiter quote: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in get_jupiter_quote: {e}")
    return None

async def get_jupiter_swap_tx(user_public_key_str: str, quote_response: dict):
    """Gets swap transaction from Jupiter API."""
    if not user_public_key_str:
        logging.error("User public key is required to get swap transaction.")
        return None
    url = f"{JUPITER_API_BASE_URL}/swap"
    payload = {
        "userPublicKey": user_public_key_str,
        "quoteResponse": quote_response,
        "wrapAndUnwrapSol": True, # Automatically wrap/unwrap SOL
        "computeUnitPriceMicroLamports": "auto", # Or set a specific priority fee
        # "asLegacyTransaction": "false", # Jupiter v6 defaults to VersionedTransaction
    }
    try:
        response = await jupiter_http_client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"Error fetching Jupiter swap transaction: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        logging.error(f"Request error fetching Jupiter swap transaction: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in get_jupiter_swap_tx: {e}")
    return None

# --- Arbitrage Logic ---
async def find_arbitrage_opportunity_for_pair(pair_config: dict):
    """Checks a single token pair for arbitrage opportunities."""
    token_a_mint = pair_config["token_a_mint"]
    token_a_decimals = pair_config["token_a_decimals"]
    token_b_mint = pair_config["token_b_mint"]
    token_b_decimals = pair_config["token_b_decimals"]
    # Amount of token A to start with, converted to its smallest unit (wei)
    amount_a_wei = int(pair_config["trade_amount_token_a"] * (10**token_a_decimals))

    logging.info(f"Scanning {pair_config['name']}: {pair_config['trade_amount_token_a']} {token_a_mint}...")

    # 1. Trade Token A for Token B
    quote_a_to_b = await get_jupiter_quote(token_a_mint, token_b_mint, amount_a_wei)
    if not quote_a_to_b or not quote_a_to_b.get("outAmount"):
        logging.warning(f"Could not get quote for {token_a_mint} -> {token_b_mint}")
        return None
    
    amount_b_received_wei = int(quote_a_to_b["outAmount"])
    amount_b_received_units = Decimal(amount_b_received_wei) / (10**token_b_decimals)
    logging.info(f"Quote A->B: {pair_config['trade_amount_token_a']} {token_a_mint} -> {amount_b_received_units:.{token_b_decimals}f} {token_b_mint}")

    # 2. Trade Token B (amount received from first trade) back to Token A
    quote_b_to_a = await get_jupiter_quote(token_b_mint, token_a_mint, amount_b_received_wei)
    if not quote_b_to_a or not quote_b_to_a.get("outAmount"):
        logging.warning(f"Could not get quote for {token_b_mint} -> {token_a_mint} with {amount_b_received_wei} wei")
        return None

    amount_a_returned_wei = int(quote_b_to_a["outAmount"])
    amount_a_returned_units = Decimal(amount_a_returned_wei) / (10**token_a_decimals)
    logging.info(f"Quote B->A: {amount_b_received_units:.{token_b_decimals}f} {token_b_mint} -> {amount_a_returned_units:.{token_a_decimals}f} {token_a_mint}")

    # Calculate profit in terms of Token A
    profit_a_wei = amount_a_returned_wei - amount_a_wei
    profit_a_units = Decimal(profit_a_wei) / (10**token_a_decimals)

    # Estimate profit in USD (very rough, needs a real-time price feed for Token A vs USD)
    # This is a placeholder. You'd fetch SOL/USD or TOKEN_A/USD price from an oracle or API.
    token_a_price_usd = Decimal("130.0") if token_a_mint == "So11111111111111111111111111111111111111112" else Decimal("1.0") # Example prices
    profit_usd_estimate = profit_a_units * token_a_price_usd

    logging.info(f"Potential profit for {pair_config['name']}: {profit_a_units:.{token_a_decimals}f} {token_a_mint} (~${profit_usd_estimate:.4f})")

    if profit_usd_estimate > MIN_PROFIT_THRESHOLD_USD:
        logging.info(f"Arbitrage opportunity DETECTED for {pair_config['name']}! Profit: ~${profit_usd_estimate:.4f}")
        return {
            "pair_name": pair_config['name'],
            "profit_usd_estimate": profit_usd_estimate,
            "profit_token_a_units": profit_a_units,
            "token_a_mint": token_a_mint,
            "quote_a_to_b": quote_a_to_b,
            "quote_b_to_a": quote_b_to_a
        }
    return None

# --- AI Assessment ---
async def assess_opportunity_with_ai(opportunity_details: dict):
    if not openrouter_client:
        logging.info("AI assessment skipped: OpenRouter client not initialized.")
        return "EXECUTE" # Default to execute if AI is not available/configured

    prompt = f"""Analyze the following Solana arbitrage opportunity:
    Pair: {opportunity_details['pair_name']}
    Estimated USD Profit: ${opportunity_details['profit_usd_estimate']:.4f}
    Profit in Token A ({opportunity_details['token_a_mint']}): {opportunity_details['profit_token_a_units']:.8f}
    Trade 1 (A->B) Quote: {json.dumps(opportunity_details['quote_a_to_b'])}
    Trade 2 (B->A) Quote: {json.dumps(opportunity_details['quote_b_to_a'])}

    Considering potential risks like network congestion, actual slippage differing from quote, and smart contract risks (though Jupiter is reputable), should I proceed with this arbitrage?
    Respond with only 'EXECUTE' or 'HOLD'. Add a very brief one-sentence reason after the command on a new line.
    Example:
    EXECUTE
    Profit margin seems sufficient given the trade size.
    """
    try:
        logging.info("Asking AI to assess opportunity...")
        completion = openrouter_client.chat.completions.create(
            model="openai/gpt-3.5-turbo", # Or your preferred OpenRouter model like "mistralai/mistral-7b-instruct"
            messages=[
                {"role": "system", "content": "You are a cautious AI assistant for a Solana arbitrage trading bot."},
                {"role": "user", "content": prompt}
            ]
        )
        ai_response_text = completion.choices[0].message.content.strip()
        logging.info(f"AI Assessment: {ai_response_text}")
        if ai_response_text.upper().startswith("EXECUTE"):
            return "EXECUTE"
        return "HOLD"
    except Exception as e:
        logging.error(f"Error during AI assessment: {e}")
        return "HOLD" # Default to hold if AI fails

# --- Trade Execution (CRITICAL IMPLEMENTATION NEEDED) ---
async def execute_arbitrage_trade(opportunity: dict):
    if not payer_keypair:
        logging.error("Cannot execute trade: Wallet not loaded.")
        return False

    logging.info(f"Attempting to execute arbitrage for {opportunity['pair_name']}...")
    user_pubkey_str = str(payer_keypair.pubkey())

    # Trade 1: Token A -> Token B
    quote1 = opportunity['quote_a_to_b']
    logging.info(f"Executing trade 1 (A->B) for {opportunity['pair_name']}")
    swap_tx_payload1 = await get_jupiter_swap_tx(user_pubkey_str, quote1)
    if not swap_tx_payload1 or not swap_tx_payload1.get("swapTransaction"):
        logging.error("Failed to get swap transaction for trade 1.")
        return False
    
    tx_signature1 = await sign_and_send_jupiter_tx(swap_tx_payload1["swapTransaction"], "Trade 1 (A->B)")
    if not tx_signature1:
        logging.error("Trade 1 failed or was not confirmed.")
        # CRITICAL: Consider if you need logic to handle partial success (e.g., if trade 1 executes but trade 2 fails)
        # This is complex and might involve trying to swap back or accepting the current state.
        return False
    logging.info(f"Trade 1 (A->B) successful. Signature: {tx_signature1}")

    # Important: Small delay and/or balance check before initiating trade 2
    await asyncio.sleep(5) # Give some time for state to update; a balance check is better

    # Trade 2: Token B -> Token A
    quote2 = opportunity['quote_b_to_a']
    logging.info(f"Executing trade 2 (B->A) for {opportunity['pair_name']}")
    swap_tx_payload2 = await get_jupiter_swap_tx(user_pubkey_str, quote2)
    if not swap_tx_payload2 or not swap_tx_payload2.get("swapTransaction"):
        logging.error("Failed to get swap transaction for trade 2.")
        # If trade 1 succeeded, you are now holding Token B. This is a risk.
        # You might try to sell Token B back to SOL or USDC as a fallback.
        return False # Or implement fallback logic

    tx_signature2 = await sign_and_send_jupiter_tx(swap_tx_payload2["swapTransaction"], "Trade 2 (B->A)")
    if not tx_signature2:
        logging.error("Trade 2 failed or was not confirmed.")
        # Trade 1 succeeded, but Trade 2 failed. You are holding Token B.
        return False # Or implement fallback logic
    
    logging.info(f"Trade 2 (B->A) successful. Signature: {tx_signature2}")
    logging.info(f"Arbitrage for {opportunity['pair_name']} fully executed successfully!")
    return True

async def sign_and_send_jupiter_tx(base64_encoded_transaction: str, tx_description: str):
    """Signs and sends a VersionedTransaction received from Jupiter API."""
    if not payer_keypair:
        logging.error(f"Cannot sign transaction '{tx_description}': Wallet not loaded.")
        return None
    try:
        tx_data = base64.b64decode(base64_encoded_transaction)
        versioned_tx = VersionedTransaction.from_bytes(tx_data)
        
        # Sign the transaction with your keypair
        versioned_tx.sign([payer_keypair])
        
        serialized_tx = versioned_tx.serialize()

        logging.info(f"Sending transaction: {tx_description}")
        # Send the transaction (using confirmed commitment for this example)
        # For faster execution, you might use "processed" and then confirm separately.
        opts = TxOpts(skip_preflight=False, preflight_commitment=Commitment("confirmed"), max_retries=3)
        
        resp = await async_http_client.send_raw_transaction(serialized_tx, opts=opts)
        tx_signature = resp.value
        logging.info(f"Transaction '{tx_description}' sent. Signature: {tx_signature}. Waiting for confirmation...")
        
        # Wait for confirmation
        await async_http_client.confirm_transaction(
            tx_signature,
            commitment=Commitment("confirmed"),
            sleep_seconds=5, # How often to poll
            last_valid_block_height=versioned_tx.message.recent_blockhash_obj.last_valid_block_height # from quote response or fetched
        )
        logging.info(f"Transaction '{tx_description}' confirmed. Signature: {tx_signature}")
        return tx_signature

    except Exception as e:
        logging.error(f"Error signing/sending Jupiter transaction '{tx_description}': {e}", exc_info=True)
        return None

# --- Main Bot Loop ---
async def arbitrage_processing_loop():
    logging.info("Starting Solana AI Arbitrage Bot...")
    if not payer_keypair:
        logging.warning("Bot is running without a loaded wallet. It will only scan for opportunities, not execute trades.")
    
    while True:
        logging.info("--- New arbitrage scan cycle ---")
        for pair_config in TOKEN_PAIRS_TO_MONITOR:
            try:
                opportunity = await find_arbitrage_opportunity_for_pair(pair_config)
                if opportunity:
                    ai_decision = await assess_opportunity_with_ai(opportunity)
                    if ai_decision == "EXECUTE":
                        if payer_keypair: # Double check wallet before execution attempt
                            await execute_arbitrage_trade(opportunity)
                        else:
                            logging.warning(f"AI approved execution for {opportunity['pair_name']}, but wallet is not loaded. Skipping trade.")
                    else:
                        logging.info(f"AI recommended HOLD for {opportunity['pair_name']}.")
                await asyncio.sleep(5) # Small delay between checking different pairs to avoid rate limits
            except Exception as e:
                logging.error(f"Error processing pair {pair_config.get('name', 'UnknownPair')}: {e}", exc_info=True)
        
        scan_interval_seconds = 30
        logging.info(f"Scan cycle complete. Waiting for {scan_interval_seconds} seconds...")
        await asyncio.sleep(scan_interval_seconds)

async def amain(): # Renamed original main to amain to avoid conflict
    try:
        await arbitrage_processing_loop()
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
    finally:
        if async_http_client:
            await async_http_client.close()
        if jupiter_http_client:
            await jupiter_http_client.aclose()
        logging.info("Bot shut down gracefully.")

if __name__ == "__main__":
    # Check critical environment variables
    if not all([QUICKNODE_RPC_URL or HELIUS_RPC_URL, OPENROUTER_API_KEY]):
        logging.error("One or more critical environment variables are missing (RPC URL, OPENROUTER_API_KEY). Please check your .env file.")
        # exit(1) # Allow to run without AI key for scanning only
    if WALLET_PRIVATE_KEY_PLACEHOLDER == "YOUR_PRIVATE_KEY_BYTES_AS_STRING_HERE":
         logging.warning("Using the default placeholder private key string. Bot will not execute real trades. Update .env with your actual key bytes if you intend to trade.")

    asyncio.run(amain())
