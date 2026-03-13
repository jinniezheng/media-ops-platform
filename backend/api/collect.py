from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func, delete as sa_delete
from database import get_db
from models.task import CollectTask, VideoPost, PostComment, XhsNote, XhsComment, XhsVideo, XhsImage, DouyinPost, DouyinComment
from models.user import CollectedUser
from models.auth_user import AuthUser
from services.auth import get_current_user
from pydantic import BaseModel

router = APIRouter(prefix="/api/collect", tags=["Collect"])


class CollectTaskCreate(BaseModel):
    name: str
    platform: str = "bilibili"
    task_type: str  # keyword / video_comment / follower
    keyword: str | None = None
    target_url: str | None = None
    max_count: int = 100


@router.post("/tasks")
async def create_task(body: CollectTaskCreate, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    task = CollectTask(**body.model_dump(), owner_id=current_user.id)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"id": task.id, "status": task.status}


@router.get("/tasks")
async def list_tasks(db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    result = await db.execute(
        select(CollectTask).where(CollectTask.owner_id == current_user.id).order_by(CollectTask.id.desc())
    )
    tasks = result.scalars().all()
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "platform": t.platform,
                "task_type": t.task_type,
                "keyword": t.keyword,
                "target_url": t.target_url,
                "max_count": t.max_count,
                "collected_count": t.collected_count,
                "status": t.status,
                "error_message": t.error_message or "",
                "created_at": str(t.created_at) if t.created_at else None,
            }
            for t in tasks
        ]
    }


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: int, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    task = await db.get(CollectTask, task_id)
    if not task or task.owner_id != current_user.id:
        return {"error": "Task not found"}

    task.status = "running"
    task.error_message = ""
    await db.commit()

    try:
        result = await _do_collect(task, db)
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)[:500]
        await db.commit()
        return {"error": str(exc), "collected": 0, "duplicates_skipped": 0}

    if task.task_type == "video_comment":
        return await _save_video_comments(db, task, result)
    if task.platform == "xhs":
        return await _save_xhs_notes(db, task, result)
    if task.platform == "douyin":
        return await _save_douyin_posts(db, task, result)
    return await _save_users(db, task, result)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db), current_user: AuthUser = Depends(get_current_user)):
    task = await db.get(CollectTask, task_id)
    if not task or task.owner_id != current_user.id:
        return {"error": "Task not found"}
    # 删除关联的视频和评论
    video_ids_q = select(VideoPost.id).where(VideoPost.source_task_id == task_id)
    video_ids = (await db.execute(video_ids_q)).scalars().all()
    if video_ids:
        await db.execute(
            sa_delete(PostComment).where(PostComment.post_id.in_(video_ids))
        )
        await db.execute(
            sa_delete(VideoPost).where(VideoPost.source_task_id == task_id)
        )
    # 删除关联的用户
    await db.execute(
        sa_delete(CollectedUser).where(CollectedUser.source_task_id == task_id)
    )
    # 删除关联的 XHS 数据
    await db.execute(
        sa_delete(XhsComment).where(XhsComment.source_task_id == task_id)
    )
    await db.execute(
        sa_delete(XhsNote).where(XhsNote.source_task_id == task_id)
    )
    await db.delete(task)
    await db.commit()
    return {"ok": True}


async def _save_video_comments(db: AsyncSession, task, data: dict) -> dict:
    videos = data.get("videos", [])
    comments = data.get("comments", [])

    # Save videos (dedup by aid)
    video_count = 0
    aid_to_post_id: dict[int, int] = {}
    for v in videos:
        exists = await db.execute(
            select(VideoPost.id).where(VideoPost.aid == v["aid"]).limit(1)
        )
        row = exists.scalar()
        if row is not None:
            aid_to_post_id[v["aid"]] = row
            continue
        vp = VideoPost(
            aid=v["aid"], bvid=v.get("bvid", ""),
            title=v.get("title", ""), author=v.get("author", ""),
            mid=v.get("mid", 0), play_count=v.get("play_count", 0),
            like_count=v.get("like_count", 0),
            reply_count=v.get("reply_count", 0),
            pubdate=v.get("pubdate", 0), source_task_id=task.id,
        )
        db.add(vp)
        await db.flush()
        aid_to_post_id[v["aid"]] = vp.id
        video_count += 1

    # Save comments (dedup by rpid)
    comment_count = 0
    for c in comments:
        exists = await db.execute(
            select(PostComment.id).where(
                PostComment.rpid == c["rpid"]
            ).limit(1)
        )
        if exists.scalar() is not None:
            continue
        post_id = aid_to_post_id.get(c["aid"], 0)
        db.add(PostComment(
            rpid=c["rpid"], post_id=post_id,
            mid=c.get("mid", 0), uname=c.get("uname", ""),
            avatar=c.get("avatar", ""), message=c.get("message", ""),
            like_count=c.get("like_count", 0), ctime=c.get("ctime", 0),
            parent_rpid=c.get("parent_rpid", 0),
            source_task_id=task.id,
        ))
        comment_count += 1

    task.collected_count = comment_count
    task.status = "done"
    await db.commit()
    return {
        "collected_videos": video_count,
        "collected_comments": comment_count,
    }


