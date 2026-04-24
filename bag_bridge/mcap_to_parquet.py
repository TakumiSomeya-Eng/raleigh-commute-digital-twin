"""Convert /fused/odom from an MCAP bag back to Parquet for Python evaluation.

Language boundary: C++ fusion code only reads MCAP; Python eval code only reads Parquet.
This bridge is the one-way crossing from the ROS side to the Python side.

Implemented in task T2.8.
"""
