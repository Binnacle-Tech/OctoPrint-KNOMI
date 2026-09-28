from setuptools import setup

setup(
    name="OctoPrint-KNOMI",
    version="0.4.1",
    description="Companion plugin for the KNOMI OctoPrint firmware: status flags, websocket push and a Bluetooth LE link",
    author="Binnacle-Tech",
    url="https://github.com/Binnacle-Tech/OctoPrint-KNOMI",
    license="AGPLv3",
    packages=["octoprint_knomi"],
    python_requires=">=3.8,<4",
    install_requires=["OctoPrint", "bleak>=0.21"],
    include_package_data=True,
    package_data={"octoprint_knomi": ["templates/*.jinja2", "static/js/*.js"]},
    entry_points={"octoprint.plugin": ["knomi = octoprint_knomi"]},
)
