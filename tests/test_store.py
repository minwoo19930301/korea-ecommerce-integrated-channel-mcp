from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from korea_ecommerce_mcp.models import (
    AdapterResult,
    ChannelId,
    FanoutResult,
    MasterProduct,
    ProductOperation,
)
from korea_ecommerce_mcp.store import SQLiteStore


def product(sku: str = "SKU-1", *, name: str = "테스트 상품") -> MasterProduct:
    return MasterProduct(
        sku=sku,
        name=name,
        price=12_000,
        stock=7,
        image_urls=["https://example.test/image.jpg"],
        metadata={"z": 1, "a": {"b": 2, "a": 1}},
    )


def job(job_id: str = "job-1", *, success: bool = True) -> FanoutResult:
    return FanoutResult(
        job_id=job_id,
        sku="SKU-1",
        operation=ProductOperation.CREATE,
        dry_run=False,
        started_at=datetime(2026, 7, 18, 1, 2, 3, tzinfo=UTC),
        finished_at=datetime(2026, 7, 18, 1, 2, 4, tzinfo=UTC),
        results=[
            AdapterResult(
                channel=ChannelId.SMARTSTORE,
                operation=ProductOperation.CREATE,
                success=success,
                external_id="external-1" if success else None,
                status_code=200 if success else 503,
                retryable=not success,
                error=None if success else "temporary failure",
                data={"z": 1, "a": 2},
            )
        ],
    )


def test_bootstrap_enables_wal_and_foreign_key_schema(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "store.sqlite3"
    store = SQLiteStore(database)

    with sqlite3.connect(database) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_list(channel_mappings)").fetchall()

    assert journal_mode == "wal"
    assert user_version == 1
    assert any(row[2] == "master_products" and row[6] == "CASCADE" for row in foreign_keys)

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_mapping("missing", ChannelId.COUPANG, "outside-1", "active")


def test_product_round_trip_update_and_deterministic_json(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"
    store = SQLiteStore(database)
    original = product()

    assert store.get_product(original.sku) is None
    assert store.save_product(original) == original
    assert store.get_product(original.sku) == original

    updated = original.model_copy(update={"name": "수정 상품", "stock": 3})
    store.save_product(updated)
    assert store.get_product(original.sku) == updated

    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT product_json FROM master_products WHERE sku = ?", (original.sku,)
        ).fetchone()[0]

    assert raw == json.dumps(
        updated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def test_mapping_upsert_get_and_list(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite3")
    store.save_product(product())

    created = store.upsert_mapping("SKU-1", ChannelId.ESM, "external-1", "active")
    assert created["external_id"] == "external-1"
    assert created["status"] == "active"
    assert store.get_mapping("SKU-1", ChannelId.ESM) == created
    assert store.get_mapping("SKU-1", ChannelId.ELEVENST) is None

    updated = store.upsert_mapping("SKU-1", "esm", "external-2", "stopped")
    store.upsert_mapping("SKU-1", ChannelId.COUPANG, "outside-2", "active")

    assert updated["external_id"] == "external-2"
    assert updated["status"] == "stopped"
    assert updated["created_at"] == created["created_at"]
    assert [item["channel"] for item in store.list_mappings("SKU-1")] == [
        "coupang",
        "esm",
    ]


def test_mapping_rejects_invalid_values(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite3")
    store.save_product(product())

    with pytest.raises(ValueError, match="external_id"):
        store.upsert_mapping("SKU-1", ChannelId.ESM, "  ", "active")
    with pytest.raises(ValueError, match="status"):
        store.upsert_mapping("SKU-1", ChannelId.ESM, "external-1", "  ")
    with pytest.raises(ValueError):
        store.upsert_mapping("SKU-1", "unknown", "external-1", "active")


def test_job_round_trip_and_update(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite3")
    initial = job()

    assert store.get_job(initial.job_id) is None
    assert store.save_job(initial) == initial
    assert store.get_job(initial.job_id) == initial

    updated = job(success=False)
    store.save_job(updated)
    assert store.get_job(initial.job_id) == updated


def test_idempotency_preserves_first_result(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite3")
    first = job("job-first")
    second = job("job-second", success=False)

    assert store.get_idempotency("request-key") is None
    assert store.save_idempotency("request-key", first) == first
    assert store.save_idempotency("request-key", second) == first
    assert store.get_idempotency("request-key") == first

    with pytest.raises(ValueError, match="idempotency"):
        store.save_idempotency("  ", first)


def test_shared_memory_database_uses_connection_per_operation() -> None:
    with SQLiteStore(":memory:") as store:
        store.save_product(product())
        store.upsert_mapping("SKU-1", ChannelId.ELEVENST, "outside-1", "active")

        assert store.get_product("SKU-1") == product()
        assert store.get_mapping("SKU-1", ChannelId.ELEVENST) is not None


def test_concurrent_operations_use_independent_connections(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite3")

    def save(index: int) -> MasterProduct | None:
        item = product(f"SKU-{index}")
        store.save_product(item)
        store.upsert_mapping(item.sku, ChannelId.COUPANG, f"outside-{index}", "active")
        return store.get_product(item.sku)

    with ThreadPoolExecutor(max_workers=8) as executor:
        saved = list(executor.map(save, range(24)))

    assert saved == [product(f"SKU-{index}") for index in range(24)]
    assert all(store.list_mappings(f"SKU-{index}") for index in range(24))
