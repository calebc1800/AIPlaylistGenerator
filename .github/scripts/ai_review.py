"""
Docstring for .github.scripts.ai_review
Automated AI code review for Django projects using OpenAI's API.
This script identifies changed Python files in a GitHub Pull Request,
sends their content to OpenAI for review, and posts the feedback as a comment
on the PR. It includes robust error handling, environment variable management,
and security-conscious code sanitization.
"""
import os
import subprocess
import sys
from pathlib import Path
from openai import OpenAI
from github import Github

# Try to load environment variables from a .env file at the repository root.
# Prefer python-dotenv if installed, otherwise fall back to a simple parser.
REPO_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = REPO_ROOT / ".env"

try:
    # python-dotenv is listed in requirements.txt; prefer it when available.
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(DOTENV_PATH))
except ImportError:
    # Fallback: simple parser that handles KEY=VALUE and ignores comments.
    if DOTENV_PATH.exists():
        try:
            with open(DOTENV_PATH, "r", encoding="utf-8") as f:
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
API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print(
        """OPENAI_API_KEY not found.
            Please set it in the environment or in a .env file 
            at the repository root (OPENAI_API_KEY=your_key)."""
    )
    sys.exit(1)

# Initialize OpenAI client with the new API
try:
    CLIENT = OpenAI(api_key=API_KEY)
except Exception as e:
    print(f"Failed to initialize OpenAI client: {e}")
    sys.exit(1)

# Get GitHub environment variables
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
PR_NUMBER = os.environ.get("PR_NUMBER")
REPOSITORY_NAME = os.environ.get("REPOSITORY_NAME")
BASE_SHA = os.environ.get("BASE_SHA")
HEAD_SHA = os.environ.get("HEAD_SHA")

print(f"Repository: {REPOSITORY_NAME}")
print(f"PR Number: {PR_NUMBER}")
print(f"Base SHA: {BASE_SHA}")
print(f"Head SHA: {HEAD_SHA}")

# Get changed files in this PR using improved git commands
CHANGED_FILES = []

# Try multiple approaches to get the diff
GIT_COMMANDS = [
    # First try: use the SHAs provided by GitHub
    ["git", "diff", "--name-only", f"{BASE_SHA}...{HEAD_SHA}"] if BASE_SHA and HEAD_SHA else None,
    # Second try: fetch and use origin/main
    ["git", "fetch", "origin", "main"],
    ["git", "diff", "--name-only", "origin/main...HEAD"],
    # Third try: use merge-base approach
    ["git", "diff", "--name-only", "$(git merge-base origin/main HEAD)", "HEAD"],
    # Fourth try: just get recent changes
    ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
]

for i, cmd in enumerate(GIT_COMMANDS):
    if cmd is None:
        continue

    try:
        print(f"Trying git command {i+1}: {' '.join(cmd)}")

        if i == 1:  # This is the fetch command
            result = subprocess.run(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=True)
            continue  # Just fetch, don't process output

        if "$(git merge-base" in ' '.join(cmd):
            # Handle the merge-base command specially
            merge_base_result = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
            )
            merge_base = merge_base_result.stdout.strip()
            actual_cmd = ["git", "diff", "--name-only", merge_base, "HEAD"]
            result = subprocess.run(actual_cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=True)
        else:
            result = subprocess.run(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=True)

        CHANGED_FILES = [f for f in result.stdout.splitlines() if f.endswith(".py")]
        if CHANGED_FILES:
            print(f"Successfully got {len(CHANGED_FILES)} Python files using command {i+1}")
            break

    except subprocess.CalledProcessError as e:
        print(f"Git command {i+1} failed: {e}")
        print(f"stderr: {e.stderr}")
        continue
    except Exception as e:
        print(f"Unexpected error with git command {i+1}: {e}")
        continue

if not CHANGED_FILES:
    print("No Python files changed. Skipping AI review.")
    sys.exit(0)

print(f"Files to review: {', '.join(CHANGED_FILES)}")

# Read file contents with error handling
COMBINED_CODE = ""
FILES_PROCESSED = 0
MAX_FILE_SIZE = 50000  # Limit file size to prevent huge prompts

for file_path in CHANGED_FILES:
    try:
        if not Path(file_path).exists():
            print(f"Warning: File {file_path} no longer exists, skipping.")
            continue

        file_size = Path(file_path).stat().st_size
        if file_size > MAX_FILE_SIZE:
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
                if any(keyword in line_lower for keyword in ['password', 'secret',
                                                             'token', 'api_key', 'private_key']):
                    sanitized_lines.append("# [REDACTED LINE - POTENTIALLY SENSITIVE]")
                else:
                    sanitized_lines.append(line)

            SANITIZED_CONTENT = '\n'.join(sanitized_lines)
            COMBINED_CODE += f"\n\n### FILE: {file_path}\n{SANITIZED_CONTENT}"
            FILES_PROCESSED += 1

    except (IOError, OSError) as e:
        print(f"Error reading file {file_path}: {e}")
        continue
    except UnicodeDecodeError:
        print(f"Warning: File {file_path} contains non-UTF-8 content, skipping.")
        continue

