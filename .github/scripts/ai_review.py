import os
import openai
import subprocess
import sys
from pathlib import Path

# Try to load environment variables from a .env file at the repository root.
# Prefer python-dotenv if installed, otherwise fall back to a simple parser.
repo_root = Path(__file__).resolve().parents[2]
dotenv_path = repo_root / ".env"

try:
    # python-dotenv is listed in requirements.txt; prefer it when available.
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(dotenv_path))
except Exception:
    # Fallback: simple parser that handles KEY=VALUE and ignores comments.
    if dotenv_path.exists():
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    # Don't overwrite existing environment vars
                    if key not in os.environ:
                        os.environ[key] = val
        except Exception as e:
            print(f"Failed to read .env file: {e}")

# Ensure OPENAI_API_KEY is present and fail fast with a helpful message.
openai.api_key = os.environ.get("OPENAI_API_KEY")
if not openai.api_key:
    print(
        "OPENAI_API_KEY not found. Please set it in the environment or in a .env file at the repository root (OPENAI_API_KEY=your_key)."
    )
    sys.exit(1)

# Get changed files in this PR
try:
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    changed_files = [f for f in result.stdout.splitlines() if f.endswith(".py")]
except Exception as e:
    print(f"Could not get diff: {e}")
    changed_files = []

if not changed_files:
    print("No Python files changed. Skipping AI review.")
    exit(0)

print(f"Files to review: {', '.join(changed_files)}")

# Read file contents
combined_code = ""
for file in changed_files:
    with open(file, "r", encoding="utf-8") as f:
        combined_code += f"\n\n### FILE: {file}\n" + f.read()

# Send to OpenAI for review
prompt = f"""
You are a senior Django developer reviewing a Pull Request.
Please identify potential issues, anti-patterns, security risks, and suggest best practices.
Only focus on Django, Python, and API code style.

Code:
{combined_code}
"""

try:
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an expert code reviewer for Django projects."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=700
    )
    print("AI Code Review Summary:")
    print(response.choices[0].message.content)
except Exception as e:
    print(f"AI review failed: {e}")
