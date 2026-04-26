"""ROS 2 message schema strings for MCAP channel registration.

Encoding: ros2msg (concatenated .msg format with embedded sub-messages).
Used by parquet_to_mcap.py to register schemas before writing messages.
"""

from __future__ import annotations

_TIME = """\
int32 sec
uint32 nanosec"""

_HEADER = f"""\
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
{_TIME}"""

NAVSATFIX_SCHEMA: str = f"""\
std_msgs/Header header
sensor_msgs/NavSatStatus status
float64 latitude
float64 longitude
float64 altitude
float64[9] position_covariance
uint8 COVARIANCE_TYPE_UNKNOWN=0
uint8 COVARIANCE_TYPE_APPROXIMATED=1
uint8 COVARIANCE_TYPE_DIAGONAL_KNOWN=2
uint8 COVARIANCE_TYPE_KNOWN=3
uint8 position_covariance_type
================================================================================
MSG: std_msgs/Header
{_HEADER}
================================================================================
MSG: sensor_msgs/NavSatStatus
int8 STATUS_NO_FIX=-1
int8 STATUS_FIX=0
int8 STATUS_SBAS_FIX=1
int8 STATUS_GBAS_FIX=2
uint16 SERVICE_GPS=1
uint16 SERVICE_GLONASS=2
uint16 SERVICE_COMPASS=4
uint16 SERVICE_GALILEO=8
int8 status
uint16 service"""

IMU_SCHEMA: str = f"""\
std_msgs/Header header
geometry_msgs/Quaternion orientation
float64[9] orientation_covariance
geometry_msgs/Vector3 angular_velocity
float64[9] angular_velocity_covariance
geometry_msgs/Vector3 linear_acceleration
float64[9] linear_acceleration_covariance
================================================================================
MSG: std_msgs/Header
{_HEADER}
================================================================================
MSG: geometry_msgs/Quaternion
float64 x
float64 y
float64 z
float64 w
================================================================================
MSG: geometry_msgs/Vector3
float64 x
float64 y
float64 z"""

MAGNETIC_FIELD_SCHEMA: str = f"""\
std_msgs/Header header
geometry_msgs/Vector3 magnetic_field
float64[9] magnetic_field_covariance
================================================================================
MSG: std_msgs/Header
{_HEADER}
================================================================================
MSG: geometry_msgs/Vector3
float64 x
float64 y
float64 z"""

_VECTOR3 = """\
float64 x
float64 y
float64 z"""

_QUATERNION = """\
float64 x
float64 y
float64 z
float64 w"""

ODOMETRY_SCHEMA: str = f"""\
std_msgs/Header header
string child_frame_id
geometry_msgs/PoseWithCovariance pose
geometry_msgs/TwistWithCovariance twist
================================================================================
MSG: std_msgs/Header
{_HEADER}
================================================================================
MSG: geometry_msgs/PoseWithCovariance
geometry_msgs/Pose pose
float64[36] covariance
================================================================================
MSG: geometry_msgs/Pose
geometry_msgs/Point position
geometry_msgs/Quaternion orientation
================================================================================
MSG: geometry_msgs/Point
{_VECTOR3}
================================================================================
MSG: geometry_msgs/Quaternion
{_QUATERNION}
================================================================================
MSG: geometry_msgs/TwistWithCovariance
geometry_msgs/Twist twist
float64[36] covariance
================================================================================
MSG: geometry_msgs/Twist
geometry_msgs/Vector3 linear
geometry_msgs/Vector3 angular
================================================================================
MSG: geometry_msgs/Vector3
{_VECTOR3}"""
