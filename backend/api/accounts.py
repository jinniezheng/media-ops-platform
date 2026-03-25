from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.account import PlatformAccount
from models.auth_user import AuthUser
from services.auth import get_current_user
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["Accounts"])


class AccountCreate(BaseModel):
    platform: str = "bilibili"
    account_name: str
    cookies: str | None = None
    daily_limit: int = 20


@router.post("")
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    acc = PlatformAccount(**body.model_dump(), owner_id=current_user.id)
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"id": acc.id}


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    result = await db.execute(
        select(PlatformAccount).where(PlatformAccount.owner_id == current_user.id)
    )
    accounts = result.scalars().all()
    return {
        "items": [
            {
                "id": a.id,
                "platform": a.platform,
                "account_name": a.account_name,
                "is_active": a.is_active,
                "daily_limit": a.daily_limit,
                "used_today": a.used_today,
            }
            for a in accounts
        ]
    }


@router.delete("/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    acc = await db.get(PlatformAccount, account_id)
    if not acc or acc.owner_id != current_user.id:
        return {"error": "Account not found"}
    await db.delete(acc)
    await db.commit()
    return {"ok": True}


@router.post("/{account_id}/check-cookie")
async def check_account_cookie(account_id: int, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    """检测账号 cookie 是否有效"""
    acc = await db.get(PlatformAccount, account_id)
    if not acc or acc.owner_id != current_user.id:
        return {"valid": False, "msg": "账号不存在"}
    if not acc.cookies:
        return {"valid": False, "msg": "Cookie 未配置"}

    if acc.platform == "xhs":
        from services.xhs_sender import check_xhs_cookie
        result = await check_xhs_cookie(acc.cookies)
        return result
    elif acc.platform == "douyin":
        from services.douyin_checker import check_douyin_cookie
        result = await check_douyin_cookie(acc.cookies)
        # 统一字段名：douyin_checker 返回 "message"，前端期望 "msg"
        return {
            "valid": result.get("valid", False),
            "nickname": result.get("nickname", ""),
            "msg": result.get("msg", result.get("message", ""))
        }

    # bilibili 暂不支持检测，直接返回未知
    return {"valid": None, "msg": f"暂不支持 {acc.platform} 平台的 Cookie 检测"}
