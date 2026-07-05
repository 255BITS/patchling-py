from setuptools import setup, find_packages

setup(
    name='patchling',
    version='0.8.0',
    description='Natural-language code transformation as a library: generate unified diffs with LLMs and apply them resiliently with smartapply. Formerly gptdiff.',
    author='255labs',
    url='https://github.com/255BITS/patchling-py',
    project_urls={
        'Documentation': 'https://255bits.github.io/patchling-py',
        'Source': 'https://github.com/255BITS/patchling-py',
        'Browser/Node runtime (npm)': 'https://www.npmjs.com/package/patchling',
        'Built with patchling (nanoodle)': 'https://nanoodle.com',
    },
    keywords=['patchling', 'smartapply', 'llm', 'diff', 'unified-diff', 'patch', 'code-transformation', 'ai', 'codegen', 'gptdiff'],
    packages=find_packages(),  # Use find_packages() to automatically discover packages
    package_data={'patchling': []},  # Add any package data if needed
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
            # New, on-brand commands
            'patchling=patchling.core:main',
            'patchling-apply=patchling.gptpatch:main',
            # Back-compat aliases (former names — keep existing scripts working)
            'gptdiff=patchling.core:main',
            'gptpatch=patchling.gptpatch:main',
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
