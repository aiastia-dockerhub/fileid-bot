"""Bot 文件数量额度 + VIP 会员迁移码 的 DB 层测试

不依赖真实 Telegram / Redis，使用临时 SQLite：
- 文件额度：按 Bot 计、边界判定（count > limit 才超）、运行时可调、VIP 不限
- 迁移码：生成/作废旧码/领取搬运/一次性/过期拒绝/目标已有会员拒绝
- get_all_vip_users 排序

运行：
    python -m pytest tests/test_quota_and_migration.py -v
或直接：
    python tests/test_quota_and_migration.py
"""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# 让脚本能直接 import 项目根目录的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 在 import 任何项目模块前，配置临时数据库 =====
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix='fileid_test_qm_', suffix='.db')
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ.setdefault('DB_TYPE', 'sqlite')
os.environ.setdefault('DB_PATH', _DB_PATH)
os.environ.setdefault('REDIS_URL', '')

from config import VIP_PLANS, BASIC_LEVEL, FREE_BOT_FILES_LIMIT, BASIC_BOT_FILES_LIMIT  # noqa: E402
from db.core import init_db, get_session  # noqa: E402
from db.models import User, UserBot, FileMapping, PlatformSetting, VipMigration  # noqa: E402
from db.bots import set_platform_setting  # noqa: E402
from db.vip import (  # noqa: E402
    get_or_create_user, update_user_vip, get_user_vip_level,
    get_all_vip_users, get_free_bot_files_limit, get_basic_bot_files_limit,
    invalidate_free_settings_cache,
)
from db.files import (  # noqa: E402
    get_bot_file_count, get_owner_file_limit, is_bot_over_file_quota,
)
from db.migrations import create_migration, get_migration, claim_migration  # noqa: E402


