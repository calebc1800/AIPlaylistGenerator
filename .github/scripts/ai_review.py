import os
import subprocess
import sys
from pathlib import Path
from openai import OpenAI
from github import Github
import json
import requests

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

# Get GitHub environment variables
github_token = os.environ.get("GITHUB_TOKEN")
pr_number = os.environ.get("PR_NUMBER")
repository_name = os.environ.get("REPOSITORY_NAME")
base_sha = os.environ.get("BASE_SHA")
head_sha = os.environ.get("HEAD_SHA")

print(f"Repository: {repository_name}")
print(f"PR Number: {pr_number}")
print(f"Base SHA: {base_sha}")
print(f"Head SHA: {head_sha}")

# Get changed files in this PR using improved git commands
changed_files = []

# Try multiple approaches to get the diff
git_commands = [
    # First try: use the SHAs provided by GitHub
    ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"] if base_sha and head_sha else None,
    # Second try: fetch and use origin/main
    ["git", "fetch", "origin", "main"],
    ["git", "diff", "--name-only", "origin/main...HEAD"],
    # Third try: use merge-base approach
    ["git", "diff", "--name-only", "$(git merge-base origin/main HEAD)", "HEAD"],
    # Fourth try: just get recent changes
    ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
]

for i, cmd in enumerate(git_commands):
    if cmd is None:
        continue

    try:
        print(f"Trying git command {i+1}: {' '.join(cmd)}")

        if i == 1:  # This is the fetch command
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            continue  # Just fetch, don't process output

        if "$(git merge-base" in ' '.join(cmd):
            # Handle the merge-base command specially
            merge_base_result = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            merge_base = merge_base_result.stdout.strip()
            actual_cmd = ["git", "diff", "--name-only", merge_base, "HEAD"]
            result = subprocess.run(actual_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        else:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

        changed_files = [f for f in result.stdout.splitlines() if f.endswith(".py")]
        if changed_files:
            print(f"Successfully got {len(changed_files)} Python files using command {i+1}")
            break

    except subprocess.CalledProcessError as e:
        print(f"Git command {i+1} failed: {e}")
        print(f"stderr: {e.stderr}")
        continue
    except Exception as e:
        print(f"Unexpected error with git command {i+1}: {e}")
        continue

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
Provide concise, actionable feedback in a clear, professional format.

Files changed: {', '.join(changed_files)}

Code:
{combined_code}
"""

# Use a configurable model
model_name = os.environ.get("OPENAI_MODEL", "gpt-5")

ai_review_content = ""
try:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are an expert code reviewer for Django projects. Provide constructive, specific feedback with clear recommendations."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=1500,
        temperature=0.1  # Lower temperature for more consistent reviews
    )

    ai_review_content = response.choices[0].message.content

    print("\n" + "="*50)
    print("AI Code Review Summary:")
    print("="*50)
    print(ai_review_content)
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
                max_tokens=1200
            )
            ai_review_content = response.choices[0].message.content
            print("\nAI Code Review Summary (Fallback):")
            print(ai_review_content)
        except Exception as fallback_error:
            print(f"Fallback also failed: {fallback_error}")
            ai_review_content = "AI review failed due to API issues. Please check the logs for details."
    else:
        ai_review_content = "AI review failed due to API issues. Please check the logs for details."

# Post the AI review as a GitHub PR comment
if github_token and pr_number and repository_name and ai_review_content:
    try:
        print("\nPosting AI review as GitHub PR comment...")

        # Initialize GitHub client
        g = Github(github_token)
        repo = g.get_repo(repository_name)
        pr = repo.get_pull(int(pr_number))

        # Format the comment
        comment_body = f"""## 🤖 AI Code Review

**Files reviewed:** {', '.join(changed_files)}

{ai_review_content}

---
*This review was automatically generated by AI. Please use it as guidance alongside human code review.*
"""

        # Check if there's already an AI review comment to update instead of creating new ones
        existing_comment = None
        for comment in pr.get_issue_comments():
            if comment.body.startswith("## 🤖 AI Code Review"):
                existing_comment = comment
                break

        if existing_comment:
            existing_comment.edit(comment_body)
            print(f"Updated existing AI review comment: {existing_comment.html_url}")
        else:
            new_comment = pr.create_issue_comment(comment_body)
            print(f"Posted new AI review comment: {new_comment.html_url}")

    except Exception as e:
        print(f"Failed to post GitHub comment: {e}")
        print("AI review completed but could not be posted as PR comment.")

else:
    print("GitHub PR comment not posted - missing required environment variables or content.")
    if not github_token:
        print("Missing GITHUB_TOKEN")
    if not pr_number:
        print("Missing PR_NUMBER")
    if not repository_name:
        print("Missing REPOSITORY_NAME")
    if not ai_review_content:
        print("No AI review content generated")

print("\n✅ AI Code Review completed successfully!")
