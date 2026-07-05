#!/usr/bin/env python3

# This allows you to run any arbitrary command repeatedly until it passes.
# You can run a test suite or a compilation step, asking the AI to fix it the errors. When the process completes successfully then the loop will stop.
import sys
import subprocess
import os
from patchling.core import load_project_files, build_environment, smartapply, save_files, generate_diff

def run_command(command):
    """
    Runs the given command (list of strings) and returns the CompletedProcess.
    """
    try:
        # Run the command, capturing both stdout and stderr.
        result = subprocess.run(command, capture_output=True, text=True)
        return result
    except Exception as e:
        print(f"Error running command {command}: {e}")
        sys.exit(1)

def main():
    # Ensure the user provided a command.
    if len(sys.argv) < 2:
        print("Usage: python3 gpt-autobuild.py <command> [args...]")
        sys.exit(1)
    
    # The command (and any additional arguments) passed on the command line.
    command = sys.argv[1:]
    
    iteration = 1
    while True:
        print(f"\n--- Iteration {iteration} ---")
        
        # Load project files from the current directory.
        original_files = dict(load_project_files(".", "."))
        
        # Run the user-specified command.
        result = run_command(command)
        
        # Combine stdout and stderr as the build log.
        build_log = result.stdout + "\n" + result.stderr
        
        # Save the build log in the files dictionary so GPT can see it.
        original_files["build command"] = " ".join(command)
        original_files["build.log"] = build_log
        
        # Check if the build (or test) command succeeded.
        print('build', result)

        if result.returncode == 0:
            print("Build succeeded!")
            break
        else:
            print("Build failed. Generating diff to fix errors...")
        
        # Build the environment for GPT-based diff generation.
        env = build_environment(original_files)
        
        # Use GPT to generate a diff that “fixes the build while preserving all functionality.”
        diff = generate_diff(env, "Fix the build while preserving all functionality")
        
        # Apply the diff to the original files.
        updated_files = smartapply(diff, {k: v for k, v in original_files.items() if k != "build.log"})
        
        # Save the updated files back to the project.
        # (This function should overwrite the existing files with the new content.)
        save_files(updated_files, ".")
        
        print("Applied fixes. Re-running build...")
        iteration += 1

if __name__ == "__main__":
    main()