async def _save_users(db: AsyncSession, task, users: list[dict]) -> dict:
    new_count = 0
    dup_count = 0
    for u in users:
        exists = await db.execute(
            select(CollectedUser.id).where(
                CollectedUser.platform == "bilibili",
                CollectedUser.platform_uid == u["mid"],
            ).limit(1)
        )
        if exists.scalar() is not None:
            dup_count += 1
            continue
        db.add(CollectedUser(
            platform="bilibili",
            platform_uid=u["mid"],
            nickname=u.get("name", ""),
            avatar_url=u.get("face", ""),
            signature=u.get("sign", ""),
            follower_count=u.get("follower_count", 0),
            following_count=u.get("following_count", 0),
            video_count=u.get("video_count", 0),
            source_task_id=task.id,
        ))
        new_count += 1

    task.collected_count = new_count
    task.status = "done"
    await db.commit()
    return {"collected": new_count, "duplicates_skipped": dup_count}


async def _do_collect(task: CollectTask, db: AsyncSession):
    from collector.factory import create_crawler
    from models.account import PlatformAccount

    crawler = create_crawler(task.platform)

    # 从数据库获取对应平台的活跃账号 cookie（按 owner_id 过滤）
    cookie_str = ""
    if task.platform == "xhs":
        result = await db.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == "xhs",
                PlatformAccount.is_active == True,
                PlatformAccount.owner_id == task.owner_id,
            ).limit(1)
        )
        account = result.scalar_one_or_none()
        if account and account.cookies:
            cookie_str = account.cookies
        else:
            raise ValueError(f"未找到可用的活跃小红书账号，请在账号管理中添加")

    return await crawler.collect(task, cookie_str=cookie_str)


async def _save_xhs_notes(db: AsyncSession, task, data: dict) -> dict:
    notes = data.get("notes", [])
    comments = data.get("comments", [])
    note_count = 0
    for n in notes:
        exists = await db.execute(
            select(XhsNote.id).where(XhsNote.note_id == n["note_id"]).limit(1)
        )
        if exists.scalar() is not None:
            continue
        db.add(XhsNote(
            note_id=n["note_id"], title=n.get("title", ""),
            desc=n.get("desc", ""), type=n.get("type", "normal"),
            user_id=n.get("user_id", ""), nickname=n.get("nickname", ""),
            avatar=n.get("avatar", ""),
            liked_count=n.get("liked_count", 0),
            collected_count=n.get("collected_count", 0),
            comment_count=n.get("comment_count", 0),
            share_count=n.get("share_count", 0),
            time=n.get("time", 0),
            note_url=f"https://www.xiaohongshu.com/explore/{n['note_id']}",
            xsec_token=n.get("xsec_token", ""),
            source_task_id=task.id,
        ))
        note_count += 1
    comment_count = 0
    for c in comments:
        exists = await db.execute(
            select(XhsComment.id).where(
                XhsComment.comment_id == c["comment_id"]
            ).limit(1)
        )
        if exists.scalar() is not None:
            continue
        db.add(XhsComment(
            comment_id=c["comment_id"], note_id=c.get("note_id", ""),
            content=c.get("content", ""), user_id=c.get("user_id", ""),
            nickname=c.get("nickname", ""), avatar=c.get("avatar", ""),
            ip_location=c.get("ip_location", ""),
            like_count=c.get("like_count", 0),
            sub_comment_count=c.get("sub_comment_count", 0),
            parent_comment_id=c.get("parent_comment_id", ""),
            create_time=c.get("create_time", 0),
            source_task_id=task.id,
        ))
        comment_count += 1
    task.collected_count = note_count
    task.status = "done"
    await db.commit()
    return {"collected_notes": note_count, "collected_comments": comment_count}


# ── Video / Comment query endpoints ─────────────────────────

