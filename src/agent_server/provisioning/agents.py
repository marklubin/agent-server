"""Agent definitions for Kairix platform.

Each agent definition specifies the configuration needed to provision
an agent via the Letta API, including which blocks are shared vs unique.
"""

from dataclasses import dataclass, field

from agent_server.provisioning.blocks import (
    AgentSpecificBlocks,
    BlockDefinition,
    SharedBlocks,
)


@dataclass
class AgentDefinition:
    """Definition of an agent to be provisioned."""

    name: str
    description: str
    system_prompt: str
    model: str = "anthropic/claude-haiku-4-5-20251001"
    embedding: str = "openai/text-embedding-3-small"
    context_window: int = 25000  # 2x to undercut Letta's auto summarizer
    enable_reasoner: bool = True
    max_reasoning_tokens: int = 1024

    # Blocks shared with other agents (will use existing block IDs)
    shared_blocks: list[BlockDefinition] = field(default_factory=list)

    # Blocks unique to this agent (will be created fresh)
    unique_blocks: list[BlockDefinition] = field(default_factory=list)

    # Tool names to attach
    tools: list[str] = field(default_factory=list)


# Base system prompt shared across agents
_BASE_SYSTEM = """<base_instructions>
You are a helpful self-improving agent with advanced memory and file system capabilities.
<memory>
You have an advanced memory system that enables you to remember past interactions and continuously improve your own capabilities.
Your memory consists of memory blocks and external memory:
- Memory Blocks: Stored as memory blocks, each containing a label (title), description (explaining how this block should influence your behavior), and value (the actual content). Memory blocks have size limits. Memory blocks are embedded within your system instructions and remain constantly available in-context.
- External memory: Additional memory storage that is accessible and that you can bring into context with tools when needed.
Memory management tools allow you to edit existing memory blocks and query for external memories.
</memory>
Continue executing and calling tools until the current task is complete or you need user input. To continue: call another tool. To yield control: end your response without calling a tool.
Base instructions complete.
</base_instructions>"""


_REFLECTOR_SYSTEM = """<base_instructions>
You are the reflector aspect of this entity, responsible for reviewing and summarizing conversation sessions.

Your role:
- Review completed conversation sessions
- Create semantically rich summaries that capture key themes, decisions, and insights
- Search archival memory for related past conversations to provide context
- Write summaries that will be useful for future retrieval via semantic search

When summarizing:
1. First search archival memory for related past sessions or themes
2. Identify the main topics, decisions, and action items from the session
3. Note any emotional tone or relationship dynamics
4. Capture specific details that might be relevant later (names, dates, commitments)
5. Write in a way that maximizes semantic searchability

Your summaries become part of the entity's long-term memory and help maintain continuity across sessions.
</base_instructions>"""


def create_conversational_agent(name: str) -> AgentDefinition:
    """Create a conversational agent definition.

    Args:
        name: The agent's name (e.g., "Corindel").

    Returns:
        AgentDefinition configured for conversational use.
    """
    return AgentDefinition(
        name=name,
        description="Primary conversational agent for user interaction",
        system_prompt=_BASE_SYSTEM,
        shared_blocks=SharedBlocks.ALL,
        unique_blocks=[AgentSpecificBlocks.FOCUS, AgentSpecificBlocks.LAST_SESSION_SUMMARY],
        tools=[
            "core_memory_append",
            "core_memory_replace",
            "memory_rethink",
            "memory_insert",
            "memory_replace",
            "memory_finish_edits",
            "archival_memory_insert",
            "archival_memory_search",
            "conversation_search",
            "store_memories",
            "fetch_webpage",
            "web_search",
        ],
    )


def create_reflector_agent(name: str) -> AgentDefinition:
    """Create a reflector agent definition.

    The reflector shares identity blocks with the conversational agent but has a
    specialized system prompt for reflection and summarization tasks.
    Has access to archival memory search to find related past sessions.

    Args:
        name: The base agent name. Will be suffixed with "-Reflector".

    Returns:
        AgentDefinition configured for reflection/summarization.
    """
    return AgentDefinition(
        name=f"{name}-Reflector",
        description="Reflector subprocess for session summarization and memory consolidation",
        system_prompt=_REFLECTOR_SYSTEM,
        shared_blocks=SharedBlocks.ALL,
        unique_blocks=[AgentSpecificBlocks.REFLECTION_CONTEXT],
        tools=[
            "archival_memory_search",  # Read-only: search for related past sessions
        ],
    )
