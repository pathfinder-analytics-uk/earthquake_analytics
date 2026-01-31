"""
Helper functions for the USGS Earthquake pipeline.

Provides reusable utilities for:
- Catalog/schema management
- Table operations (merge, overwrite, append)
- Date range handling
- Idempotent writes
"""

from datetime import datetime, timedelta
from typing import Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col
from delta.tables import DeltaTable


# =============================================================================
# Catalog & Schema Management
# =============================================================================

def get_or_create_catalog_schema(
    spark: SparkSession,
    catalog: str,
    schema: str
) -> None:
    """
    Ensure catalog and schema exist, creating if necessary.

    Args:
        spark: Active SparkSession
        catalog: Catalog name
        schema: Schema name
    """
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    print(f"✓ Catalog/schema ready: {catalog}.{schema}")


def get_table_path(catalog: str, schema: str, table: str) -> str:
    """
    Build fully qualified table name.

    Args:
        catalog: Catalog name
        schema: Schema name
        table: Table name

    Returns:
        Fully qualified table name (catalog.schema.table)
    """
    return f"{catalog}.{schema}.{table}"


def table_exists(spark: SparkSession, table_path: str) -> bool:
    """
    Check if a Delta table exists.

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name

    Returns:
        True if table exists
    """
    try:
        spark.sql(f"DESCRIBE TABLE {table_path}")
        return True
    except Exception:
        return False


# =============================================================================
# Date Range Helpers
# =============================================================================

def get_date_range(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    lookback_days: int = 1
) -> tuple[str, str]:
    """
    Get start and end dates for ingestion.

    If dates not provided, calculates based on lookback_days from today.

    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
        lookback_days: Days to look back if dates not provided

    Returns:
        Tuple of (start_date, end_date) as strings
    """
    if start_date and end_date:
        return start_date, end_date

    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# =============================================================================
# Write Operations (Idempotent)
# =============================================================================

def write_delta_table(
    df: DataFrame,
    table_path: str,
    mode: str = "merge",
    merge_keys: Optional[list[str]] = None,
    partition_by: Optional[list[str]] = None
) -> int:
    """
    Write DataFrame to Delta table with multiple mode support.

    Modes:
    - "merge": Upsert based on merge_keys (idempotent, default)
    - "overwrite": Full table replacement
    - "append": Add rows without deduplication

    Args:
        df: DataFrame to write
        table_path: Fully qualified table name
        mode: Write mode (merge/overwrite/append)
        merge_keys: Columns for merge condition (required for merge mode)
        partition_by: Optional partition columns

    Returns:
        Number of records written
    """
    spark = df.sparkSession
    record_count = df.count()

    if mode == "overwrite":
        writer = df.write.format("delta").mode("overwrite")
        if partition_by:
            writer = writer.partitionBy(*partition_by)
        writer.saveAsTable(table_path)
        print(f"✓ Overwrote {table_path}: {record_count:,} records")

    elif mode == "append":
        writer = df.write.format("delta").mode("append")
        if partition_by:
            writer = writer.partitionBy(*partition_by)
        writer.saveAsTable(table_path)
        print(f"✓ Appended to {table_path}: {record_count:,} records")

    elif mode == "merge":
        if not merge_keys:
            raise ValueError("merge_keys required for merge mode")

        if not table_exists(spark, table_path):
            # First write - create table
            writer = df.write.format("delta").mode("overwrite")
            if partition_by:
                writer = writer.partitionBy(*partition_by)
            writer.saveAsTable(table_path)
            print(f"✓ Created {table_path}: {record_count:,} records")
        else:
            # Merge into existing table
            delta_table = DeltaTable.forName(spark, table_path)

            merge_condition = " AND ".join([
                f"target.{key} = source.{key}" for key in merge_keys
            ])

            delta_table.alias("target").merge(
                df.alias("source"),
                merge_condition
            ).whenMatchedUpdateAll(
            ).whenNotMatchedInsertAll(
            ).execute()

            print(f"✓ Merged into {table_path}: {record_count:,} records processed")
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'merge', 'overwrite', or 'append'")

    return record_count


def delete_date_range(
    spark: SparkSession,
    table_path: str,
    date_column: str,
    start_date: str,
    end_date: str
) -> None:
    """
    Delete records within a date range (for reprocessing).

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name
        date_column: Column containing the date/timestamp
        start_date: Start of range to delete
        end_date: End of range to delete
    """
    if not table_exists(spark, table_path):
        print(f"Table {table_path} doesn't exist, nothing to delete")
        return

    delta_table = DeltaTable.forName(spark, table_path)
    delta_table.delete(
        f"{date_column} >= '{start_date}' AND {date_column} < '{end_date}'"
    )
    print(f"✓ Deleted records from {start_date} to {end_date} in {table_path}")


