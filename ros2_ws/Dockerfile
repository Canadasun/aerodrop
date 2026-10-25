FROM osrf/ros:humble-desktop
RUN apt-get update && apt-get install -y --no-install-recommends git python3-pip && rm -rf /var/lib/apt/lists/*
COPY ros2_ws/src/px4_msgs /opt/px4_msgs
RUN python3 -m pip install --user --no-cache-dir "websockets>=12,<16"
WORKDIR /ros2_ws
