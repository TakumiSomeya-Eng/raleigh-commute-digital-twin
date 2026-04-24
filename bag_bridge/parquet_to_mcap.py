"""FR-3.1, FR-3.2 — Convert aligned_100hz.parquet to a ROS 2 MCAP bag.

Publishes three topics: /gps/fix (NavSatFix), /imu/data (Imu), /mag (MagneticField).
Emits a sidecar trip.metadata.yaml with SHA-256 checksum and message counts.

Implemented in task T2.1.
"""
