from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class CopyDraft(BaseModel):
    listing_title: str = Field(min_length=1, max_length=80)
    selling_points: list[str] = Field(min_length=3, max_length=3)
    keywords: list[str] = Field(min_length=3, max_length=8)


class ERPResult(BaseModel):
    item_id: str
    operation_key: str = Field(min_length=8, max_length=128)
    result: Literal["written", "failed", "skipped"]
    erp_record_id: str | None = Field(default=None, max_length=128)
    error: str | None = Field(default=None, max_length=500)


class ERPResultBatch(BaseModel):
    results: list[ERPResult] = Field(min_length=1, max_length=100)


class ApprovedItem(BaseModel):
    item_id: str
    sku: str
    listing_title: str
    category: str
    price: Decimal
    stock: int
    selling_points: list[str]
    keywords: list[str]
