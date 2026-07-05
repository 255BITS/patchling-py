#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import urlparse
import subprocess
import hashlib
import re
import time
import os
import json
import sys
import fnmatch
import argparse
import pkgutil
import contextvars
from pkgutil import get_data
import threading
from threading import Lock
import shutil
import base64
import mimetypes

import openai
from openai import OpenAI
import tiktoken
import requests
from ai_agent_toolbox import Toolbox, MarkdownParser, MarkdownPromptFormatter, XMLParser, XMLPromptFormatter
from .applydiff import apply_diff, parse_diff_per_file

VERBOSE = False
diff_context = contextvars.ContextVar('diffcontent', default=[])

def create_diff_toolbox():
    toolbox = Toolbox()
    diff_context.set([])
    
    def diff(content: str):
        diff_context.set(diff_context.get()+[content])
        return content

    toolbox.add_tool(
        name="diff",
        fn=diff,
        args={
            "content": {
                "type": "string",
                "description": "Complete diff."
            }
        },
        description="""Save the calculated diff as used in 'git apply'. Should include the file and line number. For example:
a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,2 +1,2 @@
-def old():
+def new():

-
You must include the '--- file' and/or '+++ file' part of the diff. File modifications should include both.
"""
    )
    return toolbox

def create_think_toolbox():
    toolbox = Toolbox()
    
    def think(content: str):
        print("Swallowed thoughts", content)

    toolbox.add_tool(
        name="think",
        fn=think,
        args={
            "content": {
                "type": "string"
            }
        },
        description="Just output the diff content. Not a datastructure."
    )
    return toolbox

def color_code_diff(diff_text: str) -> str:
    """
    Color code lines in a diff. Lines beginning with '-' in red, and
    lines beginning with '+' in green.
    """
    red = "\033[31m"
    green = "\033[32m"
    reset = "\033[0m"

    colorized_lines = []
    for line in diff_text.split('\n'):
        if line.startswith('-'):
            colorized_lines.append(f"{red}{line}{reset}")
        elif line.startswith('+'):
            colorized_lines.append(f"{green}{line}{reset}")
        else:
            colorized_lines.append(line)

    return '\n'.join(colorized_lines)

def load_gitignore_patterns(gitignore_path):
    with open(gitignore_path, 'r') as f:
        patterns = [
            line.strip() for line in f if line.strip() and not line.startswith('#')
        ]
    return patterns

def is_ignored(filepath, gitignore_patterns):
    filepath = Path(filepath).resolve()
    ignored = False

    for pattern in gitignore_patterns:
        if pattern.startswith('!'):
            negated_pattern = pattern[1:]
            if fnmatch.fnmatch(str(filepath), negated_pattern) or fnmatch.fnmatch(str(filepath.relative_to(Path.cwd())), negated_pattern):
                ignored = False
        else:
            relative_path = str(filepath.relative_to(Path.cwd()))
            if fnmatch.fnmatch(str(filepath), pattern) or fnmatch.fnmatch(relative_path, pattern):
                ignored = True
                break
            if pattern in relative_path:
                ignored = True
                break

    # Ensure .gitignore itself is not ignored unless explicitly mentioned
    if filepath.name == ".gitignore" and not any(pattern == ".gitignore" for pattern in gitignore_patterns):
        ignored = False

    return ignored

def list_files_and_dirs(path, ignore_list=None):
    if ignore_list is None:
        ignore_list = []

    result = []

    # List all items in the current directory
    for item in os.listdir(path):
        item_path = os.path.join(path, item)

        if is_ignored(item_path, ignore_list):
            continue

        # Add the item to the result list
        result.append(item_path)

        # If it's a directory, recurse into it
        if os.path.isdir(item_path):
            result.extend(list_files_and_dirs(item_path, ignore_list))

    return result

# Function to load project files considering .gitignore
def load_project_files(project_dir, cwd): 
    """Load project files while respecting .gitignore and .gptignore rules.
    
    Recursively scans directories, skipping:
    - Files/directories matching patterns in .gitignore/.gptignore
    - Binary files that can't be decoded as UTF-8 text
    
    Args:
        project_dir: Root directory to scan for files
        cwd: Base directory for resolving ignore files
    
    Returns:
        List of (absolute_path, file_content) tuples
    
    Note:
        Prints skipped files to stdout for visibility
    """
    ignore_paths = [Path(cwd) / ".gitignore", Path(cwd) / ".gptignore"]
    gitignore_patterns = [".gitignore", "diff.patch", "prompt.txt", ".*", ".gptignore", "*.pdf", "*.docx", ".git", "*.orig", "*.rej", "*.diff"]

    for p in ignore_paths:
        if p.exists():
            with open(p, 'r') as f:
                gitignore_patterns.extend([line.strip() for line in f if line.strip() and not line.startswith('#')])

    project_files = []
    for file in list_files_and_dirs(project_dir, gitignore_patterns):
        if os.path.isfile(file):
            try:
                with open(file, 'r') as f:
                    content = f.read()
                if VERBOSE:
                    print(file)
                project_files.append((file, content))
            except UnicodeDecodeError:
                print(f"Skipping file {file} due to UnicodeDecodeError")
                continue

    print("")
    return project_files

