from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.template import MessageTemplate
from models.task import TouchRecord
from models.account import PlatformAccount
from models.auth_user import AuthUser
from services.auth import get_current_user
from pydantic import BaseModel
from services.llm import generate_comment_reply, generate_xhs_reply
from services.bilibili_sender import send_reply_comment
from services.xhs_sender import send_xhs_comment
from services.douyin_sender import send_douyin_comment
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/message", tags=["Message"])


# ── Templates (kept as-is) ──────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    template_type: str
    content: str
    variables: str = ""


@router.post("/templates")
async def create_template(
    body: TemplateCreate, db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    tpl = MessageTemplate(**body.model_dump())
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return {"id": tpl.id}


@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    result = await db.execute(select(MessageTemplate))
    templates = result.scalars().all()
    return {
        "items": [
            {
                "id": t.id, "name": t.name,
                "template_type": t.template_type,
                "content": t.content, "variables": t.variables,
            }
            for t in templates
        ]
    }


# ── Touch: create from selected comments ─────────────────────

class CommentItem(BaseModel):
    rpid: int
    aid: int
    uname: str
    message: str
    video_title: str


class VideoItem(BaseModel):
    aid: int
    title: str


class XhsNoteItem(BaseModel):
    note_id: str
    title: str


class XhsCommentItem(BaseModel):
    comment_id: str
    note_id: str
    note_title: str
    nickname: str
    content: str


class DouyinVideoItem(BaseModel):
    aweme_id: str
    desc: str
    author_name: str
    author_uid: str


class DouyinCommentItem(BaseModel):
    comment_id: str
    aweme_id: str
    content: str
    nickname: str
    user_id: str


class TouchCreateFromComments(BaseModel):
    comments: list[CommentItem] = []
    videos: list[VideoItem] = []
    xhs_notes: list[XhsNoteItem] = []
    xhs_comments: list[XhsCommentItem] = []
    douyin_videos: list[DouyinVideoItem] = []
    douyin_comments: list[DouyinCommentItem] = []


@router.post("/touch")
async def create_touch(
    body: TouchCreateFromComments, db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    created = 0
    for c in body.comments:
        rec = TouchRecord(
            touch_type="comment",
            target_rpid=c.rpid,
            target_aid=c.aid,
            target_message=c.message,
            target_uname=c.uname,
            video_title=c.video_title,
            status="pending",
        )
        db.add(rec)
        created += 1
    for v in body.videos:
        rec = TouchRecord(
            touch_type="comment",
            target_rpid=0,
            target_aid=v.aid,
            target_message="",
            target_uname="",
            video_title=v.title,
            status="pending",
        )
        db.add(rec)
        created += 1
    for n in body.xhs_notes:
        rec = TouchRecord(
            touch_type="comment",
            platform="xhs",
            target_note_id=n.note_id,
            target_note_title=n.title,
            target_message="",
            target_uname="",
            status="pending",
        )
        db.add(rec)
        created += 1
    for c in body.xhs_comments:
        rec = TouchRecord(
            touch_type="comment",
            platform="xhs",
            target_note_id=c.note_id,
            target_note_title=c.note_title,
            target_message=c.content,
            target_uname=c.nickname,
            status="pending",
        )
        db.add(rec)
        created += 1
    for v in body.douyin_videos:
        rec = TouchRecord(
            touch_type="comment",
            platform="douyin",
            target_aweme_id=v.aweme_id,
            target_message=v.desc,
            target_uname=v.author_name,
            video_title=v.desc[:100],  # 使用描述作为标题
            status="pending",
        )
        db.add(rec)
        created += 1
    for c in body.douyin_comments:
        rec = TouchRecord(
            touch_type="comment",
            platform="douyin",
            target_aweme_id=c.aweme_id,
            target_message=c.content,
            target_uname=c.nickname,
            status="pending",
        )
        db.add(rec)
        created += 1
    await db.commit()
    return {"created": created}


# ── Generate AI reply ─────────────────────────────────────────

class GenerateBody(BaseModel):
    prompt: str = ""


@router.post("/touch/{record_id}/generate-reply")
async def generate_reply(
    record_id: int, body: GenerateBody = GenerateBody(),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    rec = await db.get(TouchRecord, record_id)
    if not rec:
        return {"error": "Record not found"}
    try:
        if rec.platform == "xhs":
            reply = await generate_xhs_reply(
                rec.target_note_title, rec.target_message,
                rec.target_uname, body.prompt,
            )
        else:
            reply = await generate_comment_reply(
                rec.video_title, rec.target_message,
                rec.target_uname, body.prompt,
            )
        rec.ai_reply = reply
        rec.final_reply = reply
        rec.status = "ai_generated"
        await db.commit()
        return {"id": rec.id, "ai_reply": reply, "status": rec.status}
    except Exception as exc:
        logger.error("AI generate failed for record %s: %s", record_id, exc)
        return {"error": str(exc)}


# ── Batch generate ────────────────────────────────────────────

class BatchGenerateBody(BaseModel):
    record_ids: list[int] = []
    prompt: str = ""


@router.post("/touch/batch-generate")
async def batch_generate(
    body: BatchGenerateBody = BatchGenerateBody(),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    if body.record_ids:
        q = select(TouchRecord).where(
            TouchRecord.id.in_(body.record_ids),
            TouchRecord.status == "pending",
        )
    else:
        q = select(TouchRecord).where(TouchRecord.status == "pending")
    result = await db.execute(q)
    records = result.scalars().all()
    ok, fail = 0, 0
    for rec in records:
        try:
            if rec.platform == "xhs":
                reply = await generate_xhs_reply(
                    rec.target_note_title, rec.target_message,
                    rec.target_uname, body.prompt,
                )
            else:
                reply = await generate_comment_reply(
                    rec.video_title, rec.target_message,
                    rec.target_uname, body.prompt,
                )
            rec.ai_reply = reply
            rec.final_reply = reply
            rec.status = "ai_generated"
            ok += 1
        except Exception as exc:
            logger.error("AI generate failed for record %s: %s", rec.id, exc, exc_info=True)
            fail += 1
    await db.commit()
    return {"generated": ok, "failed": fail}


# ── Update (edit final_reply / confirm) ───────────────────────

class TouchUpdate(BaseModel):
    final_reply: str | None = None
    status: str | None = None


@router.put("/touch/{record_id}")
async def update_touch(
    record_id: int, body: TouchUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    rec = await db.get(TouchRecord, record_id)
    if not rec:
        return {"error": "Record not found"}
    if body.final_reply is not None:
        rec.final_reply = body.final_reply
    if body.status is not None:
        rec.status = body.status
    await db.commit()
    return {"id": rec.id, "status": rec.status}


@router.delete("/touch/{record_id}")
async def delete_touch(
    record_id: int, db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    rec = await db.get(TouchRecord, record_id)
    if not rec:
        return {"error": "Record not found"}
    await db.delete(rec)
    await db.commit()
    return {"ok": True}


# ── Send comment reply ────────────────────────────────────────

class SendBody(BaseModel):
    account_id: int


@router.post("/touch/{record_id}/send")
async def send_touch(
    record_id: int, body: SendBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    rec = await db.get(TouchRecord, record_id)
    if not rec:
        return {"error": "Record not found"}
    account = await db.get(PlatformAccount, body.account_id)
    if not account or not account.cookies or account.owner_id != current_user.id:
        return {"error": "Account not found or no cookies"}
    if account.used_today >= account.daily_limit:
        return {"error": "账号今日限额已用完"}

    # 检查平台匹配
    rec_platform = rec.platform or "bilibili"
    if account.platform != rec_platform:
        return {"error": f"账号平台({account.platform})与记录平台({rec_platform})不匹配"}

    try:
        if rec_platform == "xhs":
            # 小红书发送
            resp = await send_xhs_comment(
                cookie_str=account.cookies,
                note_id=rec.target_note_id,
                content=rec.final_reply,
                target_comment_id=rec.target_rpid if rec.target_rpid else "",
            )
            # 小红书成功返回 {"success": true, "code": 0, ...}
            if resp.get("success") or resp.get("code") == 0:
                rec.status = "sent"
                rec.account_id = account.id
                rec.sent_at = datetime.now()
                account.used_today += 1
            else:
                rec.status = "failed"
                rec.content = resp.get("msg", str(resp))[:500]
        elif rec_platform == "douyin":
            # 抖音发送
            if not rec.target_aweme_id:
                rec.status = "failed"
                rec.content = "抖音视频ID缺失"
                return {"error": "抖音视频ID缺失"}

            resp = await send_douyin_comment(
                cookie_str=account.cookies,
                aweme_id=rec.target_aweme_id,
                content=rec.final_reply,
                target_comment_id="",  # 暂不支持回复评论
            )
            if resp.get("success") or resp.get("code") == 0:
                rec.status = "sent"
                rec.account_id = account.id
                rec.sent_at = datetime.now()
                account.used_today += 1
            elif resp.get("needs_verify"):
                rec.status = "needs_verify"
                rec.content = resp.get("message", str(resp))[:500]
            else:
                rec.status = "failed"
                rec.content = resp.get("message", str(resp))[:500]
        else:
            # B站发送
            resp = await send_reply_comment(
                account.cookies, rec.target_aid,
                rec.target_rpid, rec.final_reply,
            )
            if resp.get("code") == 0:
                rec.status = "sent"
                rec.account_id = account.id
                rec.sent_at = datetime.now()
                account.used_today += 1
            else:
                rec.status = "failed"
                rec.content = resp.get("message", "unknown error")

        await db.commit()
        return {"id": rec.id, "status": rec.status, "resp": resp}
    except Exception as exc:
        logger.error("Send failed for record %s: %s", record_id, exc, exc_info=True)
        rec.status = "failed"
        rec.content = str(exc)[:500]
        await db.commit()
        return {"error": str(exc)}


# ── Batch send ─────────────────────────────────────────────────

class BatchSendBody(BaseModel):
    record_ids: list[int] = []
    account_id: int
    auto_switch: bool = True  # 自动切换账号


@router.post("/touch/batch-send")
async def batch_send(
    body: BatchSendBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    # 获取指定的账号
    current_account = await db.get(PlatformAccount, body.account_id)
    if not current_account or not current_account.cookies or current_account.owner_id != current_user.id:
        return {"error": "Account not found or no cookies"}

    platform = current_account.platform  # bilibili, xhs 或 douyin

    # 获取该平台所有可用账号
    all_accounts_result = await db.execute(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.is_active == True,
            PlatformAccount.owner_id == current_user.id,
        )
    )
    all_accounts = list(all_accounts_result.scalars().all())

    if not all_accounts:
        return {"error": f"没有可用的 {platform} 账号"}

    q = select(TouchRecord).where(
        TouchRecord.id.in_(body.record_ids),
        TouchRecord.status.in_(["confirmed", "needs_verify"]),
    )
    result = await db.execute(q)
    records = result.scalars().all()

    sent, failed, skipped = 0, 0, 0
    account_idx = all_accounts.index(current_account) if current_account in all_accounts else 0

    for rec in records:
        rec_platform = rec.platform or "bilibili"

        # 检查平台是否匹配
        if rec_platform != platform:
            logger.warning("Platform mismatch for record %s: expected %s, got %s", rec.id, platform, rec_platform)
            skipped += 1
            continue

        # 查找可用账号
        account_found = False
        for _ in range(len(all_accounts)):
            acc = all_accounts[account_idx]
            if acc.used_today < acc.daily_limit and acc.cookies:
                current_account = acc
                account_found = True
                break
            # 切换到下一个账号
            if body.auto_switch:
                account_idx = (account_idx + 1) % len(all_accounts)
            else:
                break

        if not account_found:
            logger.warning("All %s accounts reached daily limit, stopping", platform)
            skipped += len(records) - sent - failed - skipped
            break

        try:
            if platform == "xhs":
                # 小红书发送
                resp = await send_xhs_comment(
                    cookie_str=current_account.cookies,
                    note_id=rec.target_note_id,
                    content=rec.final_reply,
                    target_comment_id=str(rec.target_rpid) if rec.target_rpid else "",
                )
                if resp.get("success") or resp.get("code") == 0:
                    rec.status = "sent"
                    rec.account_id = current_account.id
                    rec.sent_at = datetime.now()
                    current_account.used_today += 1
                    sent += 1
                else:
                    rec.status = "failed"
                    rec.content = resp.get("msg", str(resp))[:500]
                    failed += 1
            elif platform == "douyin":
                # 抖音发送
                if not rec.target_aweme_id:
                    rec.status = "failed"
                    rec.content = "抖音视频ID缺失"
                    failed += 1
                    continue

                resp = await send_douyin_comment(
                    cookie_str=current_account.cookies,
                    aweme_id=rec.target_aweme_id,
                    content=rec.final_reply,
                    target_comment_id="",  # 暂不支持回复评论
                )
                if resp.get("success") or resp.get("code") == 0:
                    rec.status = "sent"
                    rec.account_id = current_account.id
                    rec.sent_at = datetime.now()
                    current_account.used_today += 1
                    sent += 1
                elif resp.get("needs_verify"):
                    rec.status = "needs_verify"
                    rec.content = resp.get("message", str(resp))[:500]
                    failed += 1
                else:
                    rec.status = "failed"
                    rec.content = resp.get("message", str(resp))[:500]
                    failed += 1
            else:
                # B站发送
                resp = await send_reply_comment(
                    current_account.cookies, rec.target_aid,
                    rec.target_rpid, rec.final_reply,
                )
                if resp.get("code") == 0:
                    rec.status = "sent"
                    rec.account_id = current_account.id
                    rec.sent_at = datetime.now()
                    current_account.used_today += 1
                    sent += 1
                else:
                    rec.status = "failed"
                    rec.content = resp.get("message", "unknown error")
                    failed += 1
        except Exception as exc:
            logger.error("Send failed for record %s: %s", rec.id, exc, exc_info=True)
            rec.status = "failed"
            rec.content = str(exc)[:500]
            failed += 1

    await db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped}


# ── List records ──────────────────────────────────────────────

@router.get("/records")
async def list_records(db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    # Filter records by accounts owned by current user
    owned_account_ids = select(PlatformAccount.id).where(
        PlatformAccount.owner_id == current_user.id
    )
    result = await db.execute(
        select(TouchRecord).where(
            TouchRecord.account_id.in_(owned_account_ids) | (TouchRecord.account_id == 0)
        ).order_by(TouchRecord.id.desc())
    )
    records = result.scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "platform": r.platform or "bilibili",
                "target_rpid": r.target_rpid,
                "target_aid": r.target_aid,
                "target_message": r.target_message,
                "target_uname": r.target_uname,
                "video_title": r.video_title,
                "target_aweme_id": r.target_aweme_id or "",
                "target_note_id": r.target_note_id or "",
                "target_note_title": r.target_note_title or "",
                "ai_reply": r.ai_reply,
                "final_reply": r.final_reply,
                "status": r.status,
                "account_id": r.account_id,
                "sent_at": str(r.sent_at) if r.sent_at else None,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in records
        ]
    }
