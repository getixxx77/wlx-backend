from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from stellar_sdk import Server, Keypair, Asset
from pymongo import MongoClient
import os

# Load environment variables (example: in Render Dashboard)
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://mercerbear_db_user:G1HAosnfSOG4ZhWo@wlx.5ytqctl.mongodb.net/?retryWrites=true&w=majority")
ASSET_CODE = os.getenv("ASSET_CODE", "WLX")
ASSET_ISSUER = os.getenv("ASSET_ISSUER", "GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")  # replace with valid Stellar public key

# MongoDB client
client = MongoClient(MONGO_URI)
db = client["wlx_db"]
transactions_collection = db["transactions"]

# Stellar server
HORIZON_URL = "https://horizon.stellar.org"
stellar_server = Server(horizon_url=HORIZON_URL)

# FastAPI app
app = FastAPI()

# Enable CORS for your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://wlxdao.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class WalletRequest(BaseModel):
    public_key: str

class TransactionRequest(BaseModel):
    public_key: str
    amount: float

# Initialize Stellar asset
try:
    wlx_asset = Asset(ASSET_CODE, ASSET_ISSUER)
except Exception:
    raise ValueError("ASSET_ISSUER must be a valid Stellar public key (G...)")

# Helper: calculate daily return by tier
def calculate_daily_return(balance):
    balance = float(balance)
    if 200 <= balance <= 598:
        return "0.274–0.819 XLM"
    elif 600 <= balance <= 1998:
        return "0.822–2.739 XLM"
    elif 2000 <= balance <= 5998:
        return "2.740–8.217 XLM"
    elif 6000 <= balance <= 19998:
        return "8.219–27.397 XLM"
    elif 20000 <= balance <= 49998:
        return "27.397–68.475 XLM"
    elif 50000 <= balance <= 99998:
        return "68.475–136.986 XLM"
    elif 100000 <= balance <= 200000:
        return "137–274 XLM"
    elif 240000 <= balance <= 400000:
        return "329–548 XLM"
    elif balance > 400000:
        return "548+ XLM"
    else:
        return "Contact Support"

# Endpoint: check balance
@app.post("/api/wallet/balance")
async def get_balance(wallet: WalletRequest):
    public_key = wallet.public_key
    if not public_key.startswith("G") or len(public_key) != 56:
        raise HTTPException(status_code=400, detail="Invalid Stellar public key.")

    try:
        account = stellar_server.accounts().account_id(public_key).call()
        balances = {b['asset_type']: b['balance'] for b in account['balances']}
        # native = XLM
        native_balance = balances.get("native", "0")
        return {"balance": native_balance}
    except Exception:
        raise HTTPException(status_code=400, detail="Account not found or error fetching balance.")

# Endpoint: get tier and daily return
@app.post("/api/wallet/tier")
async def get_tier(wallet: WalletRequest):
    try:
        account = stellar_server.accounts().account_id(wallet.public_key).call()
        balances = {b['asset_code'] if 'asset_code' in b else b['asset_type']: b['balance'] for b in account['balances']}
        wlx_balance = float(balances.get(ASSET_CODE, 0))
        daily_return = calculate_daily_return(wlx_balance)
        return {"wlx_balance": wlx_balance, "daily_return": daily_return}
    except Exception:
        raise HTTPException(status_code=400, detail="Error fetching tier info.")

# Endpoint: record a transaction (example: deposit)
@app.post("/api/transactions")
async def record_transaction(tx: TransactionRequest):
    transaction = {
        "public_key": tx.public_key,
        "amount": tx.amount
    }
    transactions_collection.insert_one(transaction)
    return {"status": "success", "transaction": transaction}

# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
