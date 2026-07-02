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
    balance = client.get_wallet_balance(accountType="UNIFIED")
    print("Conexion exitosa!")
    print(f"Balance: {balance['result']}")
except Exception as e:
    print(f"Error: {e}")
