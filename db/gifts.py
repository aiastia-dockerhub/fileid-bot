"""礼物领取码数据访问层"""
import logging
import secrets
import string
from datetime import datetime
from typing import Optional, Dict, List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.core import get_session, _model_to_dict
from db.models import GiftClaim

logger = logging.getLogger(__name__)

# 领取码长度（不含 claim_ 前缀）
_CODE_LEN = 10
# 码字符集（URL 友好，去掉易混淆字符）
_CODE_ALPHABET = string.ascii_lowercase + string.digits


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gen_code() -> str:
    """生成 claim_<10位随机码>，碰撞概率极低（36^10 ≈ 3.6e15）"""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    return f"claim_{body}"


async def create_claim(
    gift_id: str,
    gift_name: str,
    star_count: int,
    upgradeable: bool,
    created_by: int,
) -> Optional[Dict]:
    """创建一个领取码记录，返回 {'code': ..., 'link_path': ...}"""
    async with get_session() as session:
        try:
            # 防碰撞：最多重试 5 次
            for _ in range(5):
                code = _gen_code()
                exists = await session.execute(
                    select(GiftClaim).where(GiftClaim.code == code)
                )
                if exists.scalars().first() is None:
                    break
            else:
                logger.error("生成领取码碰撞 5 次，放弃")
                return None

            claim = GiftClaim(
                code=code,
                gift_id=gift_id,
                gift_name=gift_name,
                star_count=star_count,
                upgradeable=1 if upgradeable else 0,
                status='pending',
                created_by=created_by,
                created_at=_now(),
            )
            session.add(claim)
            await session.commit()
            logger.info("生成领取码 %s (gift=%s, %d⭐) by admin %s",
                        code, gift_name, star_count, created_by)
            return _model_to_dict(claim)
        except Exception as e:
            logger.error("创建领取码失败: %s", e, exc_info=True)
            return None


async def get_claim(code: str) -> Optional[Dict]:
    """按 code 查询领取码记录"""
    async with get_session() as session:
        result = await session.execute(
            select(GiftClaim).where(GiftClaim.code == code)
        )
        claim = result.scalars().first()
        return _model_to_dict(claim) if claim else None


async def try_claim(code: str, claimant_user_id: int) -> Optional[Dict]:
    """原子地认领一个码：仅当 status=pending 时标记为 claimed。
    返回更新后的记录（含 gift 信息），失败返回 None（已被领/不存在）。
    """
    async with get_session() as session:
        try:
            result = await session.execute(
                select(GiftClaim)
                .where(GiftClaim.code == code)
                .with_for_update()  # 行锁，防并发领取
            )
            claim = result.scalars().first()
            if not claim or claim.status != 'pending':
                return None

            claim.status = 'claimed'
            claim.claimant_user_id = claimant_user_id
            claim.claimed_at = _now()
            await session.commit()
            logger.info("领取码 %s 被 %s 领取", code, claimant_user_id)
            return _model_to_dict(claim)
        except Exception as e:
            logger.error("认领取取码 %s 失败: %s", code, e, exc_info=True)
            return None


async def list_claims(limit: int = 20) -> List[Dict]:
    """列出最近的领取码（管理员查看用）"""
    async with get_session() as session:
        result = await session.execute(
            select(GiftClaim)
            .order_by(GiftClaim.created_at.desc())
            .limit(limit)
        )
        return [_model_to_dict(c) for c in result.scalars().all()]


async def get_pending_claims_count() -> int:
    """未领取的码数量（用于展示库存/防滥用）"""
    async with get_session() as session:
        result = await session.execute(
            select(GiftClaim).where(GiftClaim.status == 'pending')
        )
        return len(result.scalars().all())
