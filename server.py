from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime

# Stellar SDK imports
from stellar_sdk import Keypair, Server, Asset
from stellar_sdk.exceptions import NotFoundError, ConnectionError as StellarConnectionError

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Stellar service configuration
STELLAR_HORIZON_URL = "https://horizon.stellar.org"
WLX_ISSUER = "GBG5YTLEZ6PLZ33TIF2IGYPAEJ66ES4A5JOX7SRQVEZY55WRACDEWZPV"

# Create the main app
app = FastAPI(title="WhiplashXLM Bot API", version="1.0.0")

# Create a router with the /api prefix
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
    
    @validator('public_key')
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

# Stellar service class
class StellarService:
    def __init__(self):
        self.server = Server(STELLAR_HORIZON_URL)
        self.wlx_asset = Asset("WLX", WLX_ISSUER)
        logger.info(f"Initialized Stellar service with {STELLAR_HORIZON_URL}")
    
    async def get_account_balances(self, public_key: str) -> WalletBalanceResponse:
        """Get account balances including WLX asset check."""
        try:
            logger.info(f"Loading account balances for {public_key}")
            
            # Get account data through API call to get balances
            account_response = self.server.accounts().account_id(public_key).call()
            logger.info(f"Account response received with {len(account_response['balances'])} balances")
            
            # Parse balances
            all_balances = []
            native_balance = "0"
            wlx_balance = None
            has_wlx = False
            
            for balance in account_response['balances']:
                asset_balance = AssetBalance(
                    asset_code=balance.get('asset_code', 'XLM'),
                    asset_issuer=balance.get('asset_issuer'),
                    balance=balance['balance'],
                    asset_type=balance['asset_type'],
                    limit=balance.get('limit')
                )
                all_balances.append(asset_balance)
                
                # Check for native XLM
                if balance['asset_type'] == 'native':
                    native_balance = balance['balance']
                
                # Check for WLX asset
                if (balance.get('asset_code') == 'WLX' and 
                    balance.get('asset_issuer') == WLX_ISSUER):
                    wlx_balance = balance['balance']
                    has_wlx = True
                    logger.info(f"Found WLX balance: {wlx_balance}")
            
            response = WalletBalanceResponse(
                account_id=account_response['account_id'],
                native_balance=native_balance,
                wlx_balance=wlx_balance,
                has_wlx=has_wlx,
                all_balances=all_balances
            )
            
            logger.info(f"Successfully retrieved balances for account. WLX found: {has_wlx}")
            return response
            
        except NotFoundError:
            logger.error(f"Account not found: {public_key}")
            raise HTTPException(
                status_code=404,
                detail=f"Account {public_key} not found on Stellar network. Please verify the public key is correct."
            )
        except StellarConnectionError as e:
            logger.error(f"Stellar network connection error: {str(e)}")
            raise HTTPException(
                status_code=503,
                detail="Unable to connect to Stellar network. Please try again later."
            )
        except Exception as e:
            logger.error(f"Unexpected error loading account: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error occurred while fetching balance."
            )

# Initialize Stellar service
stellar_service = StellarService()

def get_stellar_service() -> StellarService:
    return stellar_service

# Original routes
@api_router.get("/")
async def root():
    return {"message": "WhiplashXLM Bot API - Ready to track your WLX assets!"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# New Stellar balance routes
@api_router.post("/wallet/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(
    request: WalletBalanceRequest,
    stellar_service: StellarService = Depends(get_stellar_service)
) -> WalletBalanceResponse:
    """Get wallet balance including WLX asset information."""
    logger.info(f"Balance request received for account: {request.public_key}")
    
    try:
        balance_data = await stellar_service.get_account_balances(request.public_key)
        return balance_data
        
    except HTTPException:
        # Re-raise FastAPI HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in balance endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )

@api_router.get("/wallet/{public_key}/wlx")
async def get_wlx_balance(
    public_key: str,
    stellar_service: StellarService = Depends(get_stellar_service)
) -> Dict[str, Any]:
    """Get specific WLX balance for a Stellar account."""
    logger.info(f"WLX balance request for account: {public_key}")
    
    # Validate public key format
    try:
        Keypair.from_public_key(public_key)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stellar public key format"
        )
    
    try:
        balance_data = await stellar_service.get_account_balances(public_key)
        
        return {
            "account_id": public_key,
            "wlx_balance": balance_data.wlx_balance,
            "has_wlx": balance_data.has_wlx,
            "native_balance": balance_data.native_balance,
            "timestamp": datetime.utcnow()
        }
        
    except HTTPException:
        # Re-raise FastAPI HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in WLX endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )

@api_router.get("/health")
async def health_check():
    """Health check endpoint for monitoring service status."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "service": "whiplash-xlm-bot-api",
        "stellar_network": "mainnet"
    }

# Include the router in the main app
app.include_router(api_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global exception handler caught: {str(exc)}")
    return ErrorResponse(
        error="Internal Server Error",
        detail="An unexpected error occurred",
    )

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
