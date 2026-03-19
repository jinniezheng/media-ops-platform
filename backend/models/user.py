from sqlalchemy import Column, Integer, String, DateTime, Text, func, BigInteger
from database import Base


class CollectedUser(Base):
    __tablename__ = "collected_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, default="bilibili")
    platform_uid = Column(String(64), nullable=False, index=True)
    nickname = Column(String(128))
    avatar_url = Column(Text)
    signature = Column(Text)
    # 通用字段
    follower_count = Column(Integer, default=0)  # 粉丝数
    following_count = Column(Integer, default=0)  # 关注数
    video_count = Column(Integer, default=0)  # 视频/笔记数
    # 小红书特有字段
    liked_count = Column(Integer, default=0)  # 获赞数
    collected_count = Column(Integer, default=0)  # 收藏数
    # 来源信息
    source_task_id = Column(Integer, index=True)
    source_note_id = Column(String(64))  # 来源笔记ID
    source_comment_id = Column(String(64))  # 来源评论ID
    source_aweme_id = Column(String(64))  # 来源抖音视频ID
    tags = Column(String(256), default="")
    status = Column(String(20), default="new")  # new / contacted / converted
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
