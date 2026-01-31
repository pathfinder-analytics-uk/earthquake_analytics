"""
Earthquake Dashboard - Streamlit App

Visualizes USGS earthquake data with an interactive map
where points are sized by significance.
"""

import os
import streamlit as st
import pandas as pd
import pydeck as pdk
from datetime import datetime, timedelta
from databricks import sql
from databricks.sdk.core import Config

# =============================================================================
# Configuration
# =============================================================================

st.set_page_config(
    page_title="Earthquake Dashboard",
    page_icon="🌍",
    layout="wide"
)

# Connection settings from environment
CATALOG = os.getenv("CATALOG")
SCHEMA = os.getenv("SCHEMA")


# =============================================================================
# Data Loading
# =============================================================================


def sqlQuery(query: str) -> pd.DataFrame:
    """Execute SQL query with configurable timeout."""
    cfg = Config()
    with sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{os.getenv('DATABRICKS_WAREHOUSE_ID')}",
        credentials_provider=lambda: cfg.authenticate,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall_arrow().to_pandas()

@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_events(min_significance: int = 0, days: int = 30) -> pd.DataFrame:
    """Load earthquake events for map display, filtered by significance."""
    # For large date ranges, use longer timeout
    timeout = 600 if days > 365 else 300

    query = f"""
        SELECT *
        FROM {CATALOG}.{SCHEMA}.gold_events_map
        WHERE significance >= {min_significance}
          AND event_time >= current_timestamp() - INTERVAL {days} DAY
        ORDER BY event_time DESC
    """
    return sqlQuery(query, timeout=timeout)


@st.cache_data(ttl=300)
def load_daily_summary(days: int = 30) -> pd.DataFrame:
    """Load daily summary statistics."""
    timeout = 600 if days > 365 else 300

    query = f"""
        SELECT *
        FROM {CATALOG}.{SCHEMA}.gold_daily_summary
        WHERE date >= current_date() - INTERVAL {days} DAY
        ORDER BY date
    """
    return sqlQuery(query, timeout=timeout)

@st.cache_data(ttl=300)
def load_regional_summary(min_events: int = 5) -> pd.DataFrame:
    """Load regional summary statistics."""
    query = f"""
        SELECT *
        FROM {CATALOG}.{SCHEMA}.gold_regional_summary
        WHERE total_events >= {min_events}
        ORDER BY total_events DESC
        LIMIT 50
    """
    return sqlQuery(query)


# =============================================================================
# Color Mapping (based on significance thresholds)
# =============================================================================

SEVERITY_COLORS = {
    "severe": [255, 0, 0, 200],      # Red - significance >= 600
    "major": [255, 127, 0, 200],     # Orange - significance >= 400
    "moderate": [255, 255, 0, 200],  # Yellow - significance >= 200
    "minor": [0, 255, 0, 200],       # Green - significance >= 100
    "low": [0, 200, 255, 200],       # Cyan - significance < 100
}


def get_color_from_significance(significance: int) -> list:
    """Get RGBA color based on significance score."""
    if significance >= 600:
        return SEVERITY_COLORS["severe"]
    elif significance >= 400:
        return SEVERITY_COLORS["major"]
    elif significance >= 200:
        return SEVERITY_COLORS["moderate"]
    elif significance >= 100:
        return SEVERITY_COLORS["minor"]
    else:
        return SEVERITY_COLORS["low"]


def get_color(severity: str) -> list:
    """Get RGBA color for severity level (fallback for pre-computed severity)."""
    return SEVERITY_COLORS.get(severity, [128, 128, 128, 200])


# =============================================================================
# Main App
# =============================================================================