def load_prepend_file(file):
    with open(file, 'r') as f:
        return f.read()

def domain_for_url(base_url):
    parsed = urlparse(base_url)
    if parsed.netloc:
        if parsed.username:
            domain = parsed.hostname
            if parsed.port:
                domain += f":{parsed.port}"
        else:
            domain = parsed.netloc
    else:
        domain = base_url
    return domain

def _convert_openai_message_to_anthropic(message):
    if not isinstance(message.get("content"), list):
        return message

    converted_content = []
    for block in message["content"]:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            converted_content.append({"type": "text", "text": block.get("text", "")})
        elif block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                media_type = header.split(":", 1)[1].split(";", 1)[0]
                converted_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                })
        else:
            converted_content.append(block)

    return {**message, "content": converted_content}


def call_llm(api_key, base_url, model, messages, max_tokens, temperature, budget_tokens=None):
    # Check if we're using Anthropic
    if "api.anthropic.com" in base_url:
        anthropic_url = "https://api.anthropic.com/v1/messages"
        
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        # Extract system message if present
        system_message = None
        filtered_messages = []
        
        for message in messages:
            if message["role"] == "system":
                system_message = message["content"]
            else:
                filtered_messages.append(message)
        
        # Prepare request data
        filtered_messages = [_convert_openai_message_to_anthropic(m) for m in filtered_messages]
        data = {
            "model": model,
            "messages": filtered_messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        # Add system message as top-level parameter if found
        if system_message:
            data["system"] = system_message

        if budget_tokens:
            data["temperature"] = 1
            data["thinking"] = {"budget_tokens": budget_tokens, "type": "enabled"}
        
        # Make the API call
        response = requests.post(anthropic_url, headers=headers, json=data)
        response_data = response.json()
        
        if 'error' in response_data:
            print(f"Error from Anthropic API: {response_data}")
            return response_data
        
        # Format response to match OpenAI structure for compatibility
        class OpenAICompatResponse:
            class Choice:
                class Message:
                    def __init__(self, content):
                        self.content = content
                
                def __init__(self, message):
                    self.message = message
            
            class Usage:
                def __init__(self, prompt_tokens, completion_tokens, total_tokens):
                    self.prompt_tokens = prompt_tokens
                    self.completion_tokens = completion_tokens
                    self.total_tokens = total_tokens
            
            def __init__(self, choices, usage):
                self.choices = choices
                self.usage = usage
        
        # Get content from the response
        thinking_items = [item["thinking"] for item in response_data["content"] if item["type"] == "thinking"]
        text_items = [item["text"] for item in response_data["content"] if item["type"] == "text"]
        if not text_items:
            raise ValueError("No 'text' type found in response content")
        text_content = text_items[0]
        if thinking_items:
            wrapped_thinking = f"<think>{thinking_items[0]}</think>"
            message_content = wrapped_thinking + "\n" + text_content
        else:
            message_content = text_content
        
        # Extract token usage information
        input_tokens = response_data["usage"]["input_tokens"]
        output_tokens = response_data["usage"]["output_tokens"]
        total_tokens = input_tokens + output_tokens
        
        # Create the response object with usage information
        message = OpenAICompatResponse.Choice.Message(message_content)
        choice = OpenAICompatResponse.Choice(message)
        usage = OpenAICompatResponse.Usage(input_tokens, output_tokens, total_tokens)
        
        return OpenAICompatResponse([choice], usage)
    else:
        # Use OpenAI client as before
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )

