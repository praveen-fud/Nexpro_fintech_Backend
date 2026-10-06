"""
Production-safe startup bootstrap. Unlike app.seed (dev/demo fixtures —
fake accounts, fake wallets, a well-known password), this only ensures the
real operational config rows (fee rules, platform limits) exist — no user
accounts, nothing with a password. Safe to run on every deploy; idempotent.

Run with: python -m app.bootstrap
"""

import asyncio

from app.core.database import AsyncSessionLocal, Base, engine
from app.seed import seed_config


async def bootstrap() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        written = await seed_config(session)
        if written:
            await session.commit()
            print("Bootstrap complete: fee rules and platform limits seeded.")
        else:
            print("Bootstrap complete: configuration already present.")


if __name__ == "__main__":
    asyncio.run(bootstrap())
