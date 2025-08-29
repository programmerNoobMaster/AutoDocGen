from imports import *
from chunking import clone_repo, extract_all_chunks
from save_to_vector_db import save_to_faiss_split_by_ext
from graph import build_graph, SectionSpec

import os, sys, argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Generate technical documentation")
    parser.add_argument("--repo", dest="repo", default=None, required=False,
                        help="GitHub repo URL (or set REPO_URL env variable)")
    args = parser.parse_args()

    # Prefer CLI argument, then environment variable
    repo_url = args.repo or os.environ.get("REPO_URL")
    if not repo_url:
        print("ERROR: Provide a repo via --repo <url> (or set REPO_URL).", file=sys.stderr)
        sys.exit(2)

    # --- pipeline ---
    repo_path = clone_repo(repo_url)
    chunks = extract_all_chunks(repo_path)
    stats = save_to_faiss_split_by_ext(chunks, base_dir="docs_index", model="text-embedding-3-small")
    print(stats)

    app = build_graph()

    # Sections — exactly as you had
    overview = SectionSpec(
        name="Objective & Scope",
        query="Project goals/objectives and scope or limitations as described in README and docstrings.",
        route="both",
        k_text=12,
        guidance="Include '### Goals' bullets and '### Out of Scope' bullets."
    )
    print("Wrote:", app.invoke({"spec": overview})["out_path"])

    architecture = SectionSpec(
        name="System Architecture",
        query="Architecture overview of the project: high-level system architecture and component responsibilities",
        route="both",
        k_text=10,
        k_code=20,
        guidance="Focus on the bigger as well as smaller picture.",
        additional_context=""" 
(… same guidance you pasted …)
"""
    )
    print("Wrote:", app.invoke({"spec": architecture})["out_path"])

    technologies = SectionSpec(
        name="Technologies Used",
        query="Installation prerequisites and versions",
        route="both",
        k_text=5,
        k_code=5,
        guidance="""
        Just list the technologies used in a way like
        Languages: Python, JavaScript
        Frameworks: Flask, React
        Packages: NumPy, Pandas
        """,
    )
    print("Wrote:", app.invoke({"spec": technologies})["out_path"])

    installation_guide = SectionSpec(
        name="Installation & Setup",
        query="Installation prerequisites, enviornment variables and versions",
        route="both",
        k_text=5,
        k_code=5,
        guidance="Write a step by step guide for installation and setup",
    )
    print("Wrote:", app.invoke({"spec": installation_guide})["out_path"])

    api_key = SectionSpec(
        name="API Key",
        route="both",
        k_text=5,
        k_code=5,
        query=(
            "API endpoints, FastAPI/Flask routes @app.get @router.post @blueprint.route "
            "openapi swagger schema path operation request response status code "
            "environment variables os.getenv os.environ BaseSettings pydantic dotenv "
            ".env config settings yaml toml json"
        ),
        guidance=(
            "Write a deep, exact section:\n"
            "1) Base URL & API version (if present).\n"
            "2) Auth scheme (key/header/bearer), rate limits if any.\n"
            "3) Endpoints table: Method | Path | Summary | Request | Response | Source tag.\n"
            "4) Environment variables table: NAME | Purpose | Where read (file:lines) | Default/example if visible.\n"
            "5) Example curl for 1–2 key endpoints.\n"
            "Apply strict citation rules: every sentence must end with a single allowed tag. "
            "If info is missing, write (Information not available in repository). "
            "Do NOT invent endpoints or env vars."
        ),
    )
    print("Wrote:", app.invoke({"spec": api_key})["out_path"])


if __name__ == "__main__":
    main()
