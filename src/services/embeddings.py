"""Local embeddings service for semantic code search.

Uses Jina Embeddings v2 Base Code + ChromaDB for fully local code search.
Only active when LOCAL_EMBEDDINGS_ENABLED=true in .env
"""
import asyncio
import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_model = None
_chroma_client = None
_collection = None

DATA_DIR = Path(__file__).parent.parent.parent / "data"
REPO_DIR = DATA_DIR / "repo"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".vue",
    ".svelte",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".mdx",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".dockerfile",
    ".tf",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "bin",
    "obj",
    ".idea",
    ".vscode",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
}

MAX_FILE_SIZE = 500 * 1024

UPDATE_DEBOUNCE_SECONDS = 30
_pending_update_task: Optional[asyncio.Task] = None
_update_lock = asyncio.Lock()


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading Jina embeddings model (first time may download ~500MB)...")
        _model = SentenceTransformer(
            "jinaai/jina-embeddings-v2-base-code", trust_remote_code=True
        )
        logger.info("Embedding model loaded")
    return _model


def _get_collection():
    global _chroma_client, _collection
    if _collection is None:
        import chromadb

        EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(EMBEDDINGS_DIR))
        _collection = _chroma_client.get_or_create_collection(
            name="code_embeddings", metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"ChromaDB collection loaded with {_collection.count()} embeddings")
    return _collection


def _chunk_code(content: str, file_path: str, max_lines: int = 100) -> list[dict]:
    lines = content.split("\n")

    if len(lines) <= max_lines:
        return [
            {
                "content": content,
                "file_path": file_path,
                "start_line": 1,
                "end_line": len(lines),
            }
        ]

    chunks = []
    current_chunk = []
    chunk_start = 1

    for i, line in enumerate(lines, 1):
        current_chunk.append(line)

        is_break = len(current_chunk) >= max_lines or (
            len(current_chunk) >= 20 and _is_definition_start(line)
        )

        if is_break and current_chunk:
            chunks.append(
                {
                    "content": "\n".join(current_chunk),
                    "file_path": file_path,
                    "start_line": chunk_start,
                    "end_line": i,
                }
            )
            current_chunk = []
            chunk_start = i + 1

    if current_chunk:
        chunks.append(
            {
                "content": "\n".join(current_chunk),
                "file_path": file_path,
                "start_line": chunk_start,
                "end_line": len(lines),
            }
        )

    return chunks


def _is_definition_start(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("def ")
        or stripped.startswith("class ")
        or stripped.startswith("async def ")
        or stripped.startswith("function ")
        or stripped.startswith("const ")
        or stripped.startswith("export ")
        or stripped.startswith("pub fn ")
        or stripped.startswith("fn ")
        or stripped.startswith("func ")
    )


def _file_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


async def clone_or_pull_repo(repo: str) -> bool:
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = REPO_DIR / repo.replace("/", "_")

    try:
        if repo_path.exists():
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(repo_path), "fetch", "origin", "main"],
                capture_output=True,
                text=True,
            )

            local = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            remote = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(repo_path), "rev-parse", "origin/main"],
                capture_output=True,
                text=True,
            )

            if local.stdout.strip() == remote.stdout.strip():
                logger.debug("Repo already up to date")
                return False

            logger.info(f"Pulling latest changes for {repo}...")
            await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(repo_path), "pull", "origin", "main"],
                capture_output=True,
                text=True,
            )
            return True
        else:
            logger.info(f"Cloning {repo}...")
            await asyncio.to_thread(
                subprocess.run,
                [
                    "git",
                    "clone",
                    "--depth=1",
                    f"https://github.com/{repo}.git",
                    str(repo_path),
                ],
                capture_output=True,
                text=True,
            )
            return True

    except Exception as e:
        logger.error(f"Git operation failed: {e}")
        return False


async def get_changed_files(repo: str) -> list[str]:
    repo_path = REPO_DIR / repo.replace("/", "_")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(repo_path), "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return [f for f in result.stdout.strip().split("\n") if f]
    except Exception as e:
        logger.error(f"Failed to get changed files: {e}")

    return []


def _collect_code_files(repo_path: Path) -> list[Path]:
    files = []

    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in filenames:
            file_path = Path(root) / filename

            if file_path.suffix.lower() not in CODE_EXTENSIONS:
                continue

            try:
                if file_path.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            files.append(file_path)

    return files