@router.get("/videos")
async def list_videos(
    task_id: int = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    q = select(VideoPost).where(
        VideoPost.source_task_id == task_id
    ).order_by(VideoPost.like_count.desc())
    total_q = select(sa_func.count(VideoPost.id)).where(
        VideoPost.source_task_id == task_id
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    videos = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": v.id, "aid": v.aid, "bvid": v.bvid,
                "title": v.title, "author": v.author, "mid": v.mid,
                "play_count": v.play_count, "like_count": v.like_count,
                "reply_count": v.reply_count, "pubdate": v.pubdate,
            }
            for v in videos
        ],
    }


@router.get("/comments")
async def list_comments(
    post_id: int = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    # Fetch parent video info for aid/title
    video = await db.get(VideoPost, post_id)
    video_aid = video.aid if video else 0
    video_title = video.title if video else ""

    q = select(PostComment).where(
        PostComment.post_id == post_id,
        PostComment.parent_rpid == 0,
    ).order_by(PostComment.like_count.desc())
    total_q = select(sa_func.count(PostComment.id)).where(
        PostComment.post_id == post_id,
        PostComment.parent_rpid == 0,
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    comments = result.scalars().all()
    return {
        "total": total,
        "video_aid": video_aid,
        "video_title": video_title,
        "items": [
            {
                "id": c.id, "rpid": c.rpid, "mid": c.mid,
                "uname": c.uname, "avatar": c.avatar,
                "message": c.message, "like_count": c.like_count,
                "ctime": c.ctime,
            }
            for c in comments
        ],
    }


# ── XHS Note / Comment query endpoints ──────────────────────

@router.get("/xhs-notes")
async def list_xhs_notes(
    task_id: int = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    q = select(XhsNote).where(
        XhsNote.source_task_id == task_id
    ).order_by(
        XhsNote.time.desc(),
        XhsNote.created_at.desc(),
    )
    total_q = select(sa_func.count(XhsNote.id)).where(
        XhsNote.source_task_id == task_id
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    notes = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": n.id, "note_id": n.note_id, "title": n.title,
                "desc": n.desc, "type": n.type,
                "user_id": n.user_id, "nickname": n.nickname,
                "avatar": n.avatar,
                "liked_count": n.liked_count,
                "collected_count": n.collected_count,
                "comment_count": n.comment_count,
                "share_count": n.share_count,
                "time": n.time,
                "note_url": n.note_url,
            }
            for n in notes
        ],
    }


@router.get("/xhs-comments")
async def list_xhs_comments(
    note_id: str = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    q = select(XhsComment).where(
        XhsComment.note_id == note_id,
        XhsComment.parent_comment_id == "",
    ).order_by(XhsComment.like_count.desc())
    total_q = select(sa_func.count(XhsComment.id)).where(
        XhsComment.note_id == note_id,
        XhsComment.parent_comment_id == "",
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    comments = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": c.id, "comment_id": c.comment_id,
                "note_id": c.note_id, "content": c.content,
                "user_id": c.user_id, "nickname": c.nickname,
                "avatar": c.avatar, "ip_location": c.ip_location,
                "like_count": c.like_count,
                "sub_comment_count": c.sub_comment_count,
                "create_time": c.create_time,
            }
            for c in comments
        ],
    }


# ── 小红书用户信息 ──────────────────────────────────────────────

class ExtractUsersBody(BaseModel):
    """从评论中提取用户"""
    note_id: str
    comment_ids: list[str] = []  # 为空则提取该笔记所有评论者
    account_id: int | None = None  # 如果提供，自动获取用户详情


