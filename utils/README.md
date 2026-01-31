# Utils Module Documentation

This module provides reusable utility functions for the earthquake analytics pipeline.

## Module Structure

```
utils/
├── __init__.py      # Module initialisation
├── helpers.py       # Core helper functions
├── datasource.py    # USGS API datasource registration
└── main.py          # Wheel entry point
```

---

## helpers.py

The main utility module containing functions organised into four categories.

### Catalog & Schema Management

#### `get_or_create_catalog_schema(spark, catalog, schema)`
Ensures the catalog and schema exist, creating them if necessary.

```python
from utils.helpers import get_or_create_catalog_schema

get_or_create_catalog_schema(spark, "earthquakes_dev", "usgs")
# ✓ Catalog/schema ready: earthquakes_dev.usgs
```

#### `get_table_path(catalog, schema, table)`
Builds a fully qualified table name.

```python
from utils.helpers import get_table_path

path = get_table_path("earthquakes_dev", "usgs", "bronze_events")
# Returns: "earthquakes_dev.usgs.bronze_events"
```

#### `table_exists(spark, table_path)`
Checks if a Delta table exists.

```python
from utils.helpers import table_exists

if table_exists(spark, "earthquakes_dev.usgs.bronze_events"):
    print("Table exists")
```

#### `get_date_range(start_date, end_date, lookback_days)`
Calculates date range for ingestion. If dates not provided, uses lookback_days from today.

```python
from utils.helpers import get_date_range

# Explicit dates
start, end = get_date_range("2024-01-01", "2024-01-31")

# Calculated from lookback
start, end = get_date_range(lookback_days=7)
# Returns last 7 days
```

---

### Write Operations

#### `write_delta_table(df, table_path, mode, merge_keys, partition_by)`
Writes a DataFrame to Delta with support for multiple modes.

**Modes:**
- `merge` - Upsert based on merge_keys (idempotent, default)
- `overwrite` - Full table replacement
- `append` - Add rows without deduplication

```python
from utils.helpers import write_delta_table

# Merge (upsert)
write_delta_table(
    df=df_events,
    table_path="catalog.schema.events",
    mode="merge",
    merge_keys=["event_id"]
)

# Overwrite
write_delta_table(
    df=df_events,
    table_path="catalog.schema.events",
    mode="overwrite"
)

# Append with partitioning
write_delta_table(
    df=df_events,
    table_path="catalog.schema.events",
    mode="append",
    partition_by=["year", "month"]
)
```

#### `delete_date_range(spark, table_path, date_column, start_date, end_date)`
Deletes records within a date range (useful for reprocessing).

```python
from utils.helpers import delete_date_range

delete_date_range(
    spark,
    "catalog.schema.events",
    date_column="event_time",
    start_date="2024-01-01",
    end_date="2024-01-31"
)
```

---

### Data Quality Helpers

#### `add_metadata_columns(df)`
Adds standard metadata columns for tracking.

```python
from utils.helpers import add_metadata_columns

df_with_meta = add_metadata_columns(df)
# Adds: _ingested_at (timestamp), _source ("usgs_api")
```

#### `get_table_stats(spark, table_path)`
Returns statistics about a Delta table.

```python
from utils.helpers import get_table_stats

stats = get_table_stats(spark, "catalog.schema.events")
# Returns: {
#   "exists": True,
#   "record_count": 10000,
#   "column_count": 25,
#   "last_modified": datetime(...),
#   "last_operation": "MERGE"
# }
```

#### `print_table_stats(spark, table_path)`
Prints formatted table statistics.

```python
from utils.helpers import print_table_stats

print_table_stats(spark, "catalog.schema.events")
# ==================================================
# Table: catalog.schema.events
# ==================================================
# Records:        10,000
# Columns:        25
# Last Modified:  2024-01-15 10:30:00
# Last Operation: MERGE
# ==================================================
```

---

### Change Data Feed (CDF) Helpers

#### `enable_cdf(spark, table_path)`
Enables Change Data Feed on an existing table.

```python
from utils.helpers import enable_cdf

enable_cdf(spark, "catalog.schema.events")
# ✓ CDF enabled on catalog.schema.events
```

#### `get_current_table_version(spark, table_path)`
Gets the current Delta version of a table.

```python
from utils.helpers import get_current_table_version

version = get_current_table_version(spark, "catalog.schema.events")
# Returns: 42
```

#### `get_latest_processed_version(spark, checkpoint_table, source_table)`
Gets the last processed version from a checkpoint table.

```python
from utils.helpers import get_latest_processed_version

last_version = get_latest_processed_version(
    spark,
    "catalog.schema._checkpoints",
    "catalog.schema.bronze_events"
)
# Returns: 40 (or None if no checkpoint)
```

#### `save_checkpoint(spark, checkpoint_table, source_table, processed_version, records_processed)`
Saves a processing checkpoint.

```python
from utils.helpers import save_checkpoint

save_checkpoint(
    spark,
    "catalog.schema._checkpoints",
    "catalog.schema.bronze_events",
    processed_version=42,
    records_processed=500
)
# ✓ Checkpoint saved: catalog.schema.bronze_events @ version 42
```

#### `read_cdf_changes(spark, table_path, start_version, end_version)`
Reads changes from a CDF-enabled table.

```python
from utils.helpers import read_cdf_changes

# Read changes from version 40 to 42
df_changes = read_cdf_changes(
    spark,
    "catalog.schema.events",
    start_version=40,
    end_version=42
)
# Returns DataFrame with _change_type, _commit_version, _commit_timestamp
```

#### `read_incremental_or_full(spark, source_table, checkpoint_table)`
Smart reader that uses CDF when available, falls back to full read.

```python
from utils.helpers import read_incremental_or_full

df, current_version, is_incremental = read_incremental_or_full(
    spark,
    "catalog.schema.bronze_events",
    "catalog.schema._checkpoints"
)

if is_incremental:
    print(f"Read {df.count()} changed records")
else:
    print(f"Full read of {df.count()} records")
```

#### `write_delta_table_with_cdf(df, table_path, mode, merge_keys, partition_by, enable_cdf)`
Writes with CDF enabled automatically on table creation.

```python
from utils.helpers import write_delta_table_with_cdf

write_delta_table_with_cdf(
    df=df_events,
    table_path="catalog.schema.events",
    mode="merge",
    merge_keys=["event_id"],
    enable_cdf=True  # default
)
# ✓ Created catalog.schema.events: 1,000 records (CDF enabled)
```

---

## datasource.py

Handles USGS API datasource registration for Lakehouse Federation.

#### `register_usgs_datasource(spark, catalog, schema)`
Registers the USGS Earthquake API as a foreign datasource.

```python
from utils.datasource import register_usgs_datasource

register_usgs_datasource(spark, "earthquakes_dev", "usgs")
```

---

## Usage in Notebooks

Import functions directly:

```python
from utils.helpers import (
    get_or_create_catalog_schema,
    get_table_path,
    write_delta_table_with_cdf,
    read_incremental_or_full,
    save_checkpoint,
    print_table_stats
)
```

---

## Building the Wheel

The utils module is packaged as a wheel for deployment:

```bash
uv build
```

This creates a wheel in `dist/` that's deployed with the Databricks bundle and installed on clusters.