async def _add_bot(owner_id: int, bot_id: int) -> int:
    """插入一条 Bot 记录，返回 bot_db_id"""
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(UserBot.id).where(UserBot.bot_id == bot_id))
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        bot = UserBot(
            owner_id=owner_id,
            bot_token=f'token_{owner_id}_{bot_id}',
            bot_id=bot_id,
            bot_username=f'quota{owner_id}_{bot_id}bot',
            bot_firstname='TestBot',
            status='active',
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        session.add(bot)
        await session.commit()
        return bot.id


async def _add_files(bot_db_id: int, n: int, start: int = 0):
    """给 Bot 插入 n 条文件记录"""
    async with get_session() as session:
        for i in range(start, start + n):
            session.add(FileMapping(
                code=f'c_{bot_db_id}_{i}',
                file_type='photo',
                telegram_file_id=f'fid_{bot_db_id}_{i}',
                bot_db_id=bot_db_id,
            ))
        await session.commit()


async def _reset_data():
    """清空相关表 + 设置缓存，保证用例隔离"""
    async with get_session() as session:
        from sqlalchemy import delete
        await session.execute(delete(FileMapping))
        await session.execute(delete(UserBot))
        await session.execute(delete(User))
        await session.execute(delete(PlatformSetting))
        await session.execute(delete(VipMigration))
        await session.commit()
    await invalidate_free_settings_cache()


class QuotaTestBase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await init_db()
        await _reset_data()


class TestFileQuota(QuotaTestBase):

    async def test_default_limits(self):
        """未设置运行时限制时回退默认值：免费 20000 / 基础版 50000"""
        self.assertEqual(await get_free_bot_files_limit(), FREE_BOT_FILES_LIMIT)
        self.assertEqual(await get_basic_bot_files_limit(), BASIC_BOT_FILES_LIMIT)

    async def test_limit_by_owner_level(self):
        """get_owner_file_limit：VIP1-3 不限(0)、基础版 5 万、免费 2 万"""
        vip_u, basic_u, free_u = 101, 102, 103
        await get_or_create_user(vip_u)
        await get_or_create_user(basic_u)
        await get_or_create_user(free_u)
        await update_user_vip(vip_u, 3, 1)
        await update_user_vip(basic_u, BASIC_LEVEL, 1)

        self.assertEqual(await get_owner_file_limit(vip_u), 0)
        self.assertEqual(await get_owner_file_limit(basic_u), BASIC_BOT_FILES_LIMIT)
        self.assertEqual(await get_owner_file_limit(free_u), FREE_BOT_FILES_LIMIT)

    async def test_quota_boundary_free_owner(self):
        """边界：count == limit 不超（上限内最后一个文件仍给代码），count > limit 才超"""
        uid = 110
        await get_or_create_user(uid)
        await set_platform_setting('free_bot_files', '3')
        await invalidate_free_settings_cache()
        bot_db_id = await _add_bot(uid, 1100)

        await _add_files(bot_db_id, 3)
        self.assertEqual(await get_bot_file_count(bot_db_id), 3)
        self.assertFalse(await is_bot_over_file_quota(bot_db_id, uid))  # 3 == 3，未超

        await _add_files(bot_db_id, 1, start=3)
        self.assertTrue(await is_bot_over_file_quota(bot_db_id, uid))   # 4 > 3，超

    async def test_quota_runtime_override(self):
        """运行时调整两档限额立即生效（/setfree files / basicfiles）"""
        free_u, basic_u = 120, 121
        await get_or_create_user(free_u)
        await get_or_create_user(basic_u)
        await update_user_vip(basic_u, BASIC_LEVEL, 1)

        await set_platform_setting('free_bot_files', '2')
        await set_platform_setting('basic_bot_files', '10')
        await invalidate_free_settings_cache()

        self.assertEqual(await get_owner_file_limit(free_u), 2)
        self.assertEqual(await get_owner_file_limit(basic_u), 10)

    async def test_quota_vip_unlimited(self):
        """VIP 主人文件数超多也不限"""
        uid = 130
        await get_or_create_user(uid)
        await update_user_vip(uid, 1, 1)
        bot_db_id = await _add_bot(uid, 1300)

        await set_platform_setting('free_bot_files', '2')
        await invalidate_free_settings_cache()
        await _add_files(bot_db_id, 20)
        self.assertFalse(await is_bot_over_file_quota(bot_db_id, uid))

    async def test_quota_per_bot(self):
        """额度按 Bot 独立计算：同一主人两个 Bot 各自计数"""
        uid = 140
        await get_or_create_user(uid)
        await set_platform_setting('free_bot_files', '2')
        await invalidate_free_settings_cache()
        b1 = await _add_bot(uid, 1400)
        b2 = await _add_bot(uid, 1401)

        await _add_files(b1, 3)
        await _add_files(b2, 1)
        self.assertTrue(await is_bot_over_file_quota(b1, uid))
        self.assertFalse(await is_bot_over_file_quota(b2, uid))


class TestVipMigration(QuotaTestBase):

    async def test_migration_full_flow(self):
        """生成 → 领取：B 拿到 A 的等级+到期（原样搬运），A 清零，B 标记一次性，码置 used"""
        a, b = 201, 202
        await get_or_create_user(a)
        await get_or_create_user(b)
        await update_user_vip(a, 2, 1)  # VIP2 一个月

        mig = await create_migration(a, 2, (await get_or_create_user(a))['vip_expire_at'])
        self.assertIsNotNone(mig)
        self.assertTrue(mig['code'].startswith('mig_'))
        self.assertEqual(mig['status'], 'pending')

        status, info = await claim_migration(mig['code'], b)
        self.assertEqual(status, 'ok')
        self.assertEqual(info['vip_level'], 2)
        self.assertEqual(info['from_user_id'], a)

        user_a = await get_or_create_user(a)
        user_b = await get_or_create_user(b)
        self.assertEqual(user_a['vip_level'], 0)
        self.assertIsNone(user_a['vip_expire_at'])
        self.assertEqual(user_b['vip_level'], 2)
        self.assertEqual(user_b['vip_expire_at'], info['vip_expire_at'])
        self.assertEqual(user_b['vip_migrated'], 1)  # B 不能再迁出

        # 码已置 used，不能重复领取
        status2, _ = await claim_migration(mig['code'], 203)
        self.assertEqual(status2, 'invalid')

    async def test_migration_code_regenerated_cancels_old(self):
        """再次生成迁移码会作废旧码"""
        a = 211
        await get_or_create_user(a)
        await update_user_vip(a, 1, 1)
        expire = (await get_or_create_user(a))['vip_expire_at']

        mig1 = await create_migration(a, 1, expire)
        mig2 = await create_migration(a, 1, expire)
        self.assertNotEqual(mig1['code'], mig2['code'])
        self.assertEqual((await get_migration(mig1['code']))['status'], 'cancelled')
        self.assertEqual((await get_migration(mig2['code']))['status'], 'pending')

        status, _ = await claim_migration(mig1['code'], 212)
        self.assertEqual(status, 'invalid')

    async def test_migration_expired_source(self):
        """源会员过期后领取失败"""
        a, b = 221, 222
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        async with get_session() as session:
            session.add(User(user_id=a, vip_level=1, vip_expire_at=past,
                             created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            await session.commit()

        mig = await create_migration(a, 1, past)
        status, _ = await claim_migration(mig['code'], b)
        self.assertEqual(status, 'expired')

    async def test_migration_self_and_target_has_vip(self):
        """拒绝：迁移给自己 / 目标账号已有有效会员"""
        a, b = 231, 232
        await get_or_create_user(a)
        await get_or_create_user(b)
        await update_user_vip(a, 1, 1)
        await update_user_vip(b, 1, 1)
        expire = (await get_or_create_user(a))['vip_expire_at']

        mig = await create_migration(a, 1, expire)
        status, _ = await claim_migration(mig['code'], a)
        self.assertEqual(status, 'self')

        status, _ = await claim_migration(mig['code'], b)
        self.assertEqual(status, 'target_has_vip')
        # 被拒绝后码仍是 pending，A 会员未被清动
        self.assertEqual((await get_migration(mig['code']))['status'], 'pending')
        self.assertEqual(await get_user_vip_level(a), 1)

    async def test_migration_flag_reset_on_new_grant(self):
        """迁移收到的会员不能迁出；但新购买/管理员设置的新会员会重置标记"""
        a, b = 241, 242
        await get_or_create_user(a)
        await get_or_create_user(b)
        await update_user_vip(a, 1, 6)
        mig = await create_migration(a, 1, (await get_or_create_user(a))['vip_expire_at'])
        status, _ = await claim_migration(mig['code'], b)
        self.assertEqual(status, 'ok')
        self.assertEqual((await get_or_create_user(b))['vip_migrated'], 1)

        # B 自购新会员（模拟支付成功）→ 标记重置，可再迁移
        await update_user_vip(b, 1, 1)
        self.assertEqual((await get_or_create_user(b))['vip_migrated'], 0)


class TestVipUserList(QuotaTestBase):

    async def test_get_all_vip_users_ordered(self):
        """会员列表：只含 vip_level>0，按等级降序"""
        free_u, v1, v3, basic = 301, 302, 303, 304
        for u in (free_u, v1, v3, basic):
            await get_or_create_user(u)
        await update_user_vip(v1, 1, 1)
        await update_user_vip(v3, 3, 1)
        await update_user_vip(basic, BASIC_LEVEL, 1)

        users = await get_all_vip_users()
        levels = [u['vip_level'] for u in users]
        self.assertEqual(levels, sorted(levels, reverse=True))
        self.assertEqual(set(levels), {1, 3, BASIC_LEVEL})
        self.assertNotIn(free_u, [u['user_id'] for u in users])


if __name__ == '__main__':
    unittest.main(verbosity=2)
