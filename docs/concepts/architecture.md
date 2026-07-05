# System Architecture

> **GPTDiff everywhere:** this library also ships for browser and Node as [gptdiff-js](https://github.com/255BITS/gptdiff-js) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Core Components

### **Key Architectural Decisions:**
1. **Clear command line usage** - Easily fit into workflows and agents as a command line program
2. **Contextual Safety** - File isolation during patching
3. **Pluggable LLMs** - Model-agnostic through AI Agent Toolbox
4. **Easy API** - A simple API to use in agent loops

### 1. Prompt Engine

Integrates with AI Agent Toolbox for reliable parsing

```python
# Uses Toolbox for structured output
toolbox = create_toolbox()
toolbox.add_tool("diff", diff_handler)
```

### 2. Diff Generator
- Whole-codebase awareness by default
- Context-aware change proposals
- Git-compatible output format

**Generation Process:**
1. Environment snapshot creation
2. LLM makes diff of environment
3. Diff is applied
4. Validation is run

### 3. SmartApply System

```mermaid
graph LR
    A[Raw Diff] --> B(Per-File Split)
    B --> C{File Exists?}
    C -->|Yes| D[Patch Application]
    C -->|No| E[New File Creation]
    D --> F{Apply Success?}
    F -->|Yes| G[Commit Change]
    F -->|No| H[LLM Reconciliation]
    H --> I[Contextual Merge]
    I --> G
```

**Conflict Resolution Logic:**
- Preserves surrounding code context
- Maintains line ending/encoding consistency
- Atomic file operations

## AI Agent Toolbox Integration

**Key Components:**
- `FlatXMLParser`: Ensures structured diff output
- `Toolbox`: Manages diff generation and validation

### Key Integration Points
1. **Structured Output Parsing**
   ```python
   parser = FlatXMLParser("diff")
   formatter = FlatXMLPromptFormatter(tag="diff")
   ```
2. **Tool Definition**
   ```python
   toolbox.add_tool(name="diff", fn=diff_handler, ...)
   ```
3. **Error-Resistant Parsing** - Handles malformed LLM responses

## Built for Continuous Operation

Every architectural decision in GPTDiff was made with agent loops in mind:

| Component | Single-Use Benefit | Agent Loop Benefit |
|-----------|-------------------|-------------------|
| **Pluggable LLMs** | Use the best model for each task | Switch models between iterations based on complexity |
| **Contextual Safety** | File isolation prevents corruption | Safe to run indefinitely without human supervision |
| **SmartApply Conflict Resolution** | Handles messy diffs gracefully | Patches stay valid as code evolves between cycles |
| **Easy API** | Simple integration | Trivial to wrap in `while true` for autonomous improvement |

The same properties that make GPTDiff reliable for one-off patches make it *powerful* when run continuously. See [Agent Loops](../examples/automation.md) for battle-tested automation patterns.
