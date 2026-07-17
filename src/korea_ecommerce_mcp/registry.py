from __future__ import annotations

from korea_ecommerce_mcp.adapters.base import ChannelAdapter
from korea_ecommerce_mcp.adapters.coupang import CoupangAdapter
from korea_ecommerce_mcp.adapters.elevenst import ElevenStreetAdapter
from korea_ecommerce_mcp.adapters.esm import EsmAdapter
from korea_ecommerce_mcp.adapters.naver import NaverAdapter
from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.models import ChannelId


def build_adapters(settings: Settings) -> dict[ChannelId, ChannelAdapter]:
    return {
        ChannelId.SMARTSTORE: NaverAdapter(settings),
        ChannelId.COUPANG: CoupangAdapter(settings),
        ChannelId.ELEVENST: ElevenStreetAdapter(settings),
        ChannelId.ESM: EsmAdapter(settings),
    }
