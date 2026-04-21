#!/usr/bin/env python3
"""
new_city.py — Spin up a new city deployment
Usage: python new_city.py
Requirements: pip install openai anthropic railway (Railway CLI via npm)
Run from your project root where config.template.json lives.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────

def info(msg):    print(f"\n  [✓] {msg}")
def prompt(msg):  return input(f"\n  [?] {msg}\n  > ").strip()
def error(msg):   print(f"\n  [✗] {msg}"); sys.exit(1)
def divider():    print("\n" + "─" * 50)

def run(cmd, check=True):
    """Run a shell command, print output live."""
    result = subprocess.run(cmd, shell=True, text=True)
    if check and result.returncode != 0:
        error(f"Command failed: {cmd}")
    return result

def check_prereqs():
    divider()
    print("  Checking prerequisites...")
    if subprocess.run("railway --version", shell=True, capture_output=True).returncode != 0:
        error("Railway CLI not found. Install it: npm install -g @railway/cli")
    try:
        import openai
    except ImportError:
        error("openai package not found. Run: pip install openai")
    info("Prerequisites OK")

# ── Step 1: Gather city info ──────────────────────────────

def gather_info():
    divider()
    print("  Step 1 of 4: City information")
    divider()

    info_dict = {}
    info_dict["city_name"]      = prompt("City name (e.g. Palm Springs):")
    info_dict["city_slug"]      = prompt("City slug — no spaces, lowercase (e.g. palmsprings):")
    info_dict["city_subdomain"] = prompt("Subdomain (e.g. palmsprings.yourdomain.com):")
    info_dict["admin_email"]    = prompt("City admin email:")
    info_dict["anthropic_key"]  = prompt("Anthropic API key for this city:")
    info_dict["openai_key"]     = prompt("OpenAI API key (for vector store):")

    return info_dict

# ── Step 2: Railway project ───────────────────────────────

def setup_railway(info_dict):
    divider()
    print("  Step 2 of 4: Railway project")
    divider()

    info("Logging into Railway...")
    run("railway login")

    choice = prompt("Create a NEW Railway project? (y/n — enter n to link existing):")
    if choice.lower() == "y":
        run(f"railway init --name {info_dict['city_slug']}")
        info(f"Railway project created: {info_dict['city_slug']}")
    else:
        project_id = prompt("Enter your existing Railway project ID:")
        run(f"railway link {project_id}")

    info("Setting environment variables...")
    vars_str = " ".join([
        f'CITY_NAME="{info_dict["city_name"]}"',
        f'CITY_SLUG="{info_dict["city_slug"]}"',
        f'CONFIG_PATH="/data/config.json"',
        f'ANTHROPIC_API_KEY="{info_dict["anthropic_key"]}"',
        f'OPENAI_API_KEY="{info_dict["openai_key"]}"',
        f'ADMIN_EMAIL="{info_dict["admin_email"]}"',
    ])
    run(f"railway variables set {vars_str}")
    info("Environment variables set.")

# ── Step 3: Generate and upload config ────────────────────

def setup_config(info_dict):
    divider()
    print("  Step 3 of 4: Generate config.json")
    divider()

    template_path = Path("config.template.json")
    if not template_path.exists():
        error("config.template.json not found. Run this script from your project root.")

    with open(template_path) as f:
        config = json.load(f)

    # Patch city-specific values — adjust keys to match your config structure
    patches = {
        "city_name":   info_dict["city_name"],
        "city_slug":   info_dict["city_slug"],
        "admin_email": info_dict["admin_email"],
    }
    config.update(patches)

    output_path = Path(f"{info_dict['city_slug']}_config.json")
    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    info(f"Config written to {output_path}")
    print(f"\n  Open and review {output_path} before continuing.")
    prompt("Press Enter when config.json is ready to upload...")

    info("Uploading config to Railway volume...")
    run(f'railway run cp ".\\{output_path}" /data/config.json')
    info("config.json uploaded to /data/config.json on Railway volume.")

    return output_path, config

# ── Step 4: Vector store + document upload ────────────────

def setup_vector_store(info_dict, config, output_path):
    divider()
    print("  Step 4 of 4: OpenAI vector store")
    divider()

    docs_path = prompt("Path to folder containing this city's documents:")
    docs_dir = Path(docs_path)
    if not docs_dir.is_dir():
        error(f"Directory not found: {docs_path}")

    from openai import OpenAI
    client = OpenAI(api_key=info_dict["openai_key"])

    info("Creating vector store...")
    vs = client.beta.vector_stores.create(name=f"{info_dict['city_slug']}-store")
    info(f"Vector store created: {vs.id}")

    file_ids = []
    files = [f for f in docs_dir.iterdir() if f.is_file()]
    if not files:
        error(f"No files found in {docs_path}")

    info(f"Uploading {len(files)} document(s)...")
    for fpath in files:
        with open(fpath, "rb") as f:
            uploaded = client.files.create(file=f, purpose="assistants")
            file_ids.append(uploaded.id)
            print(f"    Uploaded: {fpath.name} → {uploaded.id}")

    info("Attaching files to vector store...")
    client.beta.vector_stores.file_batches.create(
        vector_store_id=vs.id,
        file_ids=file_ids
    )
    info(f"All {len(file_ids)} file(s) attached.")

    # Patch vector_store_id into local config and re-upload
    config["vector_store_id"] = vs.id
    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    info("Patching vector_store_id into config on Railway volume...")
    run(f'railway run cp ".\\{output_path}" /data/config.json')
    info("Config updated with vector_store_id.")

    return vs.id

# ── Step 5: Deploy ────────────────────────────────────────

def deploy(info_dict, vector_store_id):
    divider()
    print("  Deploying to Railway...")
    divider()

    run("railway up --detach")
    info("Deployment triggered.")

    divider()
    print("\n  Done! City setup complete.\n")
    print(f"  City:         {info_dict['city_name']}")
    print(f"  Subdomain:    {info_dict['city_subdomain']}")
    print(f"  Vector store: {vector_store_id}")
    print(f"  Config:       /data/config.json (on Railway volume)")
    print(f"\n  Next: point DNS for {info_dict['city_subdomain']} to your Railway service.")
    divider()

# ── Main ──────────────────────────────────────────────────

def main():
    print("\n" + "═" * 50)
    print("  New City Setup")
    print("═" * 50)

    check_prereqs()
    info_dict           = gather_info()
    setup_railway(info_dict)
    output_path, config = setup_config(info_dict)
    vector_store_id     = setup_vector_store(info_dict, config, output_path)
    deploy(info_dict, vector_store_id)

if __name__ == "__main__":
    main()
