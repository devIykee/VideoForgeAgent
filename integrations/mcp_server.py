"""MCP (Model Context Protocol) server for MinecraftCast (optional layer).

Lets MCP clients — Claude Desktop, Cursor, and others — call MinecraftCast as a
tool. Exposes a single ``generate_minecraft_video`` tool that runs the core
pipeline and returns the local video path.

Run: ``python integrations/mcp_server.py``
"""

import json
import uuid
import asyncio

from dotenv import load_dotenv

load_dotenv()

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import Tool, TextContent  # noqa: E402

from pipeline import run as run_pipeline  # noqa: E402
from config import VideoConfig, CharacterConfig  # noqa: E402

server = Server("minecraftcast")


@server.list_tools()
async def list_tools():
    """Advertise the single video-generation tool."""
    return [Tool(
        name="generate_minecraft_video",
        description=(
            "Generate a Minecraft faceless YouTube video with two AI characters "
            "having a dialogue over Minecraft gameplay footage. Returns video path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic":             {"type": "string"},
                "char1_name":        {"type": "string", "default": "Alex"},
                "char1_personality": {"type": "string"},
                "char2_name":        {"type": "string", "default": "Steve"},
                "char2_personality": {"type": "string"},
                "duration_minutes":  {"type": "number", "default": 3.0},
                "footage_type":      {"type": "string", "default": "survival gameplay"},
            },
            "required": ["topic", "char1_personality", "char2_personality"],
        },
    )]


@server.call_tool()
async def call_tool(name, arguments):
    """Handle a tool invocation. Runs the pipeline for generate_minecraft_video."""
    if name != "generate_minecraft_video":
        raise ValueError(f"Unknown tool: {name}")

    config = VideoConfig(
        topic=arguments["topic"],
        char1=CharacterConfig(
            name=arguments.get("char1_name", "Alex"),
            personality=arguments["char1_personality"],
            voice_provider="elevenlabs",
            avatar_skin="alex", shirt_color="#6AA84F",
        ),
        char2=CharacterConfig(
            name=arguments.get("char2_name", "Steve"),
            personality=arguments["char2_personality"],
            voice_provider="elevenlabs",
            avatar_skin="steve", shirt_color="#3B6BB5",
        ),
        duration_minutes=arguments.get("duration_minutes", 3.0),
        footage_source="youtube",
        footage_type=arguments.get("footage_type", "survival gameplay"),
        job_id=str(uuid.uuid4()),
    )
    video_path = await run_pipeline(config)
    return [TextContent(type="text", text=json.dumps({"video_path": video_path}))]


async def main() -> None:
    """Serve the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