def call_llm_for_diff(system_prompt, user_prompt, files_content, model, temperature=1.0, max_tokens=30000, api_key=None, base_url=None, budget_tokens=None, images=None):
    enc = tiktoken.get_encoding("o200k_base")
    
    # Use colors in print statements
    red = "\033[91m"
    green = "\033[92m"
    blue = "\033[94m"
    reset = "\033[0m"
    start_time = time.time()

    parser = MarkdownParser()
    formatter = MarkdownPromptFormatter()
    toolbox = create_diff_toolbox()
    #tool_prompt = formatter.usage_prompt(toolbox)
    tool_prompt="""Save the calculated diff as used in 'git apply'. Should include the file and line number. For example:
```diff
a/file.py b/file.py
--- a/file.py
+++ b/file.py
@@ -1,2 +1,2 @@
-def old():
+def new():
```

You must include the '--- file' and/or '+++ file' part of the diff. File modifications should include both.
"""
    system_prompt += "\n" + tool_prompt

    if 'gemini' in model:
        user_prompt = system_prompt + "\n" + user_prompt

    input_content = system_prompt + "\n" + user_prompt + "\n" + files_content
    token_count = len(enc.encode(input_content))
    user_content = user_prompt + "\n" + files_content
    if images:
        content_blocks = [{"type": "text", "text": user_content}]
        for image in images:
            data_url = f"data:{image['media_type']};base64,{image['data']}"
            content_blocks.append({"type": "image_url", "image_url": {"url": data_url}})
        user_content = content_blocks

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    if VERBOSE:
        print(f"{green}Using {model}{reset}")
        print(f"{green}SYSTEM PROMPT{reset}")
        print(system_prompt)
        print(f"{green}USER PROMPT{reset}")
        print(user_prompt, "+", len(enc.encode(files_content)), "tokens of file content")
    else:
        print(f"Generating diff using model '{green}{model}{reset}' from '{blue}{domain_for_url(base_url)}{reset}' with {token_count} input tokens...")

    if not api_key:
        api_key = os.getenv('GPTDIFF_LLM_API_KEY')
    if not base_url:
        base_url = os.getenv('GPTDIFF_LLM_BASE_URL', "https://nano-gpt.com/api/v1/")
    base_url = base_url or "https://nano-gpt.com/api/v1/"

    response = call_llm(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        budget_tokens=budget_tokens,
        temperature=temperature
    )
    if VERBOSE:
        print("Debug: Raw LLM Response\n---")
        print(response.choices[0].message.content.strip())
        print("---")
    else:
        print("Diff generated.")

    # Robust token usage handling
    prompt_tokens = completion_tokens = total_tokens = 0
    usage = getattr(response, "usage", None)

    if usage:
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        else:
            prompt_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)
            total_tokens = getattr(usage, "total_tokens", None) or (prompt_tokens + completion_tokens)
    elif hasattr(response, "x_nanogpt_pricing"):
        pricing = getattr(response, "x_nanogpt_pricing") or {}
        prompt_tokens = pricing.get("inputTokens") or pricing.get("cacheCreationInputTokens") or 0
        completion_tokens = pricing.get("outputTokens", 0)
        total_tokens = prompt_tokens + completion_tokens


    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    print(f"Diff creation time: {time_str}")

    # Now, these rates are updated to per million tokens

    full_response = response.choices[0].message.content.strip()
    full_response, reasoning = swallow_reasoning(full_response)
    if reasoning and len(reasoning) > 0:
        print("Swallowed reasoning", reasoning)

    events = parser.parse(full_response)
    for event in events:
        toolbox.use(event)
    diff_response = diff_context.get()

    return full_response, "\n".join(diff_response), prompt_tokens, completion_tokens, total_tokens

# New API functions
def build_environment(files_dict):
    """Rebuild environment string from file dictionary"""
    env = []
    for path, content in files_dict.items():
        env.append(f"File: {path}")
        env.append("Content:")
        env.append(content)
    return '\n'.join(env)

def load_images(image_paths):
    images = []
    for image_path in image_paths or []:
        media_type, _ = mimetypes.guess_type(image_path)
        media_type = media_type or "image/png"
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        images.append({"media_type": media_type, "data": encoded, "path": image_path})
    return images

def generate_diff(environment, goal, model=None, temperature=1.0, max_tokens=32000, api_key=None, base_url=None, prepend=None, anthropic_budget_tokens=None, images=None):
    """API: Generate a git diff from the environment and goal.

If 'prepend' is provided, it should be a path to a file whose content will be
prepended to the system prompt.
    """
    if model is None:
        model = os.getenv('GPTDIFF_MODEL', 'deepseek-reasoner')
        # Use ANTHROPIC_BUDGET_TOKENS env var if set and no cli override provided
        if anthropic_budget_tokens is None:
            anthropic_budget_tokens = os.getenv('ANTHROPIC_BUDGET_TOKENS')
    
    if prepend:
        if prepend.startswith("http://") or prepend.startswith("https://"):
            import urllib.request
            with urllib.request.urlopen(prepend) as response:
                prepend = response.read().decode('utf-8')
        else:
            prepend = load_prepend_file(prepend)+"\n"
    else:
        prepend = ""
    
    diff_tag = "```diff"
    system_prompt = prepend + f"Output a full unified git diff into a \"{diff_tag}\" block."
    encoded_images = load_images(images)

    _, diff_text, _, _, _ = call_llm_for_diff(
        system_prompt,
        goal,
        environment,
        model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=base_url,
        budget_tokens=int(anthropic_budget_tokens) if anthropic_budget_tokens is not None else None,
        images=encoded_images,
    )
    return diff_text