# =============================================================================
# Data Quality Helpers
# =============================================================================

def add_metadata_columns(df: DataFrame) -> DataFrame:
    """
    Add standard metadata columns for tracking.

    Adds:
    - _ingested_at: Timestamp when record was ingested
    - _source: Data source identifier

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with metadata columns
    """
    from pyspark.sql.functions import current_timestamp, lit

    return df.withColumn("_ingested_at", current_timestamp()) \
             .withColumn("_source", lit("usgs_api"))


def get_table_stats(spark: SparkSession, table_path: str) -> dict:
    """
    Get basic statistics for a Delta table.

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name

    Returns:
        Dict with record_count, partition_count, last_modified
    """
    if not table_exists(spark, table_path):
        return {"exists": False}

    df = spark.table(table_path)
    history = spark.sql(f"DESCRIBE HISTORY {table_path} LIMIT 1").collect()

    return {
        "exists": True,
        "record_count": df.count(),
        "column_count": len(df.columns),
        "last_modified": history[0]["timestamp"] if history else None,
        "last_operation": history[0]["operation"] if history else None
    }


def print_table_stats(spark: SparkSession, table_path: str) -> None:
    """Print formatted table statistics."""
    stats = get_table_stats(spark, table_path)

    if not stats["exists"]:
        print(f"Table {table_path} does not exist")
        return

    print(f"\n{'='*50}")
    print(f"Table: {table_path}")
    print(f"{'='*50}")
    print(f"Records:        {stats['record_count']:,}")
    print(f"Columns:        {stats['column_count']}")
    print(f"Last Modified:  {stats['last_modified']}")
    print(f"Last Operation: {stats['last_operation']}")
    print(f"{'='*50}\n")


# =============================================================================
# Change Data Feed (CDF) Helpers
# =============================================================================

def enable_cdf(spark: SparkSession, table_path: str) -> None:
    """
    Enable Change Data Feed on an existing Delta table.

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name
    """
    if not table_exists(spark, table_path):
        print(f"Table {table_path} doesn't exist, CDF will be enabled on creation")
        return

    spark.sql(f"""
        ALTER TABLE {table_path}
        SET TBLPROPERTIES (delta.enableChangeDataFeed = true)
    """)
    print(f"✓ CDF enabled on {table_path}")


def get_latest_processed_version(
    spark: SparkSession,
    checkpoint_table: str,
    source_table: str
) -> Optional[int]:
    """
    Get the last processed version from a checkpoint table.

    Args:
        spark: Active SparkSession
        checkpoint_table: Table storing processing checkpoints
        source_table: The source table we're tracking

    Returns:
        Last processed version number, or None if no checkpoint exists
    """
    if not table_exists(spark, checkpoint_table):
        return None

    result = spark.sql(f"""
        SELECT max(processed_version) as last_version
        FROM {checkpoint_table}
        WHERE source_table = '{source_table}'
    """).collect()

    if result and result[0]["last_version"] is not None:
        return int(result[0]["last_version"])
    return None


def get_current_table_version(spark: SparkSession, table_path: str) -> int:
    """
    Get the current version of a Delta table.

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name

    Returns:
        Current version number
    """
    history = spark.sql(f"DESCRIBE HISTORY {table_path} LIMIT 1").collect()
    return int(history[0]["version"])


def save_checkpoint(
    spark: SparkSession,
    checkpoint_table: str,
    source_table: str,
    processed_version: int,
    records_processed: int
) -> None:
    """
    Save a processing checkpoint.

    Args:
        spark: Active SparkSession
        checkpoint_table: Table storing processing checkpoints
        source_table: The source table we processed
        processed_version: Version number we processed up to
        records_processed: Number of records processed
    """
    from pyspark.sql.functions import lit, current_timestamp

    checkpoint_df = spark.createDataFrame([(
        source_table,
        processed_version,
        records_processed
    )], ["source_table", "processed_version", "records_processed"]) \
        .withColumn("processed_at", current_timestamp())

    checkpoint_df.write.format("delta").mode("append").saveAsTable(checkpoint_table)
    print(f"✓ Checkpoint saved: {source_table} @ version {processed_version}")


