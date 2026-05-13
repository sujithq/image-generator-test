"""
Generate an image using Azure OpenAI and save it to a local file.

Authentication precedence:
  1. DefaultAzureCredential (recommended – uses GitHub Actions OIDC when
     azure/login@v2 has run in the same job).
  2. Azure OpenAI API key via AZURE_OPENAI_API_KEY (optional fallback, only
     used when the environment variable is present and non-empty).

Required environment variables:
  AZURE_OPENAI_ENDPOINT          – https://<resource>.openai.azure.com/
  AZURE_OPENAI_IMAGE_DEPLOYMENT  – deployment name (NOT the model name)

Optional environment variables:
  AZURE_OPENAI_API_KEY  – API key fallback (only when key auth is enabled)

Command-line arguments:
  --prompt   TEXT    Image description (required)
    --size     TEXT    Image size, e.g. 1024x1024 (default: 1024x1024)
    --quality  TEXT    Image quality: low, medium, high, or auto (default: medium)
  --output   FILE    Output file path (default: image.png)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


AZURE_OPENAI_API_VERSION = "2024-02-01"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an image with Azure OpenAI and save it locally."
    )
    parser.add_argument("--prompt", required=True, help="Image description prompt.")
    parser.add_argument(
        "--size",
        default="1024x1024",
        help="Image dimensions (default: 1024x1024).",
    )
    parser.add_argument(
        "--quality",
        default="medium",
        choices=["low", "medium", "high", "auto"],
        help="Image quality (default: medium).",
    )
    parser.add_argument(
        "--output",
        default="image.png",
        help="Output file path (default: image.png).",
    )
    return parser


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"ERROR: Required environment variable '{name}' is not set or empty.", file=sys.stderr)
        sys.exit(1)
    return value


def load_settings() -> dict:
    """Load non-sensitive configuration from environment variables."""
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    deployment = _require_env("AZURE_OPENAI_IMAGE_DEPLOYMENT")
    return {"endpoint": endpoint, "deployment": deployment}


# ---------------------------------------------------------------------------
# Azure OpenAI client construction
# ---------------------------------------------------------------------------

def build_client(settings: dict):
    """Return an AzureOpenAI client using the best available credential.

    The optional API key is read directly here so that no function returning
    non-sensitive settings is ever in scope with secret values, avoiding
    false-positive taint paths in static analysis.
    """
    try:
        from openai import AzureOpenAI  # type: ignore
    except ImportError:
        print(
            "ERROR: 'openai' package is not installed. Run: pip install openai",
            file=sys.stderr,
        )
        sys.exit(1)

    # Optional API-key fallback – only used when the variable is present and non-empty.
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    if api_key:
        print("INFO: Using API key authentication (AZURE_OPENAI_API_KEY is set).")
        return AzureOpenAI(
            azure_endpoint=settings["endpoint"],
            api_key=api_key,
            api_version=AZURE_OPENAI_API_VERSION,
        )

    os.environ.pop("AZURE_OPENAI_API_KEY", None)

    # Default path: token credential (GitHub Actions OIDC or managed identity).
    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider  # type: ignore
    except ImportError:
        print(
            "ERROR: 'azure-identity' package is not installed. Run: pip install azure-identity",
            file=sys.stderr,
        )
        sys.exit(1)

    print("INFO: Using DefaultAzureCredential (OIDC / managed identity).")
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=settings["endpoint"],
        azure_ad_token_provider=token_provider,
        api_version=AZURE_OPENAI_API_VERSION,
    )


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

def generate_image(client, settings: dict, args: argparse.Namespace) -> dict:
    """Call Azure OpenAI image generation and return response metadata."""
    deployment = settings["deployment"]
    print(f"INFO: Generating image with deployment='{deployment}', size={args.size}, "
          f"quality={args.quality}.")

    request = {
        "model": deployment,
        "prompt": args.prompt,
        "n": 1,
        "size": args.size,
        "quality": args.quality,
    }

    try:
        response = client.images.generate(**request)
    except Exception as exc:
        _handle_api_error(exc)

    image_data = response.data[0]
    return {
        "b64_json": getattr(image_data, "b64_json", None),
        "url": getattr(image_data, "url", None),
        "revised_prompt": getattr(image_data, "revised_prompt", None),
    }


def _handle_api_error(exc: Exception) -> None:
    """Print a user-friendly message for common API errors and exit."""
    # Use specific openai exception types when available; fall back to string matching.
    exc_type = type(exc).__name__
    message = str(exc)

    if exc_type == "AuthenticationError" or "401" in message or "403" in message:
        print(
            "ERROR: Authentication or authorization failure.\n"
            "  - Verify AZURE_OPENAI_ENDPOINT, AZURE_CLIENT_ID, AZURE_TENANT_ID.\n"
            "  - Confirm the identity has the 'Azure AI User' role on the Azure OpenAI resource.\n"
            "  - Check that the federated credential subject matches this workflow run.",
            file=sys.stderr,
        )
    elif exc_type == "NotFoundError" or "404" in message:
        print(
            "ERROR: Deployment not found.\n"
            "  - Confirm AZURE_OPENAI_IMAGE_DEPLOYMENT is the deployment name, not the model name.",
            file=sys.stderr,
        )
    elif exc_type == "RateLimitError" or "429" in message:
        print(
            "ERROR: Rate limit exceeded (429).\n"
            "  - Reduce request frequency or add retry logic with exponential backoff.",
            file=sys.stderr,
        )
    elif exc_type == "ContentFilterError" or "content_filter" in message.lower() or "content filter" in message.lower():
        print(
            "ERROR: Content filtered.\n"
            "  - Revise the prompt to comply with Azure OpenAI content policy.",
            file=sys.stderr,
        )
    elif "api key" in message.lower() or "authentication" in message.lower():
        print(
            "ERROR: API key authentication may be disabled for this resource.\n"
            "  - Use OIDC with Azure RBAC instead.",
            file=sys.stderr,
        )
    else:
        print(f"ERROR: Azure OpenAI API call failed ({exc_type}): {exc}", file=sys.stderr)

    sys.exit(1)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_image(b64_data: str, output_path: str) -> None:
    """Decode base64 image data and write to a file."""
    image_bytes = base64.b64decode(b64_data)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image_bytes)
    print(f"INFO: Image saved to '{output_path}' ({len(image_bytes):,} bytes).")


def download_image(url: str, output_path: str) -> None:
    """Download an image from a temporary Azure OpenAI URL."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=60) as response:
        image_bytes = response.read()
    output.write_bytes(image_bytes)
    print(f"INFO: Image downloaded to '{output_path}' ({len(image_bytes):,} bytes).")


def save_metadata(args: argparse.Namespace, result: dict, output_path: str) -> None:
    """Write generation metadata as a JSON file alongside the image."""
    metadata_path = Path(output_path).with_suffix(".json")
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt": args.prompt,
        "revised_prompt": result.get("revised_prompt"),
        "size": args.size,
        "quality": args.quality,
        "response_type": "b64_json" if result.get("b64_json") else "url",
        "output_file": output_path,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"INFO: Metadata saved to '{metadata_path}'.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings()
    client = build_client(settings)
    result = generate_image(client, settings, args)
    if result.get("b64_json"):
        save_image(result["b64_json"], args.output)
    elif result.get("url"):
        download_image(result["url"], args.output)
    else:
        print("ERROR: Image response did not include b64_json or url data.", file=sys.stderr)
        sys.exit(1)
    save_metadata(args, result, args.output)
    print("INFO: Done.")


if __name__ == "__main__":
    main()
