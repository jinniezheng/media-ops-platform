from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, func
from database import Base


class CollectTask(Base):
    __tablename__ = "collect_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, default=0, index=True)
    name = Column(String(128), nullable=False)
    platform = Column(String(20), nullable=False, default="bilibili")
    task_type = Column(String(32), nullable=False)  # keyword / video_comment / follower
    keyword = Column(String(256))
    target_url = Column(Text)
    max_count = Column(Integer, default=100)
    collected_count = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending / running / done / failed
    error_message = Column(Text, default="")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TouchRecord(Base):
    __tablename__ = "touch_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, default=0)
    account_id = Column(Integer, default=0)
    template_id = Column(Integer)
    touch_type = Column(String(20), default="comment")
    content = Column(Text)
    # comment-reply specific fields
    target_rpid = Column(BigInteger, default=0)
    target_aid = Column(BigInteger, default=0)
    target_message = Column(Text, default="")
    target_uname = Column(String(128), default="")
    video_title = Column(String(512), default="")
    ai_reply = Column(Text, default="")
    final_reply = Column(Text, default="")
    # XHS 扩展字段
    platform = Column(String(20), default="bilibili")  # bilibili / xhs
    target_note_id = Column(String(64), default="")     # XHS 笔记ID
    target_note_title = Column(String(512), default="")  # 笔记标题（XHS 用）
    # 抖音扩展字段
    target_aweme_id = Column(String(64), default="")    # 抖音视频ID
    # pending → ai_generated → confirmed → sent / failed
    status = Column(String(20), default="pending")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


class VideoPost(Base):
    __tablename__ = "video_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aid = Column(BigInteger, nullable=False, index=True)
    bvid = Column(String(32))
    title = Column(String(512))
    author = Column(String(128))
    mid = Column(BigInteger)
    play_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    pubdate = Column(Integer, default=0)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class PostComment(Base):
    __tablename__ = "post_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rpid = Column(BigInteger, nullable=False, index=True)
    post_id = Column(Integer, nullable=False, index=True)
    mid = Column(BigInteger)
    uname = Column(String(128))
    avatar = Column(Text)
    message = Column(Text)
    like_count = Column(Integer, default=0)
    ctime = Column(Integer, default=0)
    parent_rpid = Column(BigInteger, default=0)  # 0=root, >0=sub-reply
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class XhsNote(Base):
    __tablename__ = "xhs_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(String(64), nullable=False, index=True)
    title = Column(String(512))
    desc = Column(Text)
    type = Column(String(20))
    user_id = Column(String(64))
    nickname = Column(String(128))
    avatar = Column(Text)
    liked_count = Column(Integer, default=0)
    collected_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    image_list = Column(Text)
    video_url = Column(Text)
    tag_list = Column(Text)
    ip_location = Column(String(64))
    time = Column(BigInteger)
    note_url = Column(Text)
    xsec_token = Column(String(256), default="")
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class XhsComment(Base):
    __tablename__ = "xhs_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(String(64), nullable=False, index=True)
    note_id = Column(String(64), index=True)
    content = Column(Text)
    user_id = Column(String(64))
    nickname = Column(String(128))
    avatar = Column(Text)
    ip_location = Column(String(64))
    like_count = Column(Integer, default=0)
    sub_comment_count = Column(Integer, default=0)
    parent_comment_id = Column(String(64), default="")
    create_time = Column(BigInteger)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class XhsVideo(Base):
    """小红书视频资源"""
    __tablename__ = "xhs_videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(String(64), nullable=False, index=True)
    title = Column(String(512))
    cover_url = Column(Text)
    video_url_1080p = Column(Text)
    video_url_720p = Column(Text)
    video_url_480p = Column(Text)
    video_url_default = Column(Text)  # 默认/最佳画质
    duration = Column(Integer)  # 时长（毫秒）
    width = Column(Integer)
    height = Column(Integer)
    download_status = Column(String(20), default="pending")  # pending/downloading/done/failed
    local_path = Column(Text)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class XhsImage(Base):
    """小红书图片资源"""
    __tablename__ = "xhs_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    note_id = Column(String(64), nullable=False, index=True)
    image_index = Column(Integer, default=0)  # 图片序号
    url_watermark = Column(Text)  # 有水印 URL
    url_original = Column(Text)  # 无水印 URL
    width = Column(Integer)
    height = Column(Integer)
    download_status = Column(String(20), default="pending")
    local_path = Column(Text)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class DouyinPost(Base):
    """抖音视频"""
    __tablename__ = "douyin_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aweme_id = Column(String(64), nullable=False, index=True)
    desc = Column(Text)
    author_uid = Column(String(64))
    author_name = Column(String(128))
    author_avatar = Column(Text)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    collect_count = Column(Integer, default=0)
    play_count = Column(Integer, default=0)
    duration = Column(Integer, default=0)  # 毫秒
    cover_url = Column(Text)
    video_url = Column(Text)
    create_time = Column(Integer, default=0)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())


class DouyinComment(Base):
    """抖音评论"""
    __tablename__ = "douyin_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(String(64), nullable=False, index=True)
    aweme_id = Column(String(64), index=True)
    content = Column(Text)
    user_id = Column(String(64))
    nickname = Column(String(128))
    avatar = Column(Text)
    ip_location = Column(String(64))
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    create_time = Column(Integer, default=0)
    source_task_id = Column(Integer, index=True)
    created_at = Column(DateTime, server_default=func.now())