if FILES_PROCESSED == 0:
    print("No files could be processed. Skipping AI review.")
    sys.exit(0)

print(f"Successfully processed {FILES_PROCESSED} files for review.")

# Send to OpenAI for review
PROMPT = f"""
You are a senior Django developer reviewing a Pull Request.
Please identify potential issues, anti-patterns, security risks, and suggest best practices.
Only focus on Django, Python, and API code style.
Provide concise, actionable feedback in a clear, professional format.
Be as restrictive as possible to Django and Python best practices. Include a score out of 10 for code quality.

Files changed: {', '.join(CHANGED_FILES)}

Code:
{COMBINED_CODE}
"""

# Use a configurable model
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-5")
FALLBACK_MODEL = "gpt-4o"

AI_REVIEW_CONTENT = ""
try:
    response = CLIENT.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content":
            """You are an expert code reviewer for Django projects.
             Provide constructive, specific feedback with clear recommendations."""},
            {"role": "user", "content": PROMPT}
        ],
        max_completion_tokens=75000
    )

    AI_REVIEW_CONTENT = response.choices[0].message.content.strip()

    if not AI_REVIEW_CONTENT:
        raise ValueError("Primary model returned empty content")

    print("\n" + "="*50)
    print("AI Code Review Summary:")
    print("="*50)
    print(AI_REVIEW_CONTENT)
    print("="*50)

except Exception as e:
    print(f"Primary model {MODEL_NAME} failed: {e}")
    # Try with a fallback model if the primary one fails
    if MODEL_NAME != FALLBACK_MODEL:
        try:
            print(f"Retrying with fallback model {FALLBACK_MODEL}...")
            response = CLIENT.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=[
                    {"role": "system",
                     "content": "You are an expert code reviewer for Django projects."},
                    {"role": "user", "content": PROMPT}
                ],
                max_completion_tokens=1200
            )
            AI_REVIEW_CONTENT = response.choices[0].message.content.strip()
            print("\n" + "="*50)
            print("AI Code Review Summary (using fallback model):")
            print("="*50)
            print(AI_REVIEW_CONTENT)
            print("="*50)
        except Exception as fallback_error:
            print(f"Fallback also failed: {fallback_error}")
            AI_REVIEW_CONTENT = """AI review failed due to API issues.
            Please check the logs for details."""
    else:
        AI_REVIEW_CONTENT = """AI review failed due to API issues.
        Please check the logs for details."""

# Post the AI review as a GitHub PR comment
if GITHUB_TOKEN and PR_NUMBER and REPOSITORY_NAME and AI_REVIEW_CONTENT:
    try:
        print("\nPosting AI review as GitHub PR comment...")

        # Initialize GitHub client
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPOSITORY_NAME)
        pr = repo.get_pull(int(PR_NUMBER))

        # Format the comment
        COMMENT_BODY = f"""## 🤖 AI Code Review

**Files reviewed:** {', '.join(CHANGED_FILES)}

{AI_REVIEW_CONTENT}

---
*This review was automatically generated by AI. Please use it as guidance alongside human code review.*
"""

        # Check if there's already an AI review comment to update instead of creating new ones
        EXISTING_COMMENT = None
        for comment in pr.get_issue_comments():
            if comment.body.startswith("## 🤖 AI Code Review"):
                EXISTING_COMMENT = comment
                break

        if EXISTING_COMMENT:
            EXISTING_COMMENT.edit(COMMENT_BODY)
            print(f"Updated existing AI review comment: {EXISTING_COMMENT.html_url}")
        else:
            new_comment = pr.create_issue_comment(COMMENT_BODY)
            print(f"Posted new AI review comment: {new_comment.html_url}")

    except Exception as e:
        print(f"Failed to post GitHub comment: {e}")
        print("AI review completed but could not be posted as PR comment.")

else:
    print("GitHub PR comment not posted - missing required environment variables or content.")
    if not GITHUB_TOKEN:
        print("Missing GITHUB_TOKEN")
    if not PR_NUMBER:
        print("Missing PR_NUMBER")
    if not REPOSITORY_NAME:
        print("Missing REPOSITORY_NAME")
    if not AI_REVIEW_CONTENT:
        print("No AI review content generated")


print("\n✅ AI Code Review completed successfully!")
