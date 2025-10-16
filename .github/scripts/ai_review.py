import os
import subprocess
import sys
from pathlib import Path
from openai import OpenAI

# Try to load environment variables from a .env file at the repository root.
# Prefer python-dotenv if installed, otherwise fall back to a simple parser.
repo_root = Path(__file__).resolve().parents[2]
dotenv_path = repo_root / ".env"

try:
    # python-dotenv is listed in requirements.txt; prefer it when available.
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(dotenv_path))
except ImportError:
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
except Exception as e:
    print(f"Failed to load dotenv: {e}")

# Ensure OPENAI_API_KEY is present and fail fast with a helpful message.
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    print(
        "OPENAI_API_KEY not found. Please set it in the environment or in a .env file at the repository root (OPENAI_API_KEY=your_key)."
    )
    sys.exit(1)

# Initialize OpenAI client with the new API
try:
    client = OpenAI(api_key=api_key)
except Exception as e:
    print(f"Failed to initialize OpenAI client: {e}")
    sys.exit(1)

# Get changed files in this PR
try:
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True
    )
    changed_files = [f for f in result.stdout.splitlines() if f.endswith(".py")]
except subprocess.CalledProcessError as e:
    print(f"Git command failed: {e}")
    print(f"stderr: {e.stderr}")
    changed_files = []
except Exception as e:
    print(f"Could not get diff: {e}")
    changed_files = []

if not changed_files:
    print("No Python files changed. Skipping AI review.")
    sys.exit(0)

print(f"Files to review: {', '.join(changed_files)}")

# Read file contents with error handling
combined_code = ""
files_processed = 0
max_file_size = 50000  # Limit file size to prevent huge prompts

for file_path in changed_files:
    try:
        if not Path(file_path).exists():
            print(f"Warning: File {file_path} no longer exists, skipping.")
            continue

        file_size = Path(file_path).stat().st_size
        if file_size > max_file_size:
            print(f"Warning: File {file_path} is too large ({file_size} bytes), skipping.")
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Sanitize content - remove potential sensitive patterns
            lines = content.split('\n')
            sanitized_lines = []
            for line in lines:
                # Skip lines that might contain sensitive information
                line_lower = line.lower()
                if any(keyword in line_lower for keyword in ['password', 'secret', 'token', 'api_key', 'private_key']):
                    sanitized_lines.append(f"# [REDACTED LINE - POTENTIALLY SENSITIVE]")
                else:
                    sanitized_lines.append(line)

            sanitized_content = '\n'.join(sanitized_lines)
            combined_code += f"\n\n### FILE: {file_path}\n{sanitized_content}"
            files_processed += 1

    except (IOError, OSError) as e:
        print(f"Error reading file {file_path}: {e}")
        continue
    except UnicodeDecodeError:
        print(f"Warning: File {file_path} contains non-UTF-8 content, skipping.")
        continue

if files_processed == 0:
    print("No files could be processed. Skipping AI review.")
    sys.exit(0)

print(f"Successfully processed {files_processed} files for review.")

# Send to OpenAI for review
prompt = f"""
You are a senior Django developer reviewing a Pull Request.
Please identify potential issues, anti-patterns, security risks, and suggest best practices.
Only focus on Django, Python, and API code style.
Provide concise, actionable feedback.

Code:
{combined_code}
"""

# Use a more stable model
model_name = os.environ.get("OPENAI_MODEL", "gpt-5")

try:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are an expert code reviewer for Django projects. Provide constructive, specific feedback."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1000,
        temperature=0.1  # Lower temperature for more consistent reviews
    )

    print("\n" + "="*50)
    print("AI Code Review Summary:")
    print("="*50)
    print(response.choices[0].message.content)
    print("="*50)

except Exception as e:
    print(f"AI review failed: {e}")
    # Try with a fallback model if the primary one fails
    if model_name != "gpt-5":
        try:
            print("Retrying with fallback model...")
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert code reviewer for Django projects."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=800
            )
            print("\nAI Code Review Summary (Fallback):")
            print(response.choices[0].message.content)
        except Exception as fallback_error:
            print(f"Fallback also failed: {fallback_error}")
            sys.exit(1)
    else:
        sys.exit(1)
