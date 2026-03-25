from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    import models  # noqa: F401 — ensure all models are registered on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 增量迁移：给已有表加新列
        import sqlalchemy
        migrations = [
            "ALTER TABLE collected_users ADD COLUMN owner_id INTEGER DEFAULT 0",
            "ALTER TABLE touch_records ADD COLUMN xsec_token VARCHAR(256) DEFAULT ''",
            "ALTER TABLE touch_records ADD COLUMN target_comment_id VARCHAR(64) DEFAULT ''",
        ]
        for sql in migrations:
            try:
                await conn.execute(sqlalchemy.text(sql))
            except Exception:
                pass  # 列已存在，忽略
