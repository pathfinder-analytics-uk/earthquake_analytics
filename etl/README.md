# ETL Pipeline Documentation

This directory contains the medallion architecture ETL pipeline for processing USGS earthquake data.

## Pipeline Overview

```
01_raw_ingestion.ipynb → 02_silver_transformation.ipynb → 03_gold_aggregation.ipynb
      (Bronze)                    (Silver)                       (Gold)
```

## Notebooks

### 01_bronze_ingestion.ipynb (Bronze Layer)

**Purpose:** Ingest raw earthquake data from the USGS API into Delta Lake.

**Source:** USGS Earthquake API (`https://earthquake.usgs.gov/fdsnws/event/1/`)

**Target Table:** `{catalog}.{schema}.raw_events`

**Key Features:**
- Uses Spark Python Data Source API to register the API as a datasource
- Configurable date range via widgets
- Idempotent writes using merge on event `id`
- CDF enabled for downstream incremental processing

**Parameters:**
| Widget | Description | Default |
|--------|-------------|---------|
| `catalog` | Unity Catalog name | `earthquakes_dev` |
| `schema` | Schema name | `usgs` |
| `start_date` | Start of date range | (calculated) |
| `end_date` | End of date range | (calculated) |
| `lookback_days` | Days to look back if dates not set | `30` |
| `write_mode` | `merge` or `overwrite` | `merge` |

---

### 02_silver_transformation.ipynb (Silver Layer)

**Purpose:** Cleanse, validate, and enrich raw earthquake data.

**Source Table:** `{catalog}.{schema}.raw_events`

**Target Table:** `{catalog}.{schema}.silver_events`

**Transformations:**
1. **Type Casting** - Convert string fields to appropriate types (Double, Integer, Timestamp)
2. **Timestamp Conversion** - Convert Unix milliseconds to Timestamp
3. **Coordinate Extraction** - Parse longitude, latitude, depth from geometry
4. **Magnitude Categorisation** - Classify earthquakes by magnitude:
   - Great (8.0+), Major (7.0+), Strong (6.0+), Moderate (5.0+), Light (4.0+), Minor (3.0+), Micro (<3.0)
5. **Depth Categorisation** - Classify by depth:
   - Shallow (<70km), Intermediate (70-300km), Deep (>300km)
6. **Region Extraction** - Parse region from place description
7. **Deduplication** - Remove duplicates based on event_id

**Incremental Processing:**
- Uses CDF to read only new/changed records from raw_events
- Checkpoints track last processed version
- Falls back to full read on first run

**Parameters:**
| Widget | Description | Default |
|--------|-------------|---------|
| `catalog` | Unity Catalog name | `earthquakes_dev` |
| `schema` | Schema name | `usgs` |
| `write_mode` | `merge` or `overwrite` | `merge` |

---

### 03_gold_aggregation.ipynb (Gold Layer)

**Purpose:** Create analytics-ready aggregated tables for the Streamlit dashboard and Genie space.

**Source Table:** `{catalog}.{schema}.silver_events`

**Target Tables:**

#### gold_events_map
Individual earthquake events optimised for map visualisation.

| Column | Description |
|--------|-------------|
| `event_id` | Unique identifier |
| `latitude`, `longitude` | Coordinates |
| `significance` | USGS significance score (0-1000+) |
| `severity` | Category based on significance |
| `size_factor` | Scaled significance for visualisation |
| `magnitude`, `depth_km` | Event metrics |
| `event_time` | When the earthquake occurred |
| `alert_level`, `has_tsunami_warning` | Impact indicators |

#### gold_daily_summary
Daily aggregated statistics for time-series analysis.

| Column | Description |
|--------|-------------|
| `date` | Aggregation date |
| `total_events` | Count of earthquakes |
| `high_significance_events` | Count where significance >= 500 |
| `avg_significance`, `max_significance` | Significance stats |
| `count_severe`, `count_major`, etc. | Counts by category |

#### gold_regional_summary
Regional aggregated statistics.

| Column | Description |
|--------|-------------|
| `region` | Geographic region |
| `total_events` | Count of earthquakes |
| `avg_significance`, `max_significance` | Significance stats |
| `centroid_lat`, `centroid_lon` | Region centroid |
| `first_event`, `last_event` | Time range |

**Incremental Processing:**
- Reads CDF changes from silver_events
- Identifies affected dates/regions
- Only recomputes aggregations for affected partitions
- Uses merge to update existing rows

---

## Running the Pipeline

### Manual Execution

Run notebooks in order from the Databricks workspace:
1. `01_raw_ingestion.ipynb`
2. `02_silver_transformation.ipynb`
3. `03_gold_aggregation.ipynb`

### Via Databricks CLI

```bash
databricks bundle run earthquake_etl_job
```

### Scheduled Execution

The pipeline is configured to run daily via the job defined in `resources/`. The schedule is paused in dev mode.

---

## Data Lineage

```
USGS API
    │
    ▼
raw_events (Bronze)
    │ CDF
    ▼
silver_events (Silver)
    │ CDF
    ├──────────────────────┬──────────────────────┐
    ▼                      ▼                      ▼
gold_events_map    gold_daily_summary    gold_regional_summary
    │                      │                      │
    └──────────────────────┼──────────────────────┘
                           ▼
                    Streamlit App / Genie Space
```

---

## Checkpointing

The pipeline uses a `_checkpoints` table to track processed versions:

| Column | Description |
|--------|-------------|
| `source_table` | Table being tracked |
| `processed_version` | Last processed Delta version |
| `records_processed` | Count of records in that batch |
| `processed_at` | Timestamp of processing |

This enables true incremental processing - each run only processes new data.

---

## Troubleshooting

### Full Reprocessing

To force a full reprocessing, either:

1. Delete the checkpoint entry:
   ```sql
   DELETE FROM {catalog}.{schema}._checkpoints
   WHERE source_table = '{catalog}.{schema}.raw_events'
   ```

2. Or set `write_mode` to `overwrite` in the notebook widgets.

### CDF Not Enabled

If you see errors about CDF not being enabled, run:
```sql
ALTER TABLE {catalog}.{schema}.raw_events
SET TBLPROPERTIES (delta.enableChangeDataFeed = true)
```
