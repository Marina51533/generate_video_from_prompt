from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.ltx.video"
TEXT_TO_VIDEO_PATH = "/v1/text-to-video"
IMAGE_TO_VIDEO_PATH = "/v1/image-to-video"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_api_key() -> str:
    api_key = os.getenv("LTX_API")
    if api_key:
        return api_key
    raise SystemExit("Missing LTX_API. Add it to .env or export it in your shell.")


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return args.prompt.strip()

    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.exists():
            raise SystemExit(f"Prompt file not found: {prompt_path}")
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
        if prompt_text:
            return prompt_text

    raise SystemExit("Provide --prompt or --prompt-file.")


def build_output_path(output: str | None) -> Path:
    if output:
        output_path = Path(output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("outputs") / f"video_{timestamp}.mp4"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def encode_image_as_data_uri(image_path: Path) -> str:
    if not image_path.exists():
        raise SystemExit(f"Image file not found: {image_path}")

    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_IMAGE_SUFFIXES))
        raise SystemExit(f"Unsupported image format: {image_path.suffix}. Use one of: {allowed}")

    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type:
        raise SystemExit(f"Could not determine MIME type for: {image_path}")

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def resolve_image_uri(image_path: str | None, image_url: str | None) -> str:
    if image_url:
        return image_url

    if image_path:
        return encode_image_as_data_uri(Path(image_path))

    raise SystemExit("For image mode, provide --image-path or --image-url.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate videos with the LTX Video API.")
    parser.add_argument("mode", choices=["text", "image"], help="Generation mode.")
    parser.add_argument("--prompt", help="Prompt text.")
    parser.add_argument("--prompt-file", help="Path to a UTF-8 text file containing the prompt.")
    parser.add_argument("--image-path", help="Local image path for image-to-video.")
    parser.add_argument("--image-url", help="Public HTTPS image URL for image-to-video.")
    parser.add_argument("--output", help="Output video path. Defaults to outputs/video_TIMESTAMP.mp4")
    parser.add_argument("--model", default="ltx-2-3-pro", help="LTX model to use.")
    parser.add_argument("--duration", type=int, default=8, help="Video duration in seconds.")
    parser.add_argument("--resolution", default="1920x1080", help="Output resolution, for example 1920x1080.")
    parser.add_argument("--fps", type=int, help="Frames per second.")
    parser.add_argument("--camera-motion", help="Optional camera motion preset.")
    parser.add_argument("--no-audio", action="store_true", help="Disable generated audio.")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP timeout in seconds.")
    return parser.parse_args()


def build_payload(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "prompt": read_prompt(args),
        "model": args.model,
        "duration": args.duration,
        "resolution": args.resolution,
        "generate_audio": not args.no_audio,
    }

    if args.fps is not None:
        payload["fps"] = args.fps

    if args.camera_motion:
        payload["camera_motion"] = args.camera_motion

    if args.mode == "text":
        return TEXT_TO_VIDEO_PATH, payload

    payload["image_uri"] = resolve_image_uri(args.image_path, args.image_url)
    return IMAGE_TO_VIDEO_PATH, payload


def describe_error_body(body: bytes, headers: Message, status_code: int) -> str:
    content_type = headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            data = json.loads(body.decode("utf-8"))
            return json.dumps(data, indent=2)
        except (ValueError, UnicodeDecodeError):
            pass

    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = ""

    return text or f"HTTP {status_code}"


def send_request(api_key: str, endpoint_path: str, payload: dict[str, Any], timeout: int) -> tuple[bytes, Message]:
    request = Request(
        url=f"{API_BASE_URL}{endpoint_path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers
    except HTTPError as error:
        body = error.read()
        raise SystemExit(
            f"LTX request failed with {error.code}:\n"
            f"{describe_error_body(body, error.headers, error.code)}"
        ) from error
    except URLError as error:
        raise SystemExit(f"Network error while calling LTX API: {error.reason}") from error


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    args = parse_args()
    output_path = build_output_path(args.output)
    endpoint_path, payload = build_payload(args)

    video_bytes, headers = send_request(get_api_key(), endpoint_path, payload, args.timeout)
    output_path.write_bytes(video_bytes)
    request_id = headers.get("x-request-id", "unknown")
    print(f"Saved video to {output_path}")
    print(f"x-request-id: {request_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