def smartapply(diff_text, files, model=None, api_key=None, base_url=None):
    """Applies unified diffs to file contents with AI-powered conflict resolution.
    
    Key features:
    - Handles file creations, modifications, and deletions
    - Maintains idempotency - reapplying same diff produces same result
    - Uses LLM to resolve ambiguous changes while preserving context
    - Returns new files dictionary without modifying input

    Args:
        diff_text: Unified diff string compatible with git apply
        files: Dictionary of {file_path: content} to modify
        model: LLM to use for conflict resolution (default: deepseek-reasoner)
        api_key: Optional API key override
        base_url: Optional API base URL override

    Returns:
        New dictionary with updated file contents. Deleted files are omitted.

    Raises:
        APIError: If LLM API calls fail

    Example:
        >>> original = {"file.py": "def old():\n    pass"}
        >>> diff = '''diff --git a/file.py b/file.py
        ... --- a/file.py
        ... +++ b/file.py
        ... @@ -1,2 +1,2 @@
        ... -def old():
        ... +def new():'''
        >>> updated = smartapply(diff, original)
        >>> print(updated["file.py"])
        def new():
            pass
    """
    if model is None:
        model = os.getenv('GPTDIFF_MODEL', 'deepseek-reasoner')
    parsed_diffs = parse_diff_per_file(diff_text)    
    print("-" * 40)
    print("SMARTAPPLY")
    print(diff_text)
    print("-" * 40)

    def process_file(path, patch):
        original = files.get(path, '')
        # Handle file deletions
        if '+++ /dev/null' in patch:
            if path in files:
                del files[path]
        else:
            updated = call_llm_for_apply_with_think_tool_available(path, original, patch, model, api_key=api_key, base_url=base_url)
            cleaned = strip_bad_output(updated, original)
            files[path] = cleaned

    threads = []

    for path, patch in parsed_diffs:
        thread = threading.Thread(target=process_file, args=(path, patch))
        thread.start()
        threads.append(thread)

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    return files

def parse_arguments():
    parser = argparse.ArgumentParser(description='Generate and optionally apply git diffs using GPT-4.')
    parser.add_argument('prompt', type=str, help='Prompt that runs on the codebase.')
    parser.add_argument('--apply', action='store_true', help='Attempt to apply the generated git diff. Uses smartapply if applying the patch fails.')
    parser.add_argument('--prepend', type=str, default=None, help='Path to content prepended to system prompt')

    parser.add_argument('--nobeep', action='store_false', dest='beep', default=True, help='Disable completion notification beep')
    # New flag --prompt that does not call the API but instead writes the full prompt to prompt.txt
    parser.add_argument('--call', action='store_true',
                        help='Call the GPT-4 API. Writes the full prompt to prompt.txt if not specified.')
    parser.add_argument('files', nargs='*', default=[], help='Specify additional files or directories to include.')
    parser.add_argument('--temperature', type=float, default=1.0, help='Temperature parameter for model creativity (0.0 to 2.0)')
    parser.add_argument('--max_tokens', type=int, default=30000, help='Temperature parameter for model creativity (0.0 to 2.0)')
    parser.add_argument('--model', type=str, default=None, help='Model to use for the API call.')
    parser.add_argument(
        '--applymodel', type=str, default=None,
        help='Model to use for applying the diff. Overrides GPTDIFF_SMARTAPPLY_MODEL env var; if not set, defaults to "openai/gpt-4.1-mini".')

    parser.add_argument('--image', action='append', default=[], help='Path to an image file to include in the request. Can be provided multiple times.')

    parser.add_argument('--nowarn', action='store_true', help='Disable large token warning')
    parser.add_argument('--anthropic_budget_tokens', type=int, default=None, help='Budget tokens for Anthropic extended thinking')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output with detailed information')
    return parser.parse_args()

