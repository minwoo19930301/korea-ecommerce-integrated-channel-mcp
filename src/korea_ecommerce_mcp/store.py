from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from korea_ecommerce_mcp.models import ChannelId, FanoutResult, MasterProduct

_SCHEMA_VERSION = 1


def _json_dumps(value: Any) -> str:
    """Serialize JSON consistently so persisted payloads are reproducible."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _model_json(model: MasterProduct | FanoutResult) -> str:
    return _json_dumps(model.model_dump(mode="json"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class SQLiteStore:
    """Small synchronous persistence layer with one SQLite connection per operation."""

    def __init__(self, database_path: str | Path) -> None:
        raw_path = str(database_path)
        self._keeper: sqlite3.Connection | None = None

        # A named shared-memory database preserves per-operation connections while
        # remaining convenient for callers and tests that pass ``:memory:``.
        if raw_path == ":memory:":
            self._database = (
                f"file:korea_ecommerce_integrated_channel_{uuid4().hex}?mode=memory&cache=shared"
            )
            self._uri = True
            self._keeper = self._new_connection()
            self._bootstrap(self._keeper)
        else:
            path = Path(database_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._database = str(path)
            self._uri = False
            with self._connection() as connection:
                self._bootstrap(connection)

    def close(self) -> None:
        """Release the shared-memory keeper connection, when one is in use."""

        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database,
            timeout=30.0,
            uri=self._uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _bootstrap(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS master_products (
                sku TEXT PRIMARY KEY,
                product_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_mappings (
                sku TEXT NOT NULL,
                channel TEXT NOT NULL,
                external_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (sku, channel),
                UNIQUE (channel, external_id),
                FOREIGN KEY (sku) REFERENCES master_products (sku) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_channel_mappings_external_id
                ON channel_mappings (channel, external_id);

            CREATE TABLE IF NOT EXISTS job_results (
                job_id TEXT PRIMARY KEY,
                sku TEXT NOT NULL,
                operation TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_job_results_sku
                ON job_results (sku, created_at);

            CREATE TABLE IF NOT EXISTS idempotency_results (
                idempotency_key TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_idempotency_results_job_id
                ON idempotency_results (job_id);
            """
        )
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def save_product(self, product: MasterProduct) -> MasterProduct:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO master_products (sku, product_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (sku) DO UPDATE SET
                    product_json = excluded.product_json,
                    updated_at = excluded.updated_at
                """,
                (product.sku, _model_json(product), now, now),
            )
        return product

    def get_product(self, sku: str) -> MasterProduct | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT product_json FROM master_products WHERE sku = ?",
                (sku,),
            ).fetchone()
        if row is None:
            return None
        return MasterProduct.model_validate_json(row["product_json"])

    def upsert_mapping(
        self,
        sku: str,
        channel: ChannelId | str,
        external_id: str,
        status: str,
    ) -> dict[str, str]:
        normalized_channel = ChannelId(channel).value
        if not external_id.strip():
            raise ValueError("external_id must not be blank")
        if not status.strip():
            raise ValueError("status must not be blank")

        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO channel_mappings (
                    sku, channel, external_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (sku, channel) DO UPDATE SET
                    external_id = excluded.external_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (sku, normalized_channel, external_id, status, now, now),
            )
            row = connection.execute(
                """
                SELECT sku, channel, external_id, status, created_at, updated_at
                FROM channel_mappings
                WHERE sku = ? AND channel = ?
                """,
                (sku, normalized_channel),
            ).fetchone()
        assert row is not None
        return dict(row)

    def get_mapping(self, sku: str, channel: ChannelId | str) -> dict[str, str] | None:
        normalized_channel = ChannelId(channel).value
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT sku, channel, external_id, status, created_at, updated_at
                FROM channel_mappings
                WHERE sku = ? AND channel = ?
                """,
                (sku, normalized_channel),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_mappings(self, sku: str) -> list[dict[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sku, channel, external_id, status, created_at, updated_at
                FROM channel_mappings
                WHERE sku = ?
                ORDER BY channel
                """,
                (sku,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_job(self, result: FanoutResult) -> FanoutResult:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO job_results (
                    job_id, sku, operation, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id) DO UPDATE SET
                    sku = excluded.sku,
                    operation = excluded.operation,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    result.job_id,
                    result.sku,
                    result.operation.value,
                    _model_json(result),
                    now,
                    now,
                ),
            )
        return result

    def get_job(self, job_id: str) -> FanoutResult | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT result_json FROM job_results WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return FanoutResult.model_validate_json(row["result_json"])

    def get_idempotency(self, key: str) -> FanoutResult | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT result_json
                FROM idempotency_results
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        return FanoutResult.model_validate_json(row["result_json"])

    def save_idempotency(self, key: str, result: FanoutResult) -> FanoutResult:
        """Store the first result for a key and never replace it on a retry."""

        if not key.strip():
            raise ValueError("idempotency key must not be blank")

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_results (
                    idempotency_key, job_id, result_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (key, result.job_id, _model_json(result), _utc_now()),
            )
            row = connection.execute(
                """
                SELECT result_json
                FROM idempotency_results
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()

        assert row is not None
        return FanoutResult.model_validate_json(row["result_json"])
