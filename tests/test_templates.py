import json

import pytest

from korea_ecommerce_mcp.models import ChannelId, MasterProduct
from korea_ecommerce_mcp.templates import ProfileLoader


def product() -> MasterProduct:
    return MasterProduct(
        sku="SKU-1",
        name="Sample",
        price=12000,
        stock=7,
        metadata={"category": "A100"},
    )


def test_profile_preserves_native_types_and_renders_nested_metadata(tmp_path):
    profile = {
        "profile_id": "default",
        "payloads": {
            "smartstore": {
                "body": {
                    "name": "{{product.name}}",
                    "price": "{{product.price}}",
                    "category": "{{product.metadata.category}}",
                    "label": "sku={{ product.sku }}",
                }
            }
        },
    }
    (tmp_path / "default.json").write_text(json.dumps(profile), encoding="utf-8")

    rendered = ProfileLoader(tmp_path).render("default", product())

    body = rendered[ChannelId.SMARTSTORE].body
    assert body == {
        "name": "Sample",
        "price": 12000,
        "category": "A100",
        "label": "sku=SKU-1",
    }


def test_profile_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="invalid profile id"):
        ProfileLoader(tmp_path).load("../secret")


def test_profile_rejects_unknown_product_field(tmp_path):
    profile = {
        "profile_id": "invalid",
        "payloads": {"coupang": {"body": {"x": "{{product.not_here}}"}}},
    }
    (tmp_path / "invalid.json").write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown product template field"):
        ProfileLoader(tmp_path).render("invalid", product())


def test_xml_profile_escapes_product_values(tmp_path):
    profile = {
        "profile_id": "xml-safe",
        "payloads": {
            "elevenst": {
                "content_type": "application/xml",
                "body": '<Product name="{{product.name}}"><sku>{{product.sku}}</sku></Product>',
            }
        },
    }
    (tmp_path / "xml-safe.json").write_text(json.dumps(profile), encoding="utf-8")
    unsafe = MasterProduct(
        sku="A&B<1>",
        name="Quote \" and apostrophe '",
        price=1000,
        stock=1,
    )

    rendered = ProfileLoader(tmp_path).render("xml-safe", unsafe)

    assert rendered[ChannelId.ELEVENST].body == (
        '<Product name="Quote &quot; and apostrophe &apos;"><sku>A&amp;B&lt;1&gt;</sku></Product>'
    )
