#!/usr/bin/env python3
import subprocess
import sys

from patchling import generate_diff, smartapply, load_project_files, build_environment, save_files


def run_tests():
    """
    Run pytest in the root directory and return the CompletedProcess.
    """
    result = subprocess.run(["pytest"], capture_output=True, text=True)
    return result


def main():
    iteration = 1
    while True:
        print(f"Iteration {iteration}: Running pytest...")
        result = run_tests()

        if result.returncode == 0:
            print("All tests passed!")
            break
        else:
            print("Tests failed. Generating diff to fix tests.")
            test_output = result.stdout + "\n" + result.stderr
            prompt = "Fix the failing test case: " + test_output

            # Load the current project files
            files = dict(load_project_files("tests", "gptdiff"))
            # Build an environment string from the files
            env = build_environment(files)

            # Generate a diff using GPTDiff API based on the failing test output
            diff = generate_diff(env, prompt, model="o3-mini")

            # Apply the generated diff using smartapply to get updated file contents
            updated_files = smartapply(diff, files, model="o3-mini")

            # Save the updated files back to the project directory
            save_files(updated_files, ".")

            iteration += 1

    sys.exit(0)


if __name__ == "__main__":
    main()