def absolute_to_relative(absolute_path):
    cwd = os.getcwd()
    relative_path = os.path.relpath(absolute_path, cwd)
    return relative_path

def colorize_warning_warning(message):
    return f"\033[91m\033[1m{message}\033[0m"

def call_llm_for_apply_with_think_tool_available(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=30000):
    parser = XMLParser("think")
    formatter = XMLPromptFormatter(tag="think")
    toolbox = create_think_toolbox()
    full_response = call_llm_for_apply(file_path, original_content, file_diff, model, api_key=api_key, base_url=base_url, extra_prompt=extra_prompt, max_tokens=max_tokens)
    full_response, reasoning = swallow_reasoning(full_response)
    if reasoning and len(reasoning) > 0:
        print("Swallowed reasoning", reasoning)
    notool_response = ""
    events = parser.parse(full_response)
    is_in_tool = False
    appended_content = ""
    for event in events:
        if event.mode == 'append':
            appended_content += event.content
        if event.mode == 'close' and appended_content and event.tool is None:
            notool_response += appended_content
        if event.mode == 'close':
            appended_content = ""
        toolbox.use(event)

    return notool_response

def call_llm_for_apply(file_path, original_content, file_diff, model, api_key=None, base_url=None, extra_prompt=None, max_tokens=30000):
    """AI-powered diff application with conflict resolution.
    
    Internal workhorse for smartapply that handles individual file patches.
    Uses LLM to reconcile diffs while preserving code structure and context.

    Args:
        file_path: Target file path (used for context/error messages)
        original_content: Current file content as string
        file_diff: Unified diff snippet to apply
        model: LLM identifier for processing
        api_key: Optional override for LLM API credentials
        base_url: Optional override for LLM API endpoint

    Returns:
        Updated file content as string with diff applied

    Raises:
        APIError: If LLM processing fails

    Example:
        >>> updated = call_llm_for_apply(
        ...     file_path='utils.py',
        ...     original_content='def old(): pass',
        ...     file_diff='''@@ -1 +1 @@
        ...                  -def old()
        ...                  +def new()''',
        ...     model='deepseek-reasoner'
        ... )
        >>> print(updated)
        def new(): pass"""
    system_prompt = """Please apply the diff to this file. Return the result in a block. Write the entire file.

1. Carefully apply all changes from the diff
2. Preserve surrounding context that isn't changed
3. Only return the final file content, do not add any additional markup and do not add a code block
4. You must return the entire file. It overwrites the existing file."""
    user_prompt = f"""File: {file_path}
File contents:
```
{original_content}
```

Diff to apply:
```diff
{file_diff}
```"""
    if extra_prompt:
        user_prompt += f"\n\n{extra_prompt}"
    if 'gemini' in model:
        user_prompt = system_prompt+"\n"+user_prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if not api_key:
        api_key = os.getenv('GPTDIFF_LLM_API_KEY')
    if not base_url:
        base_url = os.getenv('GPTDIFF_LLM_BASE_URL', "https://nano-gpt.com/api/v1/")
    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    start_time = time.time()
    response = client.chat.completions.create(model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=max_tokens)
    full_response = response.choices[0].message.content
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    if VERBOSE:
        print(f"Smartapply time: {time_str}")
        print("-" * 40)
    else:
        print(f"Smartapply completed in {time_str}")
    return full_response

def build_environment_from_filelist(file_list, cwd):
    """Build environment string from list of file paths"""
    files_dict = {}
    for file_path in file_list:
        relative_path = os.path.relpath(file_path, cwd)
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            files_dict[relative_path] = content
        except UnicodeDecodeError:
            print(f"Skipping file {file_path} due to UnicodeDecodeError")
            continue
        except IOError as e:
            print(f"Error reading {file_path}: {e}")
            continue
    return build_environment(files_dict)

