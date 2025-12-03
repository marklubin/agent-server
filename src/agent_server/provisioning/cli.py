"""CLI for provisioning Kairix agents.

Usage:
    uv run provision-agent --type conversational --name Corindel
    uv run provision-agent --type reflector --name Corindel
    uv run provision-agent --list-blocks
    uv run provision-agent --list-archives
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from letta_client import AsyncLetta

from agent_server.config import Config
from agent_server.provisioning.agents import (
    AgentDefinition,
    create_conversational_agent,
    create_reflector_agent,
)
from agent_server.provisioning.blocks import BlockDefinition  # noqa: TC001

if TYPE_CHECKING:
    from letta_client.types import BlockResponse
    from letta_client.types.archive import Archive

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def find_or_create_block(
    client: AsyncLetta,
    block_def: BlockDefinition,
    existing_blocks: dict[str, BlockResponse],
) -> str:
    """Find existing block by label or create new one. Returns block ID."""
    if block_def.label in existing_blocks:
        block = existing_blocks[block_def.label]
        logger.info("  Using existing block: %s (%s)", block_def.label, block.id)
        return block.id

    # Create new block
    block = await client.blocks.create(
        label=block_def.label,
        value=block_def.initial_value,
        description=block_def.description,
        limit=block_def.limit,
        read_only=block_def.read_only,
    )
    logger.info("  Created new block: %s (%s)", block_def.label, block.id)
    return block.id


async def provision_agent(
    client: AsyncLetta,
    definition: AgentDefinition,
    existing_blocks: dict[str, BlockResponse],
    archive_id: str | None = None,
) -> str:
    """Provision an agent based on its definition. Returns agent ID."""
    logger.info("Provisioning agent: %s", definition.name)

    # Collect block IDs
    block_ids: list[str] = []

    # Shared blocks - reuse existing or create
    logger.info("Setting up shared blocks...")
    for block_def in definition.shared_blocks:
        block_id = await find_or_create_block(client, block_def, existing_blocks)
        block_ids.append(block_id)

    # Unique blocks - always create fresh
    logger.info("Setting up unique blocks...")
    for block_def in definition.unique_blocks:
        block = await client.blocks.create(
            label=block_def.label,
            value=block_def.initial_value,
            description=block_def.description,
            limit=block_def.limit,
            read_only=block_def.read_only,
        )
        logger.info("  Created unique block: %s (%s)", block_def.label, block.id)
        block_ids.append(block.id)

    # Create the agent
    logger.info("Creating agent with %d blocks...", len(block_ids))
    agent = await client.agents.create(
        name=definition.name,
        description=definition.description,
        system=definition.system_prompt,
        model=definition.model,
        embedding=definition.embedding,
        context_window_limit=definition.context_window,
        enable_reasoner=definition.enable_reasoner,
        max_tokens=definition.max_tokens,
        max_reasoning_tokens=definition.max_reasoning_tokens,
        block_ids=block_ids,
        include_base_tools=True,
    )

    logger.info("Agent created: %s (%s)", definition.name, agent.id)

    # Attach archive if provided
    if archive_id:
        logger.info("Attaching archive %s to agent...", archive_id)
        await client.agents.archives.attach(archive_id=archive_id, agent_id=agent.id)
        logger.info("  Archive attached successfully")

    return agent.id


async def list_blocks(client: AsyncLetta) -> None:
    """List all existing blocks."""
    logger.info("Existing blocks:")
    async for block in client.blocks.list():
        logger.info("  - %s (%s): %d chars", block.label, block.id, len(block.value))


async def list_agents(client: AsyncLetta) -> None:
    """List all existing agents."""
    logger.info("Existing agents:")
    async for agent in client.agents.list():
        logger.info("  - %s (%s)", agent.name, agent.id)


async def list_archives(client: AsyncLetta) -> None:
    """List all existing archives."""
    logger.info("Existing archives:")
    async for archive in client.archives.list():
        logger.info("  - %s (%s)", archive.name, archive.id)


async def find_or_create_archive(
    client: AsyncLetta,
    name: str,
    existing_archives: dict[str, Archive],
) -> str:
    """Find existing archive by name or create new one. Returns archive ID."""
    if name in existing_archives:
        archive = existing_archives[name]
        logger.info("  Using existing archive: %s (%s)", name, archive.id)
        return archive.id

    # Create new archive
    archive = await client.archives.create(
        name=name,
        description=f"Shared archival memory for {name} entity",
        embedding="openai/text-embedding-3-small",
    )
    logger.info("  Created new archive: %s (%s)", name, archive.id)
    return archive.id


async def find_conversational_agent_archive(
    client: AsyncLetta,
    base_name: str,
) -> str | None:
    """Find the archive attached to the conversational agent.

    Args:
        client: Letta client.
        base_name: The base agent name (e.g., "Corindel").

    Returns:
        Archive ID if found, None otherwise.
    """
    # Find the conversational agent by name
    async for agent in client.agents.list(name=base_name):
        # Get archives attached to this agent
        async for archive in client.archives.list(agent_id=agent.id):
            logger.info(
                "  Found archive %s (%s) from conversational agent %s",
                archive.name,
                archive.id,
                agent.name,
            )
            return archive.id
    return None


async def _run_provisioning(
    client: AsyncLetta,
    agent_type: str,
    base_name: str,
) -> int:
    """Run agent provisioning logic.

    Args:
        client: Letta client.
        agent_type: Type of agent ("conversational" or "reflector").
        base_name: Base name for the agent.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    # Get all existing blocks for reuse
    existing_blocks: dict[str, BlockResponse] = {}
    async for block in client.blocks.list():
        if block.label:
            existing_blocks[block.label] = block

    # Get all existing archives for reuse
    existing_archives: dict[str, Archive] = {}
    async for archive in client.archives.list():
        if archive.name:
            existing_archives[archive.name] = archive

    # Create definition using factory methods
    if agent_type == "conversational":
        definition = create_conversational_agent(base_name)
    else:
        definition = create_reflector_agent(base_name)

    # Handle archive based on agent type
    archive_id: str | None = None

    if agent_type == "conversational":
        # For conversational agent: create a new archive with the agent's name
        logger.info("Setting up shared archive...")
        archive_id = await find_or_create_archive(client, base_name, existing_archives)
    else:
        # For reflector agent: find and attach the conversational agent's archive
        logger.info("Looking for conversational agent's archive...")
        archive_id = await find_conversational_agent_archive(client, base_name)
        if not archive_id:
            logger.error(
                "Cannot provision reflector: conversational agent '%s' not found "
                "or has no archive. Provision the conversational agent first.",
                base_name,
            )
            return 1

    agent_id = await provision_agent(client, definition, existing_blocks, archive_id)
    logger.info("Done! Agent ID: %s", agent_id)

    return 0


async def main() -> int:
    """Main entry point for provisioning CLI."""
    parser = argparse.ArgumentParser(description="Provision Kairix agents")
    parser.add_argument(
        "--type",
        choices=["conversational", "reflector"],
        help="Type of agent to provision",
    )
    parser.add_argument(
        "--name",
        required=False,
        help="Base agent name (required when provisioning)",
    )
    parser.add_argument(
        "--list-blocks",
        action="store_true",
        help="List all existing blocks",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="List all existing agents",
    )
    parser.add_argument(
        "--list-archives",
        action="store_true",
        help="List all existing archives",
    )
    parser.add_argument(
        "--letta-url",
        default=Config.LETTA_BASE_URL.value,
        help="Letta server URL",
    )

    args = parser.parse_args()

    client = AsyncLetta(base_url=args.letta_url)

    if args.list_blocks:
        await list_blocks(client)
        return 0

    if args.list_agents:
        await list_agents(client)
        return 0

    if args.list_archives:
        await list_archives(client)
        return 0

    if not args.type:
        parser.error("--type is required when provisioning an agent")

    if not args.name:
        parser.error("--name is required when provisioning an agent")

    return await _run_provisioning(client, args.type, args.name)


def cli() -> None:
    """Entry point for the CLI."""
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
