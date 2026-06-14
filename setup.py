"""Backward-compatible setup.py — prefer pyproject.toml for modern builds."""
from setuptools import setup, find_packages

setup(
    name="deepforge",
    version="0.1.0",
    description="CodeWhale Architecture in Python with DeepSeek API",
    packages=find_packages(include=["deepforge", "deepforge.*"]),
    py_modules=["cli", "tui"],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "deepforge = tui:main",
            "deepforge-cli = cli:main",
        ],
    },
    install_requires=[
        "openai>=1.0.0",
        "tiktoken>=0.7.0",
        "httpx>=0.27.0",
        "rich>=13.0.0",
        "pydantic>=2.0.0",
        "gitpython>=3.1.0",
        "pyyaml>=6.0",
        "mcp>=1.27,<2",
    ],
    extras_require={
        "dev": ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.3"],
        "browser": ["playwright>=1.45.0"],
    },
)