@router.post("/xhs-extract-users")
async def extract_users_from_comments(
    body: ExtractUsersBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """从小红书评论中提取潜在用户并保存"""
    from models.user import CollectedUser
    from models.account import PlatformAccount
    from services.xhs_user import get_xhs_user_info
    import asyncio

    if body.comment_ids:
        # 提取指定评论的用户
        q = select(XhsComment).where(
            XhsComment.comment_id.in_(body.comment_ids)
        )
    else:
        # 提取该笔记所有评论者
        q = select(XhsComment).where(
            XhsComment.note_id == body.note_id
        )

    result = await db.execute(q)
    comments = result.scalars().all()

    added = 0
    skipped = 0
    new_users = []  # 新添加的用户

    for c in comments:
        if not c.user_id:
            continue
        # 检查是否已存在
        exists = await db.scalar(
            select(CollectedUser.id).where(
                CollectedUser.platform == "xhs",
                CollectedUser.platform_uid == c.user_id,
            ).limit(1)
        )
        if exists:
            skipped += 1
            continue

        # 创建用户记录
        user = CollectedUser(
            platform="xhs",
            platform_uid=c.user_id,
            nickname=c.nickname,
            avatar_url=c.avatar,
            source_task_id=c.source_task_id,
            source_note_id=c.note_id,
            source_comment_id=c.comment_id,
            status="new",
        )
        db.add(user)
        new_users.append(user)
        added += 1

    await db.commit()

    # 如果提供了账号，自动获取用户详情
    fetched = 0
    if body.account_id and new_users:
        account = await db.get(PlatformAccount, body.account_id)
        if account and account.cookies and account.platform == "xhs":
            for user in new_users:
                try:
                    info = await get_xhs_user_info(account.cookies, user.platform_uid)
                    if info.get("success"):
                        user.nickname = info.get("nickname") or user.nickname
                        user.avatar_url = info.get("avatar") or user.avatar_url
                        user.signature = info.get("desc", "")
                        user.follower_count = info.get("fans", 0)
                        user.following_count = info.get("follows", 0)
                        user.liked_count = info.get("interaction", 0)
                        fetched += 1
                except Exception:
                    pass
                await asyncio.sleep(2)  # 避免频繁请求
            await db.commit()

    return {"added": added, "skipped": skipped, "fetched": fetched}


class ExtractAuthorsBody(BaseModel):
    """从笔记中提取作者"""
    note_ids: list[str]
    account_id: int | None = None  # 如果提供，自动获取用户详情


@router.post("/xhs-extract-authors")
async def extract_authors_from_notes(
    body: ExtractAuthorsBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """从小红书笔记中提取作者并保存到用户库"""
    from models.user import CollectedUser
    from models.account import PlatformAccount
    from services.xhs_user import get_xhs_user_info
    import asyncio

    # 获取指定的笔记
    q = select(XhsNote).where(XhsNote.note_id.in_(body.note_ids))
    result = await db.execute(q)
    notes = result.scalars().all()

    added = 0
    skipped = 0
    new_users = []  # 新添加的用户

    for n in notes:
        if not n.user_id:
            continue
        # 检查是否已存在
        exists = await db.scalar(
            select(CollectedUser.id).where(
                CollectedUser.platform == "xhs",
                CollectedUser.platform_uid == n.user_id,
            ).limit(1)
        )
        if exists:
            skipped += 1
            continue

        # 创建用户记录
        user = CollectedUser(
            platform="xhs",
            platform_uid=n.user_id,
            nickname=n.nickname,
            avatar_url=n.avatar,
            source_task_id=n.source_task_id,
            source_note_id=n.note_id,
            status="new",
        )
        db.add(user)
        new_users.append(user)
        added += 1

    await db.commit()

    # 如果提供了账号，自动获取用户详情
    fetched = 0
    if body.account_id and new_users:
        account = await db.get(PlatformAccount, body.account_id)
        if account and account.cookies and account.platform == "xhs":
            for user in new_users:
                try:
                    info = await get_xhs_user_info(account.cookies, user.platform_uid)
                    if info.get("success"):
                        user.nickname = info.get("nickname") or user.nickname
                        user.avatar_url = info.get("avatar") or user.avatar_url
                        user.signature = info.get("desc", "")
                        user.follower_count = info.get("fans", 0)
                        user.following_count = info.get("follows", 0)
                        user.liked_count = info.get("interaction", 0)
                        fetched += 1
                except Exception:
                    pass
                await asyncio.sleep(2)  # 避免频繁请求
            await db.commit()

    return {"added": added, "skipped": skipped, "fetched": fetched}


class FetchUserInfoBody(BaseModel):
    """获取用户详细信息"""
    user_ids: list[int]  # CollectedUser.id 列表
    account_id: int  # 使用哪个小红书账号


@router.post("/xhs-fetch-user-info")
async def fetch_xhs_user_info(
    body: FetchUserInfoBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """获取小红书用户详细信息（粉丝数、获赞数等）"""
    from models.account import PlatformAccount
    from services.xhs_user import get_xhs_user_info
    import asyncio

    account = await db.get(PlatformAccount, body.account_id)
    if not account or not account.cookies:
        return {"error": "Account not found or no cookies"}
    if account.platform != "xhs":
        return {"error": "账号不是小红书平台"}

    # 获取待更新的用户
    result = await db.execute(
        select(CollectedUser).where(
            CollectedUser.id.in_(body.user_ids),
            CollectedUser.platform == "xhs",
        )
    )
    users = result.scalars().all()

    updated = 0
    failed = 0

    for user in users:
        try:
            info = await get_xhs_user_info(account.cookies, user.platform_uid)
            if info.get("success"):
                user.nickname = info.get("nickname") or user.nickname
                user.avatar_url = info.get("avatar") or user.avatar_url
                user.signature = info.get("desc", "")
                user.follower_count = info.get("fans", 0)
                user.following_count = info.get("follows", 0)
                user.liked_count = info.get("interaction", 0)  # 获赞与收藏
                updated += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
        # 间隔避免频繁请求
        await asyncio.sleep(2)

    await db.commit()
    return {"updated": updated, "failed": failed}


@router.get("/xhs-users")
async def list_xhs_users(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
    task_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
):
    """列出已采集的小红书用户"""
    q = select(CollectedUser).where(CollectedUser.platform == "xhs")
    total_q = select(sa_func.count(CollectedUser.id)).where(CollectedUser.platform == "xhs")

    if task_id:
        q = q.where(CollectedUser.source_task_id == task_id)
        total_q = total_q.where(CollectedUser.source_task_id == task_id)
    if status:
        q = q.where(CollectedUser.status == status)
        total_q = total_q.where(CollectedUser.status == status)

    q = q.order_by(CollectedUser.follower_count.desc())
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    users = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": u.id,
                "platform_uid": u.platform_uid,
                "nickname": u.nickname,
                "avatar_url": u.avatar_url,
                "signature": u.signature,
                "follower_count": u.follower_count,
                "following_count": u.following_count,
                "liked_count": u.liked_count,
                "collected_count": u.collected_count,
                "source_note_id": u.source_note_id,
                "status": u.status,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }


# ── 小红书视频/图片解析 ──────────────────────────────────────────

class ParseMediaBody(BaseModel):
    """解析笔记媒体资源"""
    note_ids: list[str]
    account_id: int
    save_to_db: bool = True  # 是否保存到数据库


@router.post("/xhs-parse-media")
async def parse_xhs_media(
    body: ParseMediaBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """
    解析小红书笔记的视频/图片资源

    通过 XHS API 获取笔记详情，返回多画质视频直链和有水印/无水印图片链接
    """
    from models.account import PlatformAccount
    from services.xhs_media import batch_parse_xhs_notes_media

    account = await db.get(PlatformAccount, body.account_id)
    if not account or not account.cookies:
        return {"error": "Account not found or no cookies"}
    if account.platform != "xhs":
        return {"error": "账号不是小红书平台"}

    # 从数据库查询每个 note_id 对应的 xsec_token
    note_items = []
    for note_id in body.note_ids:
        row = await db.scalar(
            select(XhsNote.xsec_token).where(
                XhsNote.note_id == note_id
            ).limit(1)
        )
        note_items.append({
            "note_id": note_id,
            "xsec_token": row or "",
        })

    # 批量解析（复用同一个浏览器实例）
    results = await batch_parse_xhs_notes_media(
        account.cookies, note_items, interval=2.0,
    )

    # 保存到数据库
    videos_added = 0
    images_added = 0
    if body.save_to_db:
        for result in results:
            if not result.get("success"):
                continue
            nid = result["note_id"]
            if result.get("type") == "video" and result.get("video"):
                vi = result["video"]
                exists = await db.scalar(
                    select(XhsVideo.id).where(
                        XhsVideo.note_id == nid
                    ).limit(1)
                )
                if not exists:
                    db.add(XhsVideo(
                        note_id=nid,
                        title=result.get("title", ""),
                        cover_url=result.get("cover_url", ""),
                        video_url_1080p=vi.get("video_url_1080p", ""),
                        video_url_720p=vi.get("video_url_720p", ""),
                        video_url_480p=vi.get("video_url_480p", ""),
                        video_url_default=vi.get("video_url_default", ""),
                        duration=vi.get("duration", 0),
                        width=vi.get("width", 0),
                        height=vi.get("height", 0),
                    ))
                    videos_added += 1
            elif result.get("images"):
                for img in result["images"]:
                    exists = await db.scalar(
                        select(XhsImage.id).where(
                            XhsImage.note_id == nid,
                            XhsImage.image_index == img["index"],
                        ).limit(1)
                    )
                    if not exists:
                        db.add(XhsImage(
                            note_id=nid,
                            image_index=img["index"],
                            url_watermark=img.get("url_watermark", ""),
                            url_original=img.get("url_original", ""),
                            width=img.get("width", 0),
                            height=img.get("height", 0),
                        ))
                        images_added += 1

    await db.commit()

    return {
        "results": results,
        "videos_added": videos_added,
        "images_added": images_added,
    }


@router.get("/xhs-videos")
async def list_xhs_videos(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
    note_id: str | None = None,
    page: int = 1,
    size: int = 20,
):
    """列出已解析的小红书视频"""
    q = select(XhsVideo)
    total_q = select(sa_func.count(XhsVideo.id))

    if note_id:
        q = q.where(XhsVideo.note_id == note_id)
        total_q = total_q.where(XhsVideo.note_id == note_id)

    q = q.order_by(XhsVideo.id.desc())
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    videos = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": v.id,
                "note_id": v.note_id,
                "title": v.title,
                "cover_url": v.cover_url,
                "video_url_1080p": v.video_url_1080p,
                "video_url_720p": v.video_url_720p,
                "video_url_480p": v.video_url_480p,
                "video_url_default": v.video_url_default,
                "duration": v.duration,
                "width": v.width,
                "height": v.height,
                "download_status": v.download_status,
                "local_path": v.local_path,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ],
    }


@router.get("/xhs-images")
async def list_xhs_images(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
    note_id: str | None = None,
    page: int = 1,
    size: int = 50,
):
    """列出已解析的小红书图片"""
    q = select(XhsImage)
    total_q = select(sa_func.count(XhsImage.id))

    if note_id:
        q = q.where(XhsImage.note_id == note_id)
        total_q = total_q.where(XhsImage.note_id == note_id)

    q = q.order_by(XhsImage.note_id, XhsImage.image_index)
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    images = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": i.id,
                "note_id": i.note_id,
                "image_index": i.image_index,
                "url_watermark": i.url_watermark,
                "url_original": i.url_original,
                "width": i.width,
                "height": i.height,
                "download_status": i.download_status,
                "local_path": i.local_path,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in images
        ],
    }


# ── 小红书视频/图片下载 ──────────────────────────────────────────

class DownloadVideosBody(BaseModel):
    """下载视频"""
    video_ids: list[int] = []  # XhsVideo.id 列表，为空则下载所有未下载的
    quality: str = "default"  # 1080p / 720p / 480p / default
    account_id: int | None = None


@router.post("/xhs-download-videos")
async def download_xhs_videos(
    body: DownloadVideosBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """
    下载小红书视频到服务器。
    XHS CDN 链接有时效性，下载前会重新解析获取最新链接。
    """
    from models.account import PlatformAccount
    from services.xhs_media import parse_xhs_note_media
    from services.xhs_downloader import download_xhs_video
    import asyncio

    # 1. 获取 XHS 账号 cookie
    if body.account_id:
        account = await db.get(PlatformAccount, body.account_id)
    else:
        result = await db.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == "xhs",
                PlatformAccount.is_active == True,
            ).limit(1)
        )
        account = result.scalar_one_or_none()
    cookie_str = account.cookies if account else ""

    # 2. 获取待下载的视频
    if body.video_ids:
        q = select(XhsVideo).where(XhsVideo.id.in_(body.video_ids))
    else:
        q = select(XhsVideo).where(
            XhsVideo.download_status == "pending"
        ).limit(20)
    result = await db.execute(q)
    videos = result.scalars().all()

    downloaded = 0
    failed = 0

    for v in videos:
        # 重新解析获取最新视频链接
        fresh_video = {}
        if cookie_str:
            xsec_token = await db.scalar(
                select(XhsNote.xsec_token).where(
                    XhsNote.note_id == v.note_id
                ).limit(1)
            ) or ""
            try:
                print(f"[DL] Re-parsing video {v.note_id}")
                parsed = await parse_xhs_note_media(
                    cookie_str, v.note_id, xsec_token,
                )
                if parsed.get("success") and parsed.get("video"):
                    fresh_video = parsed["video"]
            except Exception as e:
                print(f"[DL] Re-parse video error: {e}")

        # 优先使用新鲜链接
        quality_key = f"video_url_{body.quality}"
        video_url = (
            fresh_video.get(quality_key)
            or fresh_video.get("video_url_default")
            or getattr(v, quality_key, "")
            or v.video_url_default
        )

        if not video_url:
            print(f"[DL] No video URL for {v.note_id}")
            failed += 1
            continue

        if video_url.startswith("http://"):
            video_url = "https://" + video_url[7:]

        v.download_status = "downloading"
        await db.commit()

        try:
            dl_headers = {"Cookie": cookie_str} if cookie_str else None
            res = await download_xhs_video(
                note_id=v.note_id,
                video_url=video_url,
                quality=body.quality,
                extra_headers=dl_headers,
            )
            if res.get("success"):
                v.download_status = "done"
                v.local_path = res.get("path", "")
                downloaded += 1
                print(f"[DL] Video OK {v.note_id} size={res.get('size')}")
            else:
                err = res.get("error", "unknown")
                print(f"[DL] Video failed {v.note_id}: {err}")
                v.download_status = "failed"
                failed += 1
        except Exception as e:
            print(f"[DL] Video exception {v.note_id}: {e}")
            v.download_status = "failed"
            failed += 1

        await asyncio.sleep(1)

    await db.commit()
    return {"downloaded": downloaded, "failed": failed}


