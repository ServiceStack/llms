#!/usr/bin/env python

from setuptools import setup, find_packages
import os

# Read the contents of README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

# Read requirements
with open(os.path.join(this_directory, "requirements.txt"), encoding="utf-8") as f:
    requirements = [
        line.strip() for line in f if line.strip() and not line.startswith("#")
    ]

setup(
    name="llms-py",
    version="2.0.5",
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
    py_modules=["llms"],
    install_requires=requirements,
    python_requires=">=3.7",
    entry_points={
        "console_scripts": [
            "llms=llms:main",
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
    data_files=[
        ("", ["index.html", "ui.json", "llms.json", "requirements.txt"]),
        (
            "ui",
            [
                "ui/App.mjs",
                "ui/ChatPrompt.mjs",
                "ui/Main.mjs",
                "ui/Recents.mjs",
                "ui/Sidebar.mjs",
                "ui/app.css",
                "ui/fav.svg",
                "ui/markdown.mjs",
                "ui/tailwind.input.css",
                "ui/threadStore.mjs",
                "ui/typography.css",
                "ui/utils.mjs",
            ],
        ),
        (
            "ui/lib",
            [
                "ui/lib/highlight.min.mjs",
                "ui/lib/idb.min.mjs",
                "ui/lib/marked.min.mjs",
                "ui/lib/servicestack-client.min.mjs",
                "ui/lib/servicestack-vue.min.mjs",
                "ui/lib/vue-router.min.mjs",
                "ui/lib/vue.min.mjs",
            ],
        ),
    ],
    zip_safe=False,
)
