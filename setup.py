from setuptools import setup, find_packages

setup(
    name="swing-trader",
    version="2.0.0",
    description="Multi-strategy AI swing trading system — Twelve Data + DeepSeek V4 Flash",
    packages=find_packages(),
    install_requires=["numpy>=1.24"],
    entry_points={
        "console_scripts": [
            "swing-trader=swing_trader.cli:main",
        ],
    },
    python_requires=">=3.10",
)