"""
Custom PySpark DataSource for USGS Earthquake API.

Enables reading earthquake data directly into Spark:
    spark.read.format("usgs") \
        .option("starttime", "2024-01-01") \
        .option("endtime", "2024-01-31") \
        .load()
"""

from pyspark.sql.datasource import DataSource, DataSourceReader, InputPartition
from pyspark.sql.types import *
from datetime import datetime, timedelta, timezone
import requests

class TimeRangePartition(InputPartition):
    def __init__(self, starttime: str, endtime: str, pid: int):
        self.starttime = starttime
        self.endtime = endtime
        self.pid = pid

class USGSDataSource(DataSource):
    @classmethod
    def name(cls):
        return "usgs"

    def schema(self):
        return StructType([
            StructField("type", StringType(), True),
            StructField("properties", MapType(StringType(), StringType(), True), True),
            StructField("geometry", MapType(StringType(), StringType(), True), True),
            StructField("id", StringType(), True),
        ])

    def reader(self, schema: StructType):
        return USGSDataSourceReader(schema, self.options)
    

class USGSDataSourceReader(DataSourceReader):
    def __init__(self, schema, options):
        self.schema = schema
        self.options = options

    def _parse_dt_utc(self, s: str) -> datetime:
        # Accept 'YYYY-MM-DD' (UTC midnight) or ISO8601.
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _to_usgs_iso(self, dt: datetime) -> str:
        # USGS accepts this well, plus we pass timezone=UTC.
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    def partitions(self):
        start_s = self.options.get("starttime")
        end_s = self.options.get("endtime")
        if not start_s or not end_s:
            raise ValueError("You must provide .option('starttime', ...) and .option('endtime', ...)")

        num_partitions = int(self.options.get("numPartitions", "10"))
        if num_partitions <= 0:
            raise ValueError("numPartitions must be > 0")

        start = self._parse_dt_utc(start_s)
        end = self._parse_dt_utc(end_s)
        if end <= start:
            raise ValueError(f"endtime must be after starttime. Got {start_s} -> {end_s}")

        total_seconds = (end - start).total_seconds()
        step = total_seconds / num_partitions

        parts = []
        for i in range(num_partitions):
            p_start = start + timedelta(seconds=step * i)
            p_end = start + timedelta(seconds=step * (i + 1))

            parts.append(
                TimeRangePartition(
                    starttime=self._to_usgs_iso(p_start),
                    endtime=self._to_usgs_iso(p_end),
                    pid=i
                )
            )
        return parts

    def read(self, partition: TimeRangePartition):
        import requests

        url = "https://earthquake.usgs.gov/fdsnws/event/1/query"

        params = {
            "format": "geojson",
            "starttime": partition.starttime,
            "endtime": partition.endtime
        }

        r = requests.get(url, params=params, timeout=30)

        if r.status_code != 200:
            # Include the partition range + USGS response so you can see WHY it 400'd
            raise RuntimeError(
                f"USGS HTTP {r.status_code} partition={partition.pid} "
                f"start={partition.starttime} end={partition.endtime}. "
                f"Response: {r.text[:500]}"
            )

        payload = r.json()
        features = payload.get("features", [])

        for f in features:
            # dicts feed MapType columns fine
            yield (f.get("type"), f.get("properties"), f.get("geometry"), f.get("id"))


def register_usgs_datasource(spark) -> None:
    """
    Register the USGS datasource with Spark.

    Call this before using spark.read.format("usgs").

    Args:
        spark: Active SparkSession
    """
    spark.dataSource.register(USGSDataSource)
    print("✓ USGS DataSource registered")
