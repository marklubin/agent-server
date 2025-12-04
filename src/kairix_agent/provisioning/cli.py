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

from kairix_agent.config import Config
from kairix_agent.provisioning.agents import (
    AgentDefinition,
    create_conversational_agent,
    create_reflector_agent,
)
from kairix_agent.provisioning.blocks import BlockDefinition  # noqa: TC001

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


async def find_agent_by_name(
    client: AsyncLetta,
    name: str,
) -> tuple[str, set[str], set[str]] | None:
    """Find an existing agent by name.

    Returns:
        Tuple of (agent_id, set of block labels, set of archive ids) if found, None otherwise.
    """
    async for agent in client.agents.list(name=name):
        if agent.name == name:
            # Get attached blocks via dedicated endpoint
            # (agents.retrieve().memory.blocks is broken in SDK - returns empty)
            block_labels: set[str] = set()
            async for block in client.agents.blocks.list(agent_id=agent.id):
                if block.label:
                    block_labels.add(block.label)
                    logger.debug("  Found existing block: %s", block.label)

            # Get attached archives
            archive_ids: set[str] = set()
            async for archive in client.archives.list(agent_id=agent.id):
                archive_ids.add(archive.id)

            return agent.id, block_labels, archive_ids
    return None


async def provision_agent(
    client: AsyncLetta,
    definition: AgentDefinition,
    existing_blocks: dict[str, BlockResponse],
    archive_id: str | None = None,
    shared_block_ids: dict[str, str] | None = None,
) -> str:
    """Provision an agent based on its definition. Returns agent ID.

    If an agent with the same name exists, validates and remediates its configuration
    instead of creating a duplicate.

    Args:
        client: Letta client.
        definition: Agent definition.
        existing_blocks: Dict of label -> BlockResponse for all existing blocks.
        archive_id: Optional archive ID to attach.
        shared_block_ids: Optional dict of label -> block_id for shared blocks.
            Used by reflector agents to attach the same blocks as the conversational agent.
    """
    # Check if agent already exists
    existing = await find_agent_by_name(client, definition.name)

    if existing:
        agent_id, existing_block_labels, existing_archive_ids = existing
        logger.info("Found existing agent: %s (%s)", definition.name, agent_id)
        return await _remediate_existing_agent(
            client,
            agent_id,
            definition,
            existing_blocks,
            existing_block_labels,
            existing_archive_ids,
            archive_id,
            shared_block_ids,
        )

    # Agent doesn't exist - create new
    return await _create_new_agent(client, definition, existing_blocks, archive_id, shared_block_ids)


async def _remediate_existing_agent(
    client: AsyncLetta,
    agent_id: str,
    definition: AgentDefinition,
    existing_blocks: dict[str, BlockResponse],
    existing_block_labels: set[str],
    existing_archive_ids: set[str],
    archive_id: str | None,
    shared_block_ids: dict[str, str] | None = None,
) -> str:
    """Remediate an existing agent's configuration.

    Checks for missing blocks and archives, attaches them if needed.
    """
    from letta_client import ConflictError

    # Use no-retry client for attach operations (409 Conflict is expected, not retryable)
    no_retry_client = client.with_options(max_retries=0)

    # Check for missing blocks
    required_labels = {b.label for b in definition.shared_blocks} | {
        b.label for b in definition.unique_blocks
    }
    missing_labels = required_labels - existing_block_labels
    shared_labels = {b.label for b in definition.shared_blocks}

    if missing_labels:
        logger.info("Agent missing blocks: %s", missing_labels)

        for block_def in [*definition.shared_blocks, *definition.unique_blocks]:
            if block_def.label in missing_labels:
                # For shared blocks, prefer the explicit shared_block_ids mapping
                if block_def.label in shared_labels and shared_block_ids and block_def.label in shared_block_ids:
                    block_id = shared_block_ids[block_def.label]
                    logger.info("  Attaching shared block: %s (%s)", block_def.label, block_id)
                elif block_def.label in existing_blocks:
                    block_id = existing_blocks[block_def.label].id
                    logger.info("  Attaching existing block: %s (%s)", block_def.label, block_id)
                else:
                    block = await client.blocks.create(
                        label=block_def.label,
                        value=block_def.initial_value,
                        description=block_def.description,
                        limit=block_def.limit,
                        read_only=block_def.read_only,
                    )
                    block_id = block.id
                    logger.info("  Created and attaching block: %s (%s)", block_def.label, block_id)

                # Attach block to agent (handle already-attached case)
                try:
                    await no_retry_client.agents.blocks.attach(agent_id=agent_id, block_id=block_id)
                except ConflictError:
                    logger.info("  Block %s already attached (conflict ignored)", block_def.label)
    else:
        logger.info("All required blocks present")

    # Check archive attachment
    if archive_id and archive_id not in existing_archive_ids:
        logger.info("Attaching missing archive %s...", archive_id)
        try:
            await no_retry_client.agents.archives.attach(archive_id=archive_id, agent_id=agent_id)
            logger.info("  Archive attached successfully")
        except ConflictError:
            logger.info("  Archive already attached (conflict ignored)")
    elif archive_id:
        logger.info("Archive already attached")

    logger.info("Agent remediation complete: %s", agent_id)
    return agent_id