def smart_apply_patch(project_dir, diff_text, user_prompt, args):
    """
    Attempt to apply a diff via smartapply: process each file concurrently using the LLM.
    """
    from pathlib import Path
    start_time = time.time()
    parsed_diffs = parse_diff_per_file(diff_text)
    print("Found", len(parsed_diffs), "files in diff, processing smart apply concurrently:")
    green = "\033[92m"
    red = "\033[91m"
    blue = "\033[94m"
    reset = "\033[0m"

    if len(parsed_diffs) == 0:
        print(colorize_warning_warning("There were no entries in this diff. The LLM may have returned something invalid."))
        if args.beep:
            print("\a")
        return
    threads = []
    success_files = []
    failed_files = []
    success_lock = Lock()

    def process_file(file_path, file_diff):
        full_path = Path(project_dir) / file_path
        if VERBOSE:
            print(f"Processing file: {file_path}")
        if '+++ /dev/null' in file_diff:
            if full_path.exists():
                full_path.unlink()
                print(f"\033[1;32mDeleted file {file_path}.\033[0m")
            else:
                print(colorize_warning_warning(f"File {file_path} not found - skipping deletion"))
            return

        original_content = ""
        if full_path.exists():
            try:
                original_content = full_path.read_text()
            except (UnicodeDecodeError, IOError) as e:
                print(f"Cannot read {file_path} due to {str(e)}, treating as new file")
        else:
            print(f"File {file_path} does not exist, treating as new file")

        # Use SMARTAPPLY-specific environment variables if set, otherwise fallback.
        # Determine model for smartapply: CLI flag > environment > recommended default
        if hasattr(args, "applymodel") and args.applymodel:
            model = args.applymodel
        else:
            smart_apply_model = os.getenv("GPTDIFF_SMARTAPPLY_MODEL", "").strip()
            if smart_apply_model:
                model = smart_apply_model
            else:
                model = 'openai/gpt-4.1-mini'

        smart_api_key = os.getenv("GPTDIFF_SMARTAPPLY_API_KEY")
        if smart_api_key and smart_api_key.strip():
            api_key = smart_api_key
        else:
            api_key = os.getenv("GPTDIFF_LLM_API_KEY")

        smart_base_url = os.getenv("GPTDIFF_SMARTAPPLY_BASE_URL")
        if smart_base_url and smart_base_url.strip():
            base_url = smart_base_url
        else:
            base_url = os.getenv("GPTDIFF_LLM_BASE_URL", "https://nano-gpt.com/api/v1/")

        print(f"Running smartapply in parallel for '{file_path}' using model '{green}{model}{reset}' from '{blue}{domain_for_url(base_url)}{reset}'...")
        try:
            updated_content = call_llm_for_apply_with_think_tool_available(
                file_path, original_content, file_diff, model,
                api_key=api_key, base_url=base_url,
                extra_prompt=f"This changeset is from the following instructions:\n{user_prompt}",
                max_tokens=args.max_tokens)
            if updated_content.strip() == "":
                print("Cowardly refusing to write empty file to", file_path, "merge failed")
                with success_lock:
                    failed_files.append(file_path)
                return
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if updated_content and not updated_content.endswith("\n"):
                updated_content += "\n"
            full_path.write_text(updated_content)
            print(f"\033[1;32mSuccessful 'smartapply' update {file_path}.\033[0m")
            with success_lock:
                success_files.append(file_path)
        except Exception as e:
            print(f"\033[1;31mFailed to process {file_path}: {str(e)}\033[0m")
            with success_lock:
                failed_files.append(file_path)

    for file_path, file_diff in parsed_diffs:
        thread = threading.Thread(target=process_file, args=(file_path, file_diff))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    print(f"Smartapply successfully applied changes in {time_str}. Check the updated files to confirm.")
    if failed_files:
        print(f"\033[1;31mSmart apply completed in {time_str} with failures for {len(failed_files)} files:\033[0m")
        for file in failed_files:
            print(f"  - {file}")
        print("Please check the errors above for details.")
    else:
        print(f"\033[1;32mSmart apply completed successfully in {time_str} for all {len(success_files)} files.\033[0m")
    if args.beep:
        print("\a")

def save_files(files_dict, target_directory):
    """
    Save files from a dictionary mapping relative file paths to file contents
    into the specified target directory.

    Args:
        files_dict (dict): A dictionary where keys are file paths (relative)
                           and values are the corresponding file contents.
        target_directory (str or Path): The directory where files will be saved.
    """
    target_directory = Path(target_directory)
    # Create the target directory if it doesn't exist.
    target_directory.mkdir(parents=True, exist_ok=True)
    
    for file_path, content in files_dict.items():
        # Create the full path for the file.
        full_path = target_directory / file_path
        
        # Ensure parent directories exist.
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write the file content.
        with full_path.open('w', encoding='utf-8') as f:
            f.write(content)
        print(f"Saved: {full_path}")