def read_cdf_changes(
    spark: SparkSession,
    table_path: str,
    start_version: Optional[int] = None,
    end_version: Optional[int] = None
) -> DataFrame:
    """
    Read changes from a CDF-enabled Delta table.

    Args:
        spark: Active SparkSession
        table_path: Fully qualified table name
        start_version: Starting version (exclusive), None for full read
        end_version: Ending version (inclusive), None for latest

    Returns:
        DataFrame with changes (includes _change_type, _commit_version, _commit_timestamp)
    """
    reader = spark.read.format("delta").option("readChangeFeed", "true")

    if start_version is not None:
        # Start from the version AFTER the last processed
        reader = reader.option("startingVersion", start_version + 1)
    else:
        # Full initial load - read from version 0
        reader = reader.option("startingVersion", 0)

    if end_version is not None:
        reader = reader.option("endingVersion", end_version)

    return reader.table(table_path)


def read_incremental_or_full(
    spark: SparkSession,
    source_table: str,
    checkpoint_table: str
) -> tuple[DataFrame, int, bool]:
    """
    Read incrementally using CDF if possible, otherwise full table.

    Args:
        spark: Active SparkSession
        source_table: Table to read from
        checkpoint_table: Table with processing checkpoints

    Returns:
        Tuple of (DataFrame, current_version, is_incremental)
    """
    current_version = get_current_table_version(spark, source_table)
    last_processed = get_latest_processed_version(spark, checkpoint_table, source_table)

    if last_processed is not None and last_processed < current_version:
        # Incremental read using CDF
        print(f"📊 Incremental read: version {last_processed + 1} to {current_version}")
        df = read_cdf_changes(spark, source_table, start_version=last_processed, end_version=current_version)
        # Filter to only inserts and updates (ignore deletes for downstream processing)
        df = df.filter(col("_change_type").isin(["insert", "update_postimage"]))
        
        return df, current_version, True
    elif last_processed == current_version:
        # No new data
        print(f"✓ No new data (already at version {current_version})")
        return spark.createDataFrame([], spark.table(source_table).schema), current_version, True
    else:
        # Full read (first run or CDF not available)
        print(f"📊 Full table read (version {current_version})")
        df = spark.table(source_table)
        return df, current_version, False


def write_delta_table_with_cdf(
    df: DataFrame,
    table_path: str,
    mode: str = "merge",
    merge_keys: Optional[list[str]] = None,
    partition_by: Optional[list[str]] = None,
    enable_cdf: bool = True
) -> int:
    """
    Write DataFrame to Delta table with CDF enabled.

    Same as write_delta_table but ensures CDF is enabled on creation.

    Args:
        df: DataFrame to write
        table_path: Fully qualified table name
        mode: Write mode (merge/overwrite/append)
        merge_keys: Columns for merge condition (required for merge mode)
        partition_by: Optional partition columns
        enable_cdf: Whether to enable CDF (default True)

    Returns:
        Number of records written
    """
    spark = df.sparkSession
    record_count = df.count()

    # Build table properties
    tbl_properties = {}
    if enable_cdf:
        tbl_properties["delta.enableChangeDataFeed"] = "true"

    if mode == "overwrite":
        writer = df.write.format("delta").mode("overwrite")
        if partition_by:
            writer = writer.partitionBy(*partition_by)
        if tbl_properties:
            writer = writer.option("overwriteSchema", "true")
            for k, v in tbl_properties.items():
                writer = writer.option(k, v)
        writer.saveAsTable(table_path)
        print(f"✓ Overwrote {table_path}: {record_count:,} records (CDF enabled)")

    elif mode == "append":
        writer = df.write.format("delta").mode("append")
        if partition_by:
            writer = writer.partitionBy(*partition_by)
        writer.saveAsTable(table_path)
        print(f"✓ Appended to {table_path}: {record_count:,} records")

    elif mode == "merge":
        if not merge_keys:
            raise ValueError("merge_keys required for merge mode")

        if not table_exists(spark, table_path):
            # First write - create table with CDF enabled
            writer = df.write.format("delta").mode("overwrite")
            if partition_by:
                writer = writer.partitionBy(*partition_by)
            # Set table properties for CDF
            for k, v in tbl_properties.items():
                writer = writer.option(k, v)
            writer.saveAsTable(table_path)
            print(f"✓ Created {table_path}: {record_count:,} records (CDF enabled)")
        else:
            # Merge into existing table
            delta_table = DeltaTable.forName(spark, table_path)

            merge_condition = " AND ".join([
                f"target.{key} = source.{key}" for key in merge_keys
            ])

            delta_table.alias("target").merge(
                df.alias("source"),
                merge_condition
            ).whenMatchedUpdateAll(
            ).whenNotMatchedInsertAll(
            ).execute()

            print(f"✓ Merged into {table_path}: {record_count:,} records processed")
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'merge', 'overwrite', or 'append'")

    return record_count