async def _create_new_agent(
    client: AsyncLetta,
    definition: AgentDefinition,
    existing_blocks: dict[str, BlockResponse],
    archive_id: str | None,
    shared_block_ids: dict[str, str] | None = None,
) -> str:
    """Create a new agent from scratch."""
    logger.info("Provisioning new agent: %s", definition.name)

    # Collect block IDs
    block_ids: list[str] = []

    # Shared blocks - use explicit IDs if provided, otherwise reuse existing or create
    logger.info("Setting up shared blocks...")
    for block_def in definition.shared_blocks:
        if shared_block_ids and block_def.label in shared_block_ids:
            # Use the exact block ID from the conversational agent
            block_id = shared_block_ids[block_def.label]
            logger.info("  Using shared block: %s (%s)", block_def.label, block_id)
        else:
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
    logger.info("Creating agent with %d blocks (include_base_tools=%s)...", len(block_ids), definition.include_base_tools)
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
        include_base_tools=definition.include_base_tools,
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


async def get_conversational_agent_shared_blocks(
    client: AsyncLetta,
    base_name: str,
    shared_block_labels: set[str],
) -> dict[str, str]:
    """Get the block IDs for shared blocks from the conversational agent.

    For reflector agents, we need to attach the SAME blocks (by ID) that the
    conversational agent uses, not just blocks with the same label.

    Args:
        client: Letta client.
        base_name: The base agent name (e.g., "Corindel").
        shared_block_labels: Set of labels to look for (e.g., {"persona", "human"}).

    Returns:
        Dict mapping label -> block_id for the shared blocks.
    """
    # Find the conversational agent by name
    async for agent in client.agents.list(name=base_name):
        if agent.name == base_name:
            shared_blocks: dict[str, str] = {}
            async for block in client.agents.blocks.list(agent_id=agent.id):
                if block.label in shared_block_labels:
                    shared_blocks[block.label] = block.id
                    logger.info(
                        "  Found shared block %s (%s) from conversational agent",
                        block.label,
                        block.id,
                    )
            return shared_blocks
    return {}


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

    # Handle archive and shared blocks based on agent type
    archive_id: str | None = None
    shared_block_ids: dict[str, str] | None = None

    if agent_type == "conversational":
        # For conversational agent: create a new archive with the agent's name
        logger.info("Setting up shared archive...")
        archive_id = await find_or_create_archive(client, base_name, existing_archives)
    else:
        # For reflector agent: find and attach the conversational agent's archive and blocks
        logger.info("Looking for conversational agent's archive...")
        archive_id = await find_conversational_agent_archive(client, base_name)
        if not archive_id:
            logger.error(
                "Cannot provision reflector: conversational agent '%s' not found "
                "or has no archive. Provision the conversational agent first.",
                base_name,
            )
            return 1

        # Get the shared block IDs from the conversational agent
        logger.info("Looking for conversational agent's shared blocks...")
        shared_labels = {b.label for b in definition.shared_blocks}
        shared_block_ids = await get_conversational_agent_shared_blocks(
            client, base_name, shared_labels
        )
        if not shared_block_ids:
            logger.error(
                "Cannot provision reflector: conversational agent '%s' has no shared blocks.",
                base_name,
            )
            return 1

    agent_id = await provision_agent(
        client, definition, existing_blocks, archive_id, shared_block_ids
    )
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
