#!/usr/bin/env python

import os

from setuptools import setup

# Read the contents of README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

# Read requirements
with open(os.path.join(this_directory, "requirements.txt"), encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="llms-py",
    version="3.0.25",
    author="ServiceStack",
    author_email="team@servicestack.net",
    description="A lightweight CLI tool and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ServiceStack/llms",
    project_urls={
        "Bug Reports": "https://github.com/ServiceStack/llms/issues",
        "Source": "https://github.com/ServiceStack/llms",
        "Documentation": "https://github.com/ServiceStack/llms#readme",
    },
    packages=["llms"],
    package_data={
        "llms": [
            "index.html",
            "llms.json",
            "providers.json",
            "providers-extra.json",
            "ui/*",
            "ui/modules/*",
            "ui/lib/*",
        ]
    },
    install_requires=requirements,
    python_requires=">=3.7",
    entry_points={
        "console_scripts": [
            "llms=llms.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
        "Environment :: Console",
    ],
    keywords="llm ai openai anthropic google gemini groq mistral ollama cli server chat completion",
    include_package_data=True,
    zip_safe=False,
)