def main():
    global VERBOSE
    # Adding color support for Windows CMD
    if os.name == 'nt':
        os.system('color')

    args = parse_arguments()
    VERBOSE = args.verbose

    # openai.api_base = "https://nano-gpt.com/api/v1/"
    if len(sys.argv) < 2:
        print("Usage: python script.py '<user_prompt>' [--apply]")
        sys.exit(1)

    user_prompt = sys.argv[1]
    project_dir = os.getcwd()
    enc = tiktoken.get_encoding("o200k_base")

    try:
        encoded_images = load_images(args.image)
    except FileNotFoundError as e:
        print(f"\033[1;31mError loading image: {e}\033[0m")
        sys.exit(1)

    # Load project files, defaulting to current working directory if no additional paths are specified
    if not args.files:
        project_files = load_project_files(project_dir, project_dir)
    else:
        project_files = []
        for additional_path in args.files:
            if os.path.isfile(additional_path):
                with open(additional_path, 'r') as f:
                    project_files.append((additional_path, f.read()))
            elif os.path.isdir(additional_path):
                project_files.extend(load_project_files(additional_path, project_dir))

    if args.prepend:
        prepend = args.prepend+"\n"
    else:
        prepend = ""

    if prepend.startswith("http://") or prepend.startswith("https://"):
        try:
            import urllib.request
            with urllib.request.urlopen(prepend) as response:
                prepend = response.read().decode('utf-8')
        except Exception as e:
            print(f"Error fetching prepend content from URL {prepend}: {e}")
            prepend = ""
    elif os.path.exists(prepend):
        prepend = load_prepend_file(prepend)
    else:
        # If the specified prepend path does not exist, treat the value as literal content.
        prepend = prepend

    if prepend != "":
        prepend += "\n"

    system_prompt = prepend + f"Output a full unified git diff into a ```diff block(diff --git ...)"

    files_content = ""
    for file, content in project_files:
        if VERBOSE:
            print(f"Including {len(enc.encode(content)):5d} tokens", absolute_to_relative(file))
        files_content += f"File: {absolute_to_relative(file)}\nContent:\n{content}\n"

    full_prompt = f"{system_prompt}\n\n{user_prompt}\n\n{files_content}"
    token_count = len(enc.encode(full_prompt))
    if args.model is None:
        args.model = os.getenv('GPTDIFF_MODEL', 'deepseek-reasoner')

    if not args.call and not args.apply:
        """
        For convenience:
          • macOS & Linux  → copy prompt directly to the system clipboard
          • Windows        → fall back to prompt.txt (no reliable native clipboard CLI)
        """
        wrote_location = None

        try:
            if os.name == "nt":
                # Windows: keep legacy behaviour
                with open("prompt.txt", "w") as f:
                    f.write(full_prompt)
                wrote_location = "prompt.txt"
            else:
                # macOS first
                if sys.platform == "darwin" and shutil.which("pbcopy"):
                    subprocess.run(["pbcopy"], input=full_prompt.encode(), check=True)
                    wrote_location = "clipboard (pbcopy)"
                # Linux – prefer wl-copy, then xclip
                elif shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=full_prompt.encode(), check=True)
                    wrote_location = "clipboard (wl-copy)"
                elif shutil.which("xclip"):
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=full_prompt.encode(),
                        check=True,
                    )
                    wrote_location = "clipboard (xclip)"
                # No clipboard utility available – revert to file
                else:
                    with open("prompt.txt", "w") as f:
                        f.write(full_prompt)
                    wrote_location = "prompt.txt (clipboard utility not found)"
        except Exception as e:
            # Any failure falls back to prompt.txt as a safe default
            print(
                f"\033[1;31mClipboard write failed: {e}. Falling back to prompt.txt\033[0m"
            )
            with open("prompt.txt", "w") as f:
                f.write(full_prompt)
            wrote_location = "prompt.txt (clipboard write failed)"

        print(f"Total tokens: {token_count:5d}")
        print(f"\033[1;32mWrote full prompt to {wrote_location}.\033[0m")
        if wrote_location.startswith("prompt.txt"):
            print(
                "Tip: install 'xclip' or 'wl-clipboard' to enable automatic clipboard copy on Linux."
            )
        exit(0)
    else:
        # Validate API key presence before any API operations
        if not os.getenv('GPTDIFF_LLM_API_KEY'):
            print("\033[1;31mError: GPTDIFF_LLM_API_KEY environment variable required\033[0m")
            print("Set it with: export GPTDIFF_LLM_API_KEY='your-key'")
            sys.exit(1)

        # Confirm large requests without specified files
        if (not args.nowarn) and (not args.files) and token_count > 10000 and (args.call or args.apply):
            print(f"\033[1;33mThis is a larger request ({token_count} tokens). Disable this warning with --nowarn. Are you sure you want to send it? [y/N]\033[0m")
            confirmation = input().strip().lower()
            if confirmation != 'y':
                print("Request canceled")
                sys.exit(0)
        full_text, diff_text, prompt_tokens, completion_tokens, total_tokens = call_llm_for_diff(system_prompt, user_prompt, files_content, args.model,
                                                                                                temperature=args.temperature,
                                                                                                api_key=os.getenv('GPTDIFF_LLM_API_KEY'),
                                                                                                base_url=os.getenv('GPTDIFF_LLM_BASE_URL', "https://nano-gpt.com/api/v1/"),
                                                                                                max_tokens=args.max_tokens,
                                                                                                budget_tokens=args.anthropic_budget_tokens,
                                                                                                images=encoded_images
                                                                                                )

    if(diff_text.strip() == ""):
        print(f"\033[1;33mWarning: No valid diff data was generated. This could be due to an unclear prompt or an invalid LLM response.\033[0m")
        print("Suggested action: Refine your prompt or check the full response below for clues.")
        print("Full LLM response:\n---\n" + full_text + "\n---")
        if args.beep:
            print("\a")
        return

    elif args.apply:
        print("\nAttempting apply with the following diff:")
        print(color_code_diff(diff_text))
        print("\033[94m**Attempting to apply patch using basic method...**\033[0m")
        smart_apply_patch(project_dir, diff_text, user_prompt, args)
        #apply_result = apply_diff(project_dir, diff_text)
        #if apply_result:
        #    print(f"\033[1;32mPatch applied successfully with basic apply.\033[0m")
        #else:
        #    print("\033[94m**Attempting smart apply with LLM...**\033[0m")

    if args.beep:
        print("\a")

    green = "\033[92m"
    reset = "\033[0m"
    if VERBOSE:
        print("API Usage Details:")
        print(f"- Prompt tokens: {prompt_tokens}")
        print(f"- Completion tokens: {completion_tokens}")
        print(f"- Total tokens: {total_tokens}")
        print(f"- Model used: {green}{args.model}{reset}")
    else:
        print(f"API Usage: {total_tokens} tokens, Model used: {green}{args.model}{reset}")

