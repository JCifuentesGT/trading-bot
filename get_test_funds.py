from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os

load_dotenv()

client = HTTP(
    testnet=True,
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)

try:
    result = client.create_demo_applied_money(
        accountType="UNIFIED",
        amount="10000",
        coin="USDT"
    )
    print("Fondos acreditados!")
    print(result)
except Exception as e:
    print(f"Error: {e}")
