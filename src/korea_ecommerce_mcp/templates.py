from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as escape_xml

from pydantic import BaseModel, Field

from korea_ecommerce_mcp.models import ChannelId, ChannelPayload, MasterProduct

_PROFILE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_EXACT_PLACEHOLDER = re.compile(r"^\{\{\s*product\.([A-Za-z0-9_.-]+)\s*\}\}$")
_PLACEHOLDER = re.compile(r"\{\{\s*product\.([A-Za-z0-9_.-]+)\s*\}\}")


class ChannelProfile(BaseModel):
    profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    description: str = ""
    payloads: dict[ChannelId, ChannelPayload]


class ProfileLoader:
    """Loads reviewed local payload templates; it never evaluates code."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def list_profiles(self) -> list[dict[str, str]]:
        if not self.directory.exists():
            return []
        profiles: list[dict[str, str]] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                profile = self._load_path(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            profiles.append({"profile_id": profile.profile_id, "description": profile.description})
        return profiles

    def load(self, profile_id: str) -> ChannelProfile:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("invalid profile id")
        path = (self.directory / f"{profile_id}.json").resolve()
        if path.parent != self.directory:
            raise ValueError("profile path escapes the configured directory")
        if not path.is_file():
            raise ValueError(f"profile not found: {profile_id}")
        profile = self._load_path(path)
        if profile.profile_id != profile_id:
            raise ValueError("profile id does not match its filename")
        return profile

    def render(self, profile_id: str, product: MasterProduct) -> dict[ChannelId, ChannelPayload]:
        profile = self.load(profile_id)
        return {
            channel: ChannelPayload(
                body=_render_value(
                    deepcopy(payload.body),
                    product,
                    xml_escape=_is_xml_payload(payload),
                ),
                content_type=payload.content_type,
                metadata=_render_value(deepcopy(payload.metadata), product),
            )
            for channel, payload in profile.payloads.items()
        }

    @staticmethod
    def _load_path(path: Path) -> ChannelProfile:
        return ChannelProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _is_xml_payload(payload: ChannelPayload) -> bool:
    content_type = (payload.content_type or "").casefold()
    return isinstance(payload.body, str) and (
        "xml" in content_type or payload.body.lstrip().startswith("<")
    )


def _render_value(value: Any, product: MasterProduct, *, xml_escape: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: _render_value(item, product, xml_escape=xml_escape) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_render_value(item, product, xml_escape=xml_escape) for item in value]
    if not isinstance(value, str):
        return value

    exact = _EXACT_PLACEHOLDER.fullmatch(value)
    if exact:
        resolved = _lookup_product_value(product, exact.group(1))
        if xml_escape:
            return _xml_text(resolved)
        return resolved

    def replace(match: re.Match[str]) -> str:
        resolved = _lookup_product_value(product, match.group(1))
        if isinstance(resolved, (dict, list)):
            text = json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
        else:
            text = "" if resolved is None else str(resolved)
        return _xml_text(text) if xml_escape else text

    return _PLACEHOLDER.sub(replace, value)


def _xml_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return escape_xml(text, {'"': "&quot;", "'": "&apos;"})


def _lookup_product_value(product: MasterProduct, path: str) -> Any:
    value: Any = product.model_dump(mode="json")
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"unknown product template field: {path}")
        value = value[part]
    return value