def swallow_reasoning(full_response: str) -> (str, str):
    """
    Extracts and swallows the chain-of-thought reasoning section from the full LLM response.
    Assumes the reasoning block starts with a line beginning with "> Reasoning"
    and ends with a line matching 'Reasoned for <number> seconds'.

    Returns:
        A tuple (final_content, reasoning) where:
         - final_content: The response with the reasoning block removed.
         - reasoning: The extracted reasoning block, or an empty string if not found.
    """
    pattern = re.compile(
        r"(?P<reasoning>>\s*Reasoning.*?Reasoned.*?seconds)",
        re.DOTALL
    )
    reasoning_list = []
    def replacer(match):
        raw_reasoning = match.group("reasoning")
        reasoning_lines = [line.lstrip('+').strip() for line in raw_reasoning.splitlines()]
        reasoning = "\n".join(reasoning_lines).strip()
        reasoning_list.append(reasoning)
        return ""
    
    final_content = re.sub(pattern, replacer, full_response)
    reasoning = "\n".join(reasoning_list)
    return final_content.strip(), reasoning

def strip_bad_output(updated: str, original: str) -> str:
    """
    If the LLM wrapped the file content in a Markdown code fence, unwrap it.

    Uses line-based fence detection (open on the first ``` line, close on the
    LAST ``` line) so that inner triple-backticks inside the file content do
    not truncate the result. File content — including a trailing newline — is
    preserved verbatim.
    """
    # If the file itself starts with a code fence, don't try to unwrap.
    if original.lstrip().startswith("```"):
        return updated

    lines = updated.splitlines(keepends=True)

    # Find the first line that opens a fence (possibly after a preamble).
    open_idx = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            open_idx = i
            break
    if open_idx is None:
        return updated

    # Find the LAST line that is exactly a closing fence after the opener.
    close_idx = None
    for i in range(len(lines) - 1, open_idx, -1):
        if lines[i].strip() == "```":
            close_idx = i
            break
    if close_idx is None:
        return updated

    inner = "".join(lines[open_idx + 1:close_idx])

    # If the opener was a bare ``` and the model put the language on the next
    # line instead (e.g. "```\npython\n..."), drop that stray language tag.
    if lines[open_idx].strip() == "```":
        inner_lines = inner.splitlines(keepends=True)
        if len(inner_lines) > 1:
            first_inner = inner_lines[0].strip()
            if first_inner and " " not in first_inner and first_inner.lower() != "diff":
                inner = "".join(inner_lines[1:])

    return inner

if __name__ == "__main__":
    main()
