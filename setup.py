from setuptools import setup, find_packages

setup(
    name='gptdiff',
    version='0.8.0',
    description='Natural-language code transformation as a library: generate unified diffs with LLMs and apply them resiliently with smartapply',
    author='255labs',
    url='https://github.com/255BITS/gptdiff',
    project_urls={
        'Documentation': 'https://gptdiff.255labs.xyz',
        'Source': 'https://github.com/255BITS/gptdiff',
        'Browser/Node port (gptdiff-js)': 'https://github.com/255BITS/gptdiff-js',
        'Built with gptdiff-js (nanoodle)': 'https://nanoodle.com',
    },
    keywords=['llm', 'diff', 'unified-diff', 'patch', 'smartapply', 'code-transformation', 'ai', 'codegen'],
    packages=find_packages(),  # Use find_packages() to automatically discover packages
    package_data={'gptdiff': []},  # Add any package data if needed
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    install_requires=[
        'openai>=1.0.0',
        'tiktoken>=0.5.0',
        'ai-agent-toolbox>=0.2.0,<1.0'
    ],
    extras_require={
        'test': ['pytest', 'pytest-mock'],
        'docs': ['mkdocs', 'mkdocs-material']
    },
    entry_points={
        'console_scripts': [
            'gptdiff=gptdiff.gptdiff:main',
            'gptpatch=gptdiff.gptpatch:main',
        ],
    },
    license=None, # Remove license argument
    # license_file='LICENSE.txt', # Remove license_file argument
    classifiers=[  # Add license classifiers
        'License :: OSI Approved :: MIT License', # Standard MIT license classifier
        'Programming Language :: Python :: 3',
        'Operating System :: OS Independent',
    ],
)
