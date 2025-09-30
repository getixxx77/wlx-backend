from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from stellar_sdk import Keypair, Server, Asset
from stellar_sdk.exceptions import NotFoundError, ConnectionError as StellarConnectionError
from pathlib import Path
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime
import os
import uuid
import logging

# Load .env if present
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Read environment variables
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "wlx")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
STELLAR_HORIZON_URL = os.environ.get("STELLAR_RPC_URL", "https://horizon.stellar.org")
NETWORK_PASSPHRASE = os.environ.get("NETWORK_PASSPHRASE", "Public Global Stellar Network ; September 2015")
ASSET_CODE = os.environ.get("ASSET_CODE", "WLX")
ASSET_ISSUER = os.environ.get("ASSET_ISSUER")

# Validate ASSET_ISSUER
if not ASSET_ISSUER or not ASSET_ISSUER.startswith("G"):
    raise ValueError("ASSET_ISSUER must be a valid Stellar public key (G...)")

logger.info(f"ASSET_ISSUER: {ASSET_ISSUER}")

# MongoDB setup
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# FastAPI app
app = FastAPI(title="WhiplashXLM Bot API", version="1.0.0")
api_router = APIRouter(prefix="/api")

# Pydantic models
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

class WalletBalanceRequest(BaseModel):
    public_key: str = Field(..., description="Stellar account public key")
    
    @validator("public_key")
    def validate_public_key(cls, v):
        try:
            Keypair.from_public_key(v)
            return v
        except Exception:
            raise ValueError("Invalid Stellar public key format")

class AssetBalance(BaseModel):
    asset_code: str
    asset_issuer: Optional[str] = None
    balance: str
    asset_type: str
    limit: Optional[str] = None

class WalletBalanceResponse(BaseModel):
    account_id: str
    native_balance: str
    wlx_balance: Optional[str] = None
    has_wlx: bool = False
    all_balances: List[AssetBalance]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ErrorResponse(BaseModel):
    error: str
    detail: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# Stellar service
class StellarService:
    def __init__(self):
        self.server = Server(STELLAR_HORIZON_URL)
        self.wlx_asset = Asset(ASSET_CODE, ASSET_ISSUER)
        logger.info(f"Initialized Stellar service with {STELLAR_HORIZON_URL}")

    async def get_account_balances(self, public_key: str) -> WalletBalanceResponse:
        try:
            logger.info(f"Fetching balances for account: {public_key}")
            account_response = self.server.accounts().account_id(public_key).call()

            all_balances = []
            native_balance = "0"
            wlx_balance = None
            has_wlx = False

            for balance in account_response["balances"]:
                asset_balance = AssetBalance(
                    asset_code=balance.get("asset_code", "XLM"),
                    asset_issuer=balance.get("asset_issuer"),
                    balance=balance["balance"],
                    asset_type=balance["asset_type"],
                    limit=balance.get("limit")
                )
                all_balances.append(asset_balance)

                if balance["asset_type"] == "native":
                    native_balance = balance["balance"]

                if balance.get("asset_code") == ASSET_CODE and balance.get("asset_issuer") == ASSET_ISSUER:
                    wlx_balance = balance["balance"]
                    has_wlx = True
                    logger.info(f"Found WLX balance: {wlx_balance}")

            return WalletBalanceResponse(
                account_id=account_response["account_id"],
                native_balance=native_balance,
                wlx_balance=wlx_balance,
                has_wlx=has_wlx,
                all_balances=all_balances
            )

        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"Account {public_key} not found")
        except StellarConnectionError:
            raise HTTPException(status_code=503, detail="Unable to connect to Stellar network")
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

stellar_service = StellarService()
def get_stellar_service() -> StellarService:
    return stellar_service

# API routes
@api_router.get("/")
async def root():
    return {"message": "WhiplashXLM Bot API - Ready to track WLX assets!"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.dict())
    await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**s) for s in status_checks]

@api_router.post("/wallet/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(request: WalletBalanceRequest, stellar_service: StellarService = Depends(get_stellar_service)):
    return await stellar_service.get_account_balances(request.public_key)

@api_router.get("/wallet/{public_key}/wlx")
async def get_wlx_balance(public_key: str, stellar_service: StellarService = Depends(get_stellar_service)):
    try:
        Keypair.from_public_key(public_key)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Stellar public key format")
    balance_data = await stellar_service.get_account_balances(public_key)
    return {
        "account_id": public_key,
        "wlx_balance": balance_data.wlx_balance,
        "has_wlx": balance_data.has_wlx,
        "native_balance": balance_data.native_balance,
        "timestamp": datetime.utcnow()
    }

@api_router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "service": "whiplash-xlm-bot-api",
        "stellar_network": "mainnet"
    }

# Include router
app.include_router(api_router)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global exception caught: {exc}")
    return ErrorResponse(error="Internal Server Error", detail=str(exc))

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