def main():
    st.title("🌍 Earthquake Dashboard")
    st.markdown("Visualization of USGS earthquake data")

    # Sidebar filters
    st.sidebar.header("Filters")

    # Time range options with specific intervals
    time_options = {
        "Last Day": 1,
        "Last Week": 7,
        "Last Month": 30
    }

    selected_time = st.sidebar.selectbox(
        "Time Range",
        options=list(time_options.keys()),
        index=2  # Default to "Last Month"
    )
    days = time_options[selected_time]

    # Significance filter (USGS significance score 0-1000+)
    # Higher = more significant (based on magnitude, felt reports, damage, etc.)
    min_sig = st.sidebar.slider(
        "Minimum Significance",
        min_value=0,
        max_value=1000,
        value=100,
        step=50,
        help="USGS significance score (0-1000+). Higher = more impactful events."
    )

    # Load data
    loading_msg = "Loading earthquake data..." if days <= 365 else f"Loading {days // 365}+ years of data (this may take a moment)..."
    with st.spinner(loading_msg):
        try:
            df_events = load_events(min_significance=min_sig, days=days)
            df_daily = load_daily_summary(days=days)
            df_regional = load_regional_summary()
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            st.info("For large date ranges, try reducing the time range or increasing the minimum significance filter.")
            return

    # Key metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Events", f"{len(df_events):,}")

    with col2:
        # High significance events (>500 is typically notable)
        high_sig = len(df_events[df_events["significance"] >= 500])
        st.metric("High Significance (500+)", f"{high_sig:,}")

    with col3:
        if len(df_events) > 0:
            st.metric("Max Significance", f"{df_events['significance'].max():,}")
        else:
            st.metric("Max Significance", "N/A")

    with col4:
        tsunamis = df_events["has_tsunami_warning"].sum() if "has_tsunami_warning" in df_events else 0
        st.metric("Tsunami Warnings", f"{tsunamis:,}")

    st.divider()

    # Map
    st.subheader("🗺️ Earthquake Map (3D)")
    st.caption("Column height represents significance score")

    if len(df_events) > 0:
        # Add color column based on significance score
        df_events["color"] = df_events["significance"].apply(get_color_from_significance)

        # Calculate elevation based on significance (scale for visibility)
        # Significance typically ranges from 0-1000+, scale to reasonable height
        df_events["elevation"] = df_events["significance"] * 100

        # Create 3D column layer
        layer = pdk.Layer(
            "ColumnLayer",
            data=df_events,
            get_position=["longitude", "latitude"],
            get_elevation="elevation",
            elevation_scale=1,
            radius=25000,  # Base radius in meters
            get_fill_color="color",
            pickable=True,
            auto_highlight=True,
            extruded=True,
        )

        # Initial view centered on data with 3D pitch
        view_state = pdk.ViewState(
            latitude=df_events["latitude"].mean(),
            longitude=df_events["longitude"].mean(),
            zoom=2,
            pitch=45,  # Tilted view for 3D effect
            bearing=0,
        )

        # Tooltip
        tooltip = {
            "html": """
                <b>{place}</b><br/>
                Magnitude: {magnitude} ({magnitude_category})<br/>
                Depth: {depth_km} km<br/>
                Significance: {significance}<br/>
                Time: {event_time}
            """,
            "style": {"backgroundColor": "steelblue", "color": "white"}
        }

        # Render map
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view_state,
                tooltip=tooltip,
                map_style="mapbox://styles/mapbox/dark-v10",
            )
        )

        # Legend (based on significance thresholds)
        st.markdown("**Legend:** 🔴 Severe (600+) | 🟠 Major (400+) | 🟡 Moderate (200+) | 🟢 Minor (100+) | 🔵 Low (<100)")
        st.caption("💡 Tip: Click and drag to rotate the map, scroll to zoom")
    else:
        st.warning("No earthquakes found for the selected filters.")

    st.divider()

    # Charts
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("📈 Daily Event Count")
        if len(df_daily) > 0:
            st.line_chart(df_daily.set_index("date")["total_events"])
        else:
            st.info("No daily data available")

    with col_right:
        st.subheader("📊 Top Regions")
        if len(df_regional) > 0:
            chart_data = df_regional.head(10)[["region", "total_events"]].set_index("region")
            st.bar_chart(chart_data)
        else:
            st.info("No regional data available")

    st.divider()

    # Recent events table (filtered by high significance)
    st.subheader("📋 Recent High-Significance Events")

    if len(df_events) > 0:
        # Filter to high significance events (500+)
        recent = df_events[df_events["significance"] >= 500].head(20)
        if len(recent) > 0:
            st.dataframe(
                recent[["event_time", "significance", "magnitude", "place", "depth_km", "alert_level"]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("No high-significance events (500+) in the selected time range.")

    # Footer
    st.divider()
    st.caption(f"Data source: USGS Earthquake API | Catalog: {CATALOG}.{SCHEMA}")


if __name__ == "__main__":
    main()
