from glob import glob
from setuptools import find_packages, setup

package_name = "aerodrop_core"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="AeroDrop",
    maintainer_email="maintainer@example.com",
    description="PX4 offboard delivery mission execution and telemetry bridge",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "mission_executor = aerodrop_core.mission_executor:main",
            "telemetry_bridge = aerodrop_core.telemetry_bridge:main",
        ],
    },
)