class DownloadImagesBody(BaseModel):
    """下载图片"""
    image_ids: list[int] = []  # XhsImage.id 列表，为空则下载所有未下载的
    use_original: bool = True  # True=无水印，False=有水印
    account_id: int | None = None  # 用于重新获取 CDN 链接的账号 ID


@router.post("/xhs-download-images")
async def download_xhs_images(
    body: DownloadImagesBody,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """
    下载小红书图片到服务器。
    XHS CDN 链接有时效性，下载前会重新解析获取最新链接。
    """
    from models.account import PlatformAccount
    from services.xhs_media import parse_xhs_note_media
    from services.xhs_downloader import download_xhs_image
    import asyncio

    # 1. 获取 XHS 账号 cookie
    if body.account_id:
        account = await db.get(PlatformAccount, body.account_id)
    else:
        result = await db.execute(
            select(PlatformAccount).where(
                PlatformAccount.platform == "xhs",
                PlatformAccount.is_active == True,
            ).limit(1)
        )
        account = result.scalar_one_or_none()
    cookie_str = account.cookies if account else ""

    # 2. 获取待下载的图片
    if body.image_ids:
        q = select(XhsImage).where(XhsImage.id.in_(body.image_ids))
    else:
        q = select(XhsImage).where(
            XhsImage.download_status == "pending"
        ).limit(50)
    result = await db.execute(q)
    images = result.scalars().all()

    if not images:
        return {"downloaded": 0, "failed": 0, "error": "没有待下载的图片"}

    # 3. 按 note_id 分组，重新解析获取最新 CDN 链接
    from collections import defaultdict
    note_images: dict[str, list] = defaultdict(list)
    for img in images:
        note_images[img.note_id].append(img)

    downloaded = 0
    failed = 0

    for note_id, img_list in note_images.items():
        fresh_urls = {}  # {image_index: {url_original, url_watermark}}

        if cookie_str:
            # 查询 xsec_token
            xsec_token = await db.scalar(
                select(XhsNote.xsec_token).where(
                    XhsNote.note_id == note_id
                ).limit(1)
            ) or ""
            try:
                print(f"[DL] Re-parsing {note_id} for fresh URLs")
                parsed = await parse_xhs_note_media(
                    cookie_str, note_id, xsec_token,
                )
                if parsed.get("success") and parsed.get("images"):
                    for pi in parsed["images"]:
                        fresh_urls[pi["index"]] = {
                            "url_original": pi.get("url_original", ""),
                            "url_watermark": pi.get("url_watermark", ""),
                        }
                    print(f"[DL] Got {len(fresh_urls)} fresh URLs")
                else:
                    err = parsed.get("error", "unknown")
                    print(f"[DL] Re-parse failed: {err}")
            except Exception as e:
                print(f"[DL] Re-parse error: {e}")

        for img in img_list:
            # 优先使用刚解析的新鲜链接
            fu = fresh_urls.get(img.image_index, {})
            if body.use_original:
                url = fu.get("url_original") or img.url_original
            else:
                url = fu.get("url_watermark") or img.url_watermark

            if not url:
                print(f"[DL] No URL for {note_id} idx={img.image_index}")
                img.download_status = "failed"
                failed += 1
                continue

            # http -> https
            if url.startswith("http://"):
                url = "https://" + url[7:]

            img.download_status = "downloading"
            await db.commit()

            try:
                dl_headers = {"Cookie": cookie_str} if cookie_str else None
                res = await download_xhs_image(
                    note_id=img.note_id,
                    image_url=url,
                    image_index=img.image_index,
                    watermark=not body.use_original,
                    extra_headers=dl_headers,
                )
                if res.get("success"):
                    img.download_status = "done"
                    img.local_path = res.get("path", "")
                    downloaded += 1
                    print(f"[DL] OK {note_id} idx={img.image_index}"
                          f" size={res.get('size', 0)}")
                else:
                    err = res.get("error", "unknown")
                    print(f"[DL] Failed {note_id} idx={img.image_index}"
                          f": {err}")
                    img.download_status = "failed"
                    failed += 1
            except Exception as e:
                print(f"[DL] Exception {note_id} idx={img.image_index}"
                      f": {e}")
                img.download_status = "failed"
                failed += 1

            await asyncio.sleep(0.5)

        # 每个 note 解析间隔
        await asyncio.sleep(1.0)

    await db.commit()
    return {"downloaded": downloaded, "failed": failed}


# ── 抖音视频采集 ─────────────────────────────────────────────

async def _save_douyin_posts(db: AsyncSession, task: CollectTask, data: dict) -> dict:
    """去重保存抖音视频和评论"""
    posts = data.get("posts", [])
    comments = data.get("comments", [])

    post_count = 0
    for p in posts:
        aweme_id = p.get("aweme_id", "")
        if not aweme_id:
            continue
        exists = await db.scalar(
            select(DouyinPost.id).where(DouyinPost.aweme_id == aweme_id).limit(1)
        )
        if exists is not None:
            continue
        db.add(DouyinPost(
            aweme_id=aweme_id,
            desc=p.get("desc", ""),
            author_uid=p.get("author_uid", ""),
            author_name=p.get("author_name", ""),
            author_avatar=p.get("author_avatar", ""),
            like_count=p.get("like_count", 0),
            comment_count=p.get("comment_count", 0),
            share_count=p.get("share_count", 0),
            collect_count=p.get("collect_count", 0),
            play_count=p.get("play_count", 0),
            duration=p.get("duration", 0),
            cover_url=p.get("cover_url", ""),
            video_url=p.get("video_url", ""),
            create_time=p.get("create_time", 0),
            source_task_id=task.id,
        ))
        post_count += 1

    comment_count = 0
    for c in comments:
        comment_id = c.get("comment_id", "")
        if not comment_id:
            continue
        exists = await db.scalar(
            select(DouyinComment.id).where(
                DouyinComment.comment_id == comment_id
            ).limit(1)
        )
        if exists is not None:
            continue
        db.add(DouyinComment(
            comment_id=comment_id,
            aweme_id=c.get("aweme_id", ""),
            content=c.get("content", ""),
            user_id=c.get("user_id", ""),
            nickname=c.get("nickname", ""),
            avatar=c.get("avatar", ""),
            ip_location=c.get("ip_location", ""),
            like_count=c.get("like_count", 0),
            reply_count=c.get("reply_count", 0),
            create_time=c.get("create_time", 0),
            source_task_id=task.id,
        ))
        comment_count += 1

    task.collected_count = post_count
    task.status = "done"
    await db.commit()
    return {"collected_posts": post_count, "collected_comments": comment_count}


@router.get("/douyin-posts")
async def list_douyin_posts(
    task_id: int = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = select(DouyinPost).where(
        DouyinPost.source_task_id == task_id
    ).order_by(DouyinPost.like_count.desc())
    total_q = select(sa_func.count(DouyinPost.id)).where(
        DouyinPost.source_task_id == task_id
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    posts = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "aweme_id": p.aweme_id,
                "desc": p.desc,
                "author_uid": p.author_uid,
                "author_name": p.author_name,
                "author_avatar": p.author_avatar,
                "like_count": p.like_count,
                "comment_count": p.comment_count,
                "share_count": p.share_count,
                "collect_count": p.collect_count,
                "play_count": p.play_count,
                "duration": p.duration,
                "cover_url": p.cover_url,
                "video_url": p.video_url,
                "create_time": p.create_time,
            }
            for p in posts
        ],
    }


@router.get("/douyin-comments")
async def list_douyin_comments(
    aweme_id: str = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = select(DouyinComment).where(
        DouyinComment.aweme_id == aweme_id
    ).order_by(DouyinComment.like_count.desc())
    total_q = select(sa_func.count(DouyinComment.id)).where(
        DouyinComment.aweme_id == aweme_id
    )
    total = await db.scalar(total_q) or 0
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    comments = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "comment_id": c.comment_id,
                "aweme_id": c.aweme_id,
                "content": c.content,
                "user_id": c.user_id,
                "nickname": c.nickname,
                "avatar": c.avatar,
                "ip_location": c.ip_location,
                "like_count": c.like_count,
                "reply_count": c.reply_count,
                "create_time": c.create_time,
            }
            for c in comments
        ],
    }
