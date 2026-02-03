#!/usr/bin/env python3

import asyncio
import logging
from typing import Optional, List
from dataclasses import dataclass, field
import time

from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import BaseModel
import uvicorn

from src.config import config
from src.context import session_manager, ConversationSession
from src.services.pollinations import pollinations_client
from src.logging_config import setup_logging

setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Polly HTTP Bot", description="HTTP interface for Polly bot")

class MessageRequest(BaseModel):
    content: str
    user_id: int
    user_name: str
    image_urls: Optional[List[str]] = None
    video_urls: Optional[List[str]] = None
    file_urls: Optional[List[str]] = None
    thread_id: Optional[int] = None
    channel_id: Optional[int] = None

class MessageResponse(BaseModel):
    thread_id: int
    response: str
    status: str

class SessionState(BaseModel):
    thread_id: int
    topic_summary: str
    message_count: int
    participants: List[int]
    created_at: float
    last_activity: float

class ListSessionsResponse(BaseModel):
    sessions: List[SessionState]
    count: int

@app.on_event("startup")
async def startup_event():
    logger.info("HTTP Bot starting...")
    config.validate()
    
    for name, handler in get_tool_handlers().items():
        pollinations_client.register_tool_handler(name, handler)
    logger.info(f"Registered {len(get_tool_handlers())} tool handlers")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("HTTP Bot shutting down...")
    await pollinations_client.close()

def get_tool_handlers():
    from src.services.github import TOOL_HANDLERS
    from src.services.code_agent.tools import TOOL_HANDLERS as CODE_AGENT_HANDLERS
    
    handlers = dict(TOOL_HANDLERS)
    handlers.update(CODE_AGENT_HANDLERS)
    
    if config.local_embeddings_enabled:
        from src.bot import _code_search_handler
        handlers["code_search"] = _code_search_handler
    
    from src.services.pollinations import web_search_handler, web_handler
    handlers["web_search"] = web_search_handler
    handlers["web"] = web_handler
    
    from src.services.web_scraper import web_scrape_handler
    handlers["web_scrape"] = web_scrape_handler
    
    from src.services.discord_search import tool_discord_search
    handlers["discord_search"] = tool_discord_search
    
    return handlers

async def process_with_tools(
    text: str,
    session: ConversationSession,
    user_id: int,
    user_name: str,
    image_urls: Optional[List[str]] = None,
    video_urls: Optional[List[str]] = None,
    file_urls: Optional[List[str]] = None,
) -> str:
    
    image_urls = image_urls or []
    video_urls = video_urls or []
    file_urls = file_urls or []
    
    session_manager.add_to_session(
        session=session,
        role="user",
        content=text,
        author=user_name,
        author_id=user_id,
        image_urls=image_urls + video_urls,
    )
    
    conversation_history = session.get_conversation_history()
    
    system_prompt = f"""You are Polly, a helpful assistant integrated with GitHub and code development tools.
Current thread: {session.topic_summary}
Participants: {', '.join(session.get_all_participants_names())}

You have access to tools for:
- Creating and managing GitHub issues
- Searching code and repositories
- Running code and tasks
- Web search and scraping
- Discord search within the community

Always be helpful, clear, and proactive in using tools to assist the user."""
    
    try:
        response = await pollinations_client.process_with_tools(
            messages=conversation_history,
            system_prompt=system_prompt,
        )
        
        session_manager.add_to_session(
            session=session,
            role="assistant",
            content=response,
            author="Polly",
            author_id=0,
        )
        
        return response
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")

@app.post("/message", response_model=MessageResponse)
async def send_message(request: MessageRequest) -> MessageResponse:
    channel_id = request.channel_id or 1
    
    if request.thread_id:
        session = session_manager.get_session(request.thread_id)
        if not session:
            raise HTTPException(status_code=404, detail="Thread not found")
        thread_id = request.thread_id
    else:
        topic = pollinations_client.get_topic_summary_fast(request.content)
        session = session_manager.create_session(
            channel_id=channel_id,
            thread_id=int(time.time() * 1000),
            user_id=request.user_id,
            user_name=request.user_name,
            initial_message=request.content,
            topic_summary=topic,
            image_urls=request.image_urls or [],
        )
        thread_id = session.thread_id
    
    response = await process_with_tools(
        text=request.content,
        session=session,
        user_id=request.user_id,
        user_name=request.user_name,
        image_urls=request.image_urls,
        video_urls=request.video_urls,
        file_urls=request.file_urls,
    )
    
    return MessageResponse(
        thread_id=thread_id,
        response=response,
        status="success"
    )

@app.get("/sessions", response_model=ListSessionsResponse)
async def list_sessions() -> ListSessionsResponse:
    sessions = session_manager.sessions
    
    session_list = []
    for thread_id, session in sessions.items():
        session_list.append(SessionState(
            thread_id=session.thread_id,
            topic_summary=session.topic_summary,
            message_count=session.message_count(),
            participants=list(session.participants),
            created_at=session.created_at,
            last_activity=session.last_activity,
        ))
    
    return ListSessionsResponse(
        sessions=session_list,
        count=len(session_list)
    )

@app.get("/session/{thread_id}")
async def get_session_info(thread_id: int):
    session = session_manager.get_session(thread_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "thread_id": session.thread_id,
        "topic_summary": session.topic_summary,
        "message_count": session.message_count(),
        "user_message_count": session.user_message_count(),
        "participants": list(session.participants),
        "created_at": session.created_at,
        "last_activity": session.last_activity,
        "is_expired": session.is_expired(),
        "messages": [
            {
                "role": msg.role,
                "author": msg.author,
                "content": msg.content[:200],
                "timestamp": msg.timestamp,
            }
            for msg in session.messages[-10:]
        ]
    }

@app.delete("/session/{thread_id}")
async def delete_session(thread_id: int):
    if thread_id not in session_manager.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_manager.sessions.pop(thread_id, None)
    return {"status": "deleted", "thread_id": thread_id}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "bot_name": config.bot_name,
        "active_sessions": len(session_manager.sessions),
    }

if __name__ == "__main__":
    logger.info("Starting Polly HTTP Bot...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
