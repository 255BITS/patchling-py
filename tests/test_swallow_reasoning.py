import pytest

from patchling.core import swallow_reasoning

def test_swallow_reasoning_extraction_simple():
    llm_response = (
        "+> Reasoning\n"
        "+None\n"
        "+Reasoned about summary drawer button 변경 for 15 seconds\n"
        "+def new():\n"
        "```"
    )
    final_content, reasoning = swallow_reasoning(llm_response)
    expected_reasoning = (
        "> Reasoning\n"
        "None\n"
        "Reasoned about summary drawer button 변경 for 15 seconds"
    )
    assert reasoning == expected_reasoning
    # The final content should no longer contain the reasoning block.
    assert expected_reasoning not in final_content


def test_swallow_reasoning_extraction_multiline():
    llm_response = (
        "line 1> Reasoning\n"
        "+None\n"
        "+Reasoned about summary drawer button 변경 for 1 seconds\n"
        "line 2\n"
        "  > Reasoning\n"
        "+None\n"
        "+Reasoned about summary drawer button 변경 for 2 seconds\n"
        "line 3:"
    )
    final_content, reasoning = swallow_reasoning(llm_response)
    expected_reasoning = (
        "> Reasoning\n"
        "None\n"
        "Reasoned about summary drawer button 변경 for 1 seconds\n"
        "> Reasoning\n"
        "None\n"
        "Reasoned about summary drawer button 변경 for 2 seconds"
    )
    assert reasoning == expected_reasoning
    assert "line 1\nline 2\n  \nline 3:" == final_content
    # The final content should no longer contain the reasoning block.
    assert expected_reasoning not in final_content

def test_swallow_reasoning_with_untested_response():
    llm_response = (
        "> Reasoning\n"
        "**Considering the request**\n"
        "I’m noting that the user wants me to apply a diff to a file and return the result in a block, ensuring the entire file is included.\n"
        "**Ensuring comprehensive inclusion**\n"
        "I'm making sure the entire file is included when presenting the result in a block, following the user's request carefully.\n"
        "**Ensuring clarity**\n"
        "I’m integrating the diff into the file and ensuring the entire file is returned as requested. This approach maintains precision and clarity in the response.\n"
        "**Refining the response**\n"
        "I’m focusing on how to structure the response by carefully integrating the diff and ensuring the entire file is included in a clear block format.\n"
        "**Connecting the pieces**\n"
        "I'm mapping out how to apply the diff to the file carefully and ensure the entire file is incorporated into the final block.\n"
        "Reasoned for a few seconds\n"
        "\n"
        "```diff\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def old():\n"
        "+def new():\n"
        "```"
    )
    final_content, reasoning = swallow_reasoning(llm_response)

    expected_reasoning = (
        "> Reasoning\n"
        "**Considering the request**\n"
        "I’m noting that the user wants me to apply a diff to a file and return the result in a block, ensuring the entire file is included.\n"
        "**Ensuring comprehensive inclusion**\n"
        "I'm making sure the entire file is included when presenting the result in a block, following the user's request carefully.\n"
        "**Ensuring clarity**\n"
        "I’m integrating the diff into the file and ensuring the entire file is returned as requested. This approach maintains precision and clarity in the response.\n"
        "**Refining the response**\n"
        "I’m focusing on how to structure the response by carefully integrating the diff and ensuring the entire file is included in a clear block format.\n"
        "**Connecting the pieces**\n"
        "I'm mapping out how to apply the diff to the file carefully and ensure the entire file is incorporated into the final block.\n"
        "Reasoned for a few seconds"
    )

    assert reasoning == expected_reasoning
    # The final content should no longer contain the reasoning block.
    assert expected_reasoning not in final_content
    # And it should contain the diff block.
    assert "```diff" in final_content

def test_swallow_reasoning_extraction():
    llm_response = (
        "> Reasoning\n"
        "**Applying the diff**\n"
        "I'm piecing together how to efficiently apply a diff to a file...\n"
        "**Returning the result**\n"
        "I'm finalizing the method to apply the diff updates...\n"
        "Reasoned for 6 seconds\n"
        "\n"
        "```diff\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def old():\n"
        "+def new():\n"
        "```"
    )
    final_content, reasoning = swallow_reasoning(llm_response)
    expected_reasoning = (
        "> Reasoning\n"
        "**Applying the diff**\n"
        "I'm piecing together how to efficiently apply a diff to a file...\n"
        "**Returning the result**\n"
        "I'm finalizing the method to apply the diff updates...\n"
        "Reasoned for 6 seconds"
    )
    assert reasoning == expected_reasoning
    # The final content should no longer contain the reasoning block.
    assert expected_reasoning not in final_content
    # And it should contain the diff block.
    assert "```diff" in final_content


def test_swallow_reasoning_no_reasoning():
    llm_response = (
        "```diff\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def old():\n"
        "+def new():\n"
        "```"
    )
    final_content, reasoning = swallow_reasoning(llm_response)
    assert reasoning == ""
    assert final_content == llm_response.strip()

def test_swallow_reasoning_inline_newlines():
    llm_response = (
        "Prefix text before reasoning and some inline content "
        "> Reasoning\n"
        "Inline line 1\n"
        "Inline line 2\n"
        "Reasoned for 2 seconds "
        "and then suffix text.\n"
        "```diff\n"
        "--- a/inline.py\n"
        "+++ b/inline.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-print('Old')\n"
        "+print('New')\n"
        "```"
    )
    final_content, reasoning = swallow_reasoning(llm_response)
    expected_reasoning = (
        "> Reasoning\n"
        "Inline line 1\n"
        "Inline line 2\n"
        "Reasoned for 2 seconds"
    )
    # Count the newlines in the extracted reasoning block.
    newline_count = reasoning.count('\n')
    # There should be 3 newline characters: after "> Reasoning", after "Inline line 1", and after "Inline line 2"
    assert newline_count == 3, f"Expected 3 newlines, got {newline_count}"
    assert reasoning == expected_reasoning
    # Ensure the reasoning block is removed from the final content.
    assert expected_reasoning not in final_content
    # Verify that surrounding content remains.
    assert "Prefix text before reasoning" in final_content
    assert "and then suffix text." in final_content
    # Verify that the diff block is still present.
    assert "```diff" in final_content