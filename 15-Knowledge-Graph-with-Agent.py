"""
Knowledge Graph Agent (Agno + Graphiti + FalkorDB + Ollama)
===========================================================

Fully local temporal knowledge-graph memory:
- Agno agent orchestrates, calls Graphiti through two tools.
- Graphiti extracts entities/relations with an Ollama LLM (OpenAI-compatible /v1 API).
- nomic-embed-text embeddings, FalkorDB as the graph store.

Prereqs:
    podman run -d --name falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb   # UI: http://localhost:3000
    ollama pull qwen3.5-4b-16k nomic-embed-text
"""

import asyncio
from datetime import datetime, timezone
from functools import partial

from agno.agent import Agent
from agno.models.ollama import Ollama
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EpisodeType
from openai import AsyncOpenAI

LLM_MODEL = "qwen3.5-4b-16k:latest"
EMBED_MODEL = "nomic-embed-text:latest"
OLLAMA_V1 = "http://localhost:11434/v1"
GROUP_ID = "agno_kg"  # FalkorDB: each Graphiti group_id is stored as its own graph

# ---------------------------------------------------------------------------
# Graphiti on FalkorDB, every model served by Ollama (api_key is a dummy)
# ---------------------------------------------------------------------------
llm_config = LLMConfig(api_key="ollama", model=LLM_MODEL, small_model=LLM_MODEL, base_url=OLLAMA_V1)

ollama_client = AsyncOpenAI(api_key="ollama", base_url=OLLAMA_V1)
# Graphiti has no knob for this; without it qwen3.5 "thinks" ~8 min per extraction call vs <1s
ollama_client.chat.completions.create = partial(
    ollama_client.chat.completions.create, reasoning_effort="none"
)

graphiti = Graphiti(
    graph_driver=FalkorDriver(host="localhost", port=6379, database=GROUP_ID),
    llm_client=OpenAIGenericClient(config=llm_config, client=ollama_client),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="ollama", embedding_model=EMBED_MODEL, embedding_dim=768, base_url=OLLAMA_V1
        )
    ),
    cross_encoder=OpenAIRerankerClient(config=llm_config),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
async def save_episode(name: str, content: str) -> str:
    """Store a fact, note or conversation snippet in the knowledge graph.

    Args:
        name: Short title for the episode.
        content: The text to remember.
    """
    await graphiti.add_episode(
        name=name,
        episode_body=content,
        source=EpisodeType.text,
        source_description="agno agent",
        reference_time=datetime.now(timezone.utc),
        group_id=GROUP_ID,
    )
    return f"Saved episode '{name}'."


async def search_memory(query: str) -> str:
    """Search the knowledge graph for facts and relationships.

    Args:
        query: What to look up.
    """
    edges = await graphiti.search(query, group_ids=[GROUP_ID], num_results=10)
    if not edges:
        return "No matching facts."
    return "\n".join(
        f"- {e.fact} (valid_from={e.valid_at}, invalid_at={e.invalid_at})" for e in edges
    )


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=Ollama(id=LLM_MODEL, request_params={"think": False}),
    tools=[save_episode, search_memory],
    instructions=[
        "You have long-term memory in a temporal knowledge graph.",
        "When the user tells you facts, call save_episode.",
        "Before answering questions about people, projects or past facts, call search_memory.",
        "Answer only from retrieved facts; say so if nothing is found.",
    ],
    markdown=True,
)


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
async def main():
    await graphiti.build_indices_and_constraints()
    try:
        await agent.aprint_response(
            "Remember: Ashish works at Opulence Group as an AI engineer. "
            "He is building a tax-compliance agent using Agno and Graphiti."
        )
        await agent.aprint_response("What is Ashish building, and with which tools?")
    finally:
        await graphiti.close()


if __name__ == "__main__":
    asyncio.run(main())
