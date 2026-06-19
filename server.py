from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import uuid
from datetime import datetime, timezone, timedelta


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")


# ------------------ Models ------------------
class Item(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    price: float
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ItemCreate(BaseModel):
    name: str
    price: float


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None


class TransactionLine(BaseModel):
    item_id: str
    name: str
    price: float
    quantity: int


class Transaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_name: str
    items: List[TransactionLine]
    total: float
    payment_method: Literal["cash", "upi"]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TransactionCreate(BaseModel):
    customer_name: str
    items: List[TransactionLine]
    payment_method: Literal["cash", "upi"]


# ------------------ Seed ------------------
DEFAULT_ITEMS = [
    {"name": "Winkin Strawberry", "price": 40},
    {"name": "Winkin Chocolate", "price": 40},
    {"name": "Winkin Vanilla", "price": 40},
    {"name": "Sting", "price": 20},
    {"name": "Campa", "price": 20},
    {"name": "Nimbu", "price": 20},
    {"name": "Water Bottle", "price": 20},
]


@app.on_event("startup")
async def seed_items():
    count = await db.items.count_documents({})
    if count == 0:
        for it in DEFAULT_ITEMS:
            obj = Item(**it)
            await db.items.insert_one(obj.model_dump())
        logger.info("Seeded default items")


# ------------------ Items Endpoints ------------------
@api_router.get("/items", response_model=List[Item])
async def list_items():
    docs = await db.items.find({}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    return [Item(**d) for d in docs]


@api_router.post("/items", response_model=Item)
async def create_item(payload: ItemCreate):
    obj = Item(**payload.model_dump())
    await db.items.insert_one(obj.model_dump())
    return obj


@api_router.put("/items/{item_id}", response_model=Item)
async def update_item(item_id: str, payload: ItemUpdate):
    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.items.update_one({"id": item_id}, {"$set": update_data})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    doc = await db.items.find_one({"id": item_id}, {"_id": 0})
    return Item(**doc)


@api_router.delete("/items/{item_id}")
async def delete_item(item_id: str):
    res = await db.items.delete_one({"id": item_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True}


# ------------------ Transactions Endpoints ------------------
@api_router.post("/transactions", response_model=Transaction)
async def create_transaction(payload: TransactionCreate):
    if not payload.items:
        raise HTTPException(status_code=400, detail="At least one item required")
    total = sum(line.price * line.quantity for line in payload.items)
    obj = Transaction(
        customer_name=payload.customer_name.strip() or "Walk-in",
        items=payload.items,
        total=total,
        payment_method=payload.payment_method,
    )
    await db.transactions.insert_one(obj.model_dump())
    return obj


@api_router.get("/transactions", response_model=List[Transaction])
async def list_transactions(
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 500,
):
    query: dict = {}
    if start or end:
        rng: dict = {}
        if start:
            rng["$gte"] = start
        if end:
            rng["$lte"] = end
        query["created_at"] = rng
    docs = await db.transactions.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return [Transaction(**d) for d in docs]


@api_router.delete("/transactions/{txn_id}")
async def delete_transaction(txn_id: str):
    res = await db.transactions.delete_one({"id": txn_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"success": True}


# ------------------ Analytics ------------------
@api_router.get("/analytics/summary")
async def analytics_summary(period: Literal["today", "week", "month"] = "today"):
    now = datetime.now(timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets_count = 1
    elif period == "week":
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        buckets_count = 7
    else:  # month
        start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        buckets_count = 30

    docs = await db.transactions.find(
        {"created_at": {"$gte": start.isoformat()}},
        {"_id": 0},
    ).to_list(10000)

    total_revenue = sum(d["total"] for d in docs)
    total_txns = len(docs)
    total_items = sum(sum(li["quantity"] for li in d["items"]) for d in docs)
    cash_total = sum(d["total"] for d in docs if d["payment_method"] == "cash")
    upi_total = sum(d["total"] for d in docs if d["payment_method"] == "upi")

    # Bucket per day
    buckets = []
    for i in range(buckets_count):
        day_start = start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_revenue = 0.0
        for d in docs:
            try:
                dt = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
            except Exception:
                continue
            if day_start <= dt < day_end:
                day_revenue += d["total"]
        buckets.append({
            "label": day_start.strftime("%d %b") if buckets_count > 1 else day_start.strftime("%H:00"),
            "date": day_start.date().isoformat(),
            "value": day_revenue,
        })

    # Top items
    item_counts: dict = {}
    for d in docs:
        for li in d["items"]:
            key = li["name"]
            if key not in item_counts:
                item_counts[key] = {"name": key, "quantity": 0, "revenue": 0.0}
            item_counts[key]["quantity"] += li["quantity"]
            item_counts[key]["revenue"] += li["price"] * li["quantity"]
    top_items = sorted(item_counts.values(), key=lambda x: x["quantity"], reverse=True)[:5]

    return {
        "period": period,
        "total_revenue": total_revenue,
        "total_transactions": total_txns,
        "total_items_sold": total_items,
        "cash_total": cash_total,
        "upi_total": upi_total,
        "buckets": buckets,
        "top_items": top_items,
    }


@api_router.get("/")
async def root():
    return {"message": "TurfTracker API"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
