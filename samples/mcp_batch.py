"""Call a frozen local server through the real MCP stdio protocol.

Run with this checkout's .venv Python. Requests are a JSON array of
{"tool": "name", "args": {...}, "save_json": "optional/output.json"}.
Relative save_json paths resolve from the calling working directory; tool
arguments should use absolute paths because the server runs in the engine root.
The result log, protocol JSONL, and server stderr preserve execution evidence.
Binary/base64 response payloads are summarized, never printed or logged raw.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engine_hashes(root):
    return {str(p.relative_to(root)): sha256(p)
            for folder in ("src", "addon", "guidelines")
            for p in sorted((root / folder).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix.lower() not in (".pyc", ".pyo")}


def safe_payload(value):
    if isinstance(value, dict):
        binary = value.get("type") in ("image", "audio")
        clean = {}
        for key, item in value.items():
            if isinstance(item, str) and (key.endswith("base64") or key == "blob" or (key == "data" and binary)):
                clean[key] = {"omitted_binary": True, "encoded_characters": len(item),
                              "sha256_encoded": hashlib.sha256(item.encode()).hexdigest()}
            else:
                clean[key] = safe_payload(item)
        return clean
    if isinstance(value, list):
        return [safe_payload(item) for item in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(safe_payload(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProtocolLog:
    def __init__(self, stream, direction, log, started):
        self.stream, self.direction, self.log, self.started = stream, direction, log, started

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def record(self, item):
        if isinstance(item, Exception):
            payload = {"transport_error": str(item)}
        else:
            payload = item.message.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.log.write(json.dumps({"elapsed_seconds": round(time.monotonic() - self.started, 6),
                                   "direction": self.direction, "message": safe_payload(payload)},
                                  ensure_ascii=False) + "\n")
        self.log.flush()

    async def send(self, item):
        self.record(item)
        await self.stream.send(item)

    async def receive(self):
        item = await self.stream.receive()
        self.record(item)
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def aclose(self):
        await self.stream.aclose()

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)


def decoded_json(result):
    parsed = []
    for content in result.get("content", []):
        if content.get("type") == "text":
            try:
                parsed.append(json.loads(content["text"]))
            except (ValueError, KeyError):
                pass
    if len(parsed) == 1:
        return parsed[0]
    if not parsed and result.get("structuredContent") is not None:
        return result["structuredContent"]
    raise ValueError(f"save_json requires one JSON response; received {len(parsed)} JSON text blocks")


async def run(args, requests):
    started = time.monotonic()
    server_args = ["-m", "free_drawing_mcp.server", "--backend", "headless", "--only-text-feedback"]
    # Deliberately use this interpreter, not uv or an auto-selected/RPC backend.
    env = dict(os.environ, PYTHONPATH=str(args.engine_root / "src"),
               PYTHONUTF8="1", FREE_DRAWING_BACKEND="headless")
    params = StdioServerParameters(command=sys.executable, args=server_args,
                                   cwd=str(args.engine_root), env=env)
    evidence = {"protocol": "MCP stdio JSON-RPC", "command": [sys.executable] + server_args,
                "engine_root": str(args.engine_root), "requests_file": str(args.requests),
                "requests_sha256": sha256(args.requests),
                "engine_before": engine_hashes(args.engine_root), "calls": [], "status": "running"}
    write_json(args.out, evidence)
    try:
        with args.protocol.open("x", encoding="utf-8") as protocol, args.stderr.open("x", encoding="utf-8") as stderr:
            async with stdio_client(params, errlog=stderr) as (read, write):
                async with ClientSession(ProtocolLog(read, "server_to_client", protocol, started),
                                         ProtocolLog(write, "client_to_server", protocol, started),
                                         read_timeout_seconds=900) as session:
                    initialized = await session.initialize()
                    evidence["initialize"] = initialized.model_dump(mode="json", by_alias=True, exclude_none=True)
                    tools = await session.list_tools()
                    available = {tool.name for tool in tools.tools}
                    evidence["available_tools"] = sorted(available)
                    for index, request in enumerate(requests, 1):
                        call_started = time.monotonic()
                        call = {"index": index, "request": request, "status": "running"}
                        evidence["calls"].append(call)
                        write_json(args.out, evidence)
                        try:
                            if request["tool"] not in available:
                                raise ValueError(f"server did not advertise {request['tool']}")
                            response = await session.call_tool(request["tool"], request.get("args", {}),
                                                               read_timeout_seconds=900)
                            result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
                            call["result"] = result
                            texts = [content.get("text", "") for content in result.get("content", [])
                                     if content.get("type") == "text"]
                            if result.get("isError") or any(re.search(r"(?mi)^\s*ERROR\b", text) for text in texts):
                                raise RuntimeError("MCP tool returned an error; see the saved response")
                            if request["tool"] == "drawing_status" and any("status call failed:" in text for text in texts):
                                raise RuntimeError("FreeCAD backend status failed")
                            if request.get("save_json"):
                                destination = Path(request["save_json"]).resolve()
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                write_json(destination, decoded_json(result))
                                call["saved_json"] = str(destination)
                            call["status"] = "ok"
                        except Exception:
                            call["status"] = "error"
                            call["error"] = traceback.format_exc()
                        call["seconds"] = round(time.monotonic() - call_started, 3)
                        write_json(args.out, evidence)
                        print(f"{index}/{len(requests)} {request['tool']}: {call['status']} ({call['seconds']}s)", flush=True)
                        if call["status"] != "ok":
                            break
        evidence["status"] = "ok" if len(evidence["calls"]) == len(requests) and all(
            call["status"] == "ok" for call in evidence["calls"]) else "error"
    except Exception:
        evidence["status"] = "error"
        evidence["error"] = traceback.format_exc()
    finally:
        evidence["engine_after"] = engine_hashes(args.engine_root)
        evidence["engine_unchanged"] = evidence["engine_before"] == evidence["engine_after"]
        evidence["seconds"] = round(time.monotonic() - started, 3)
        if not evidence["engine_unchanged"]:
            evidence["status"] = "error"
        write_json(args.out, evidence)
    return int(evidence["status"] != "ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.engine_root, args.requests, args.out = (p.resolve() for p in (args.engine_root, args.requests, args.out))
    args.protocol = args.out.with_name(args.out.name + ".protocol.jsonl")
    args.stderr = args.out.with_name(args.out.name + ".stderr.txt")
    if not (args.engine_root / "src" / "free_drawing_mcp" / "server.py").is_file():
        parser.error("engine root must contain src/free_drawing_mcp/server.py")
    try:
        requests = json.loads(args.requests.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        parser.error(f"cannot read requests: {error}")
    if not isinstance(requests, list) or not requests:
        parser.error("requests must be a nonempty JSON array")
    save_paths = [args.out, args.protocol, args.stderr]
    for request in requests:
        if not isinstance(request, dict) or not isinstance(request.get("tool"), str) or not isinstance(request.get("args", {}), dict):
            parser.error("each request needs a tool string and an args object")
        if request.get("save_json"):
            save_paths.append(Path(request["save_json"]).resolve())
    if len(set(save_paths)) != len(save_paths) or any(path.exists() for path in save_paths):
        parser.error("evidence/save_json paths must be unique and must not already exist")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run(args, requests))


if __name__ == "__main__":
    raise SystemExit(main())