async def embed_repository(repo: str, force_full: bool = False) -> int:
    repo_path = REPO_DIR / repo.replace("/", "_")

    if not repo_path.exists():
        logger.error(f"Repo not found at {repo_path}")
        return 0

    model = _get_model()
    collection = _get_collection()

    files = _collect_code_files(repo_path)
    logger.info(f"Found {len(files)} code files to process")

    embedded_count = 0
    all_ids = []
    all_embeddings = []
    all_documents = []
    all_metadatas = []

    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            rel_path = str(file_path.relative_to(repo_path))

            chunks = _chunk_code(content, rel_path)

            for chunk in chunks:
                chunk_id = f"{rel_path}:{chunk['start_line']}-{chunk['end_line']}"
                content_hash = _file_hash(chunk["content"])

                if not force_full:
                    existing = collection.get(ids=[chunk_id])
                    if existing["ids"] and existing["metadatas"]:
                        if existing["metadatas"][0].get("hash") == content_hash:
                            continue

                embedding = await asyncio.to_thread(model.encode, chunk["content"])

                all_ids.append(chunk_id)
                all_embeddings.append(embedding.tolist())
                all_documents.append(chunk["content"])
                all_metadatas.append(
                    {
                        "file_path": rel_path,
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                        "hash": content_hash,
                    }
                )

                embedded_count += 1

        except Exception as e:
            logger.warning(f"Failed to process {file_path}: {e}")
            continue

    if all_ids:
        collection.upsert(
            ids=all_ids,
            embeddings=all_embeddings,
            documents=all_documents,
            metadatas=all_metadatas,
        )
        logger.info(f"Embedded {embedded_count} chunks")

    return embedded_count


async def search_code(query: str, top_k: int = 5) -> list[dict]:
    model = _get_model()
    collection = _get_collection()

    if collection.count() == 0:
        return []

    query_embedding = await asyncio.to_thread(model.encode, query)

    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    formatted = []
    for i, doc in enumerate(results["documents"][0]):
        metadata = results["metadatas"][0][i]
        distance = results["distances"][0][i]

        formatted.append(
            {
                "file_path": metadata["file_path"],
                "start_line": metadata["start_line"],
                "end_line": metadata["end_line"],
                "content": doc,
                "similarity": round(1 - distance, 3),
            }
        )

    return formatted


async def pull_and_update():
    from ..config import config

    async with _update_lock:
        logger.info("Updating repository and embeddings...")

        had_changes = await clone_or_pull_repo(config.embeddings_repo)

        if had_changes:
            count = await embed_repository(config.embeddings_repo)
            logger.info(f"Update complete. Embedded {count} new/changed chunks.")

            await _sync_sandbox_repo()
        else:
            logger.info("No changes detected")


async def _sync_sandbox_repo():
    try:
        from .code_agent.sandbox import get_persistent_sandbox

        sandbox = get_persistent_sandbox()

        if await sandbox.is_running():
            logger.info("Syncing sandbox workspace with updated repo...")
            await sandbox.sync_repo(force=True)
            logger.info("Sandbox workspace synced successfully")
        else:
            logger.info("Sandbox not running, skipping sync (will sync on next task)")

    except Exception as e:
        logger.warning(f"Failed to sync sandbox repo: {e}")


async def schedule_update():
    global _pending_update_task

    if _pending_update_task and not _pending_update_task.done():
        _pending_update_task.cancel()
        try:
            await _pending_update_task
        except asyncio.CancelledError:
            pass

    async def _delayed_update():
        await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
        await pull_and_update()

    _pending_update_task = asyncio.create_task(_delayed_update())
    logger.debug(f"Update scheduled in {UPDATE_DEBOUNCE_SECONDS}s")


async def initialize():
    from ..config import config

    if not config.local_embeddings_enabled:
        logger.info("Local embeddings disabled")
        return

    logger.info(f"Initializing embeddings for {config.embeddings_repo}...")

    await clone_or_pull_repo(config.embeddings_repo)

    collection = _get_collection()
    if collection.count() == 0:
        logger.info("No existing embeddings found, running full embed...")
        await embed_repository(config.embeddings_repo, force_full=True)
    else:
        logger.info(f"Found {collection.count()} existing embeddings")


def get_stats() -> dict:
    collection = _get_collection()
    return {
        "total_chunks": collection.count(),
        "repo_dir": str(REPO_DIR),
        "embeddings_dir": str(EMBEDDINGS_DIR),
    }


async def close():
    global _model, _chroma_client, _collection, _pending_update_task

    if _pending_update_task and not _pending_update_task.done():
        _pending_update_task.cancel()
        try:
            await _pending_update_task
        except asyncio.CancelledError:
            pass

    _model = None
    _collection = None
    _chroma_client = None
    logger.info("Embeddings service closed")

