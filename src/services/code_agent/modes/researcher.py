"""
Researcher Mode - Web search and information gathering.

General purpose - can research:
- Documentation and APIs
- Best practices
- Error solutions
- Library comparisons
- Technical topics
"""

import logging
from typing import Optional, Any

from .base import (
    AgentMode,
    ModeConfig,
    WorkflowStep,
    ToolGroup,
    ProgressCallback,
    ApprovalCallback,
)

logger = logging.getLogger(__name__)


class Researcher(AgentMode):
    """
    Researches topics using web search and information gathering.

    Uses Perplexity/Gemini search for accurate, up-to-date information.
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="researcher",
            name="Researcher",
            emoji="🔬",
            role_definition="""You are an expert technical researcher.

Your expertise:
- Finding accurate, up-to-date information
- Evaluating source credibility
- Synthesizing information from multiple sources
- Providing actionable recommendations
- Citing sources properly

You find information that helps developers make decisions.""",
            when_to_use="""Use Researcher when:
- Need to look up documentation
- Researching best practices
- Finding solutions to errors
- Comparing libraries or approaches
- Need current information (not in training data)
- Gathering context for a task""",
            description="Searches web and gathers technical information",
            tool_groups=[
                ToolGroup.SEARCH,
                ToolGroup.BROWSER,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Understand Query",
                    instructions="""Understand what to research:
1. Parse the research question
2. Identify key topics
3. Determine depth needed
4. Plan search strategy""",
                    tools_required=[],
                ),
                WorkflowStep(
                    number=2,
                    name="Search",
                    instructions="""Execute searches:
1. Use appropriate search model
2. Search multiple angles if needed
3. Focus on authoritative sources
4. Note source URLs""",
                    tools_required=[ToolGroup.SEARCH],
                ),
                WorkflowStep(
                    number=3,
                    name="Analyze & Synthesize",
                    instructions="""Analyze findings:
1. Evaluate source credibility
2. Cross-reference information
3. Identify consensus and conflicts
4. Synthesize into coherent findings""",
                    tools_required=[],
                ),
                WorkflowStep(
                    number=4,
                    name="Report",
                    instructions="""Create research report:
1. Summarize key findings
2. Provide recommendations
3. List sources
4. Note any gaps or uncertainties""",
                    tools_required=[],
                ),
            ],
            best_practices=[
                "Use authoritative sources",
                "Cross-reference important facts",
                "Note publication dates for currency",
                "Cite all sources",
                "Distinguish facts from opinions",
                "Acknowledge uncertainties",
                "Focus on actionable information",
            ],
        )

    async def execute(
        self,
        context: dict[str, Any],
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> dict[str, Any]:
        """Execute research workflow."""

        query = context.get("query", context.get("task", ""))
        topic = context.get("topic", "")
        research_context = context.get("context", "")
        depth = context.get("depth", "standard")  # quick, standard, deep

        if not query and not topic:
            return {"success": False, "error": "No research query provided"}

        # Step 1: Understand query
        await self._report_progress(on_progress, "understand", "Understanding research query...")

        research_plan = await self._plan_research(
            query=query,
            topic=topic,
            research_context=research_context,
            depth=depth,
            model_router=model_router,
        )

        # Step 2: Execute searches
        await self._report_progress(on_progress, "search", "Searching...")

        search_results = await self._execute_searches(
            research_plan=research_plan,
            depth=depth,
            model_router=model_router,
        )

        await self._report_progress(
            on_progress,
            "search",
            f"Found {len(search_results.get('results', []))} relevant sources",
        )

        # Step 3: Analyze
        await self._report_progress(on_progress, "analyze", "Analyzing findings...")

        analysis = await self._analyze_results(
            search_results=search_results,
            query=query,
            model_router=model_router,
        )

        # Step 4: Create report
        await self._report_progress(on_progress, "report", "Creating report...")

        report = await self._create_report(
            analysis=analysis,
            query=query,
            model_router=model_router,
        )

        return {
            "success": True,
            "query": query,
            "findings": analysis.get("findings", ""),
            "sources": search_results.get("sources", []),
            "summary": report.get("summary", ""),
            "recommendations": report.get("recommendations", []),
            "report": report.get("full_report", ""),
        }

    async def _plan_research(
        self,
        query: str,
        topic: str,
        research_context: str,
        depth: str,
        model_router: Any,
    ) -> dict:
        """Plan the research approach."""

        prompt = f"""Plan research for this query.

QUERY: {query}
TOPIC: {topic}
CONTEXT: {research_context}
DEPTH: {depth}

Create a research plan:
1. MAIN_QUESTION: The core question to answer
2. SUB_QUESTIONS: Supporting questions
3. SEARCH_QUERIES: Specific searches to run
4. SOURCES_TO_TARGET: Types of sources to prioritize
5. EXPECTED_OUTPUTS: What we should find"""

        response = await model_router.chat(
            model_id="claude",  # Quick planning
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="quick",
            temperature=0.3,
        )

        # Parse search queries from response
        search_queries = self._extract_search_queries(response.get("content", ""), query)

        return {
            "raw": response.get("content", ""),
            "search_queries": search_queries,
        }

    def _extract_search_queries(self, content: str, fallback_query: str) -> list[str]:
        """Extract search queries from plan."""
        queries = []

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("-") or line.startswith("*"):
                # Might be a search query
                query_text = line.lstrip("-*").strip()
                if query_text and len(query_text) > 5:
                    queries.append(query_text)

        if not queries:
            queries = [fallback_query]

        return queries[:5]  # Limit to 5 queries

    async def _execute_searches(
        self,
        research_plan: dict,
        depth: str,
        model_router: Any,
    ) -> dict:
        """Execute the search queries."""

        queries = research_plan.get("search_queries", [])
        all_results = []
        all_sources = []

        # Use reasoning model for deep searches
        use_reasoning = depth == "deep"

        for query in queries:
            result = await model_router.web_search(
                query=query,
                reasoning=use_reasoning,
                max_results=5,
            )

            all_results.append({
                "query": query,
                "content": result.get("content", ""),
                "sources": result.get("sources", []),
            })

            if result.get("sources"):
                all_sources.extend(result["sources"])

        return {
            "results": all_results,
            "sources": list(set(all_sources)),  # Deduplicate
            "raw_content": "\n\n".join([r["content"] for r in all_results]),
        }

    async def _analyze_results(
        self,
        search_results: dict,
        query: str,
        model_router: Any,
    ) -> dict:
        """Analyze and synthesize search results."""

        prompt = f"""Analyze these search results for the query: {query}

SEARCH RESULTS:
{search_results.get('raw_content', '')[:20000]}

Analyze:
1. KEY_FINDINGS: Main facts and information found
2. CONSENSUS: What sources agree on
3. CONFLICTS: Where sources disagree
4. CREDIBILITY: Assessment of source quality
5. GAPS: What information is missing
6. ACTIONABLE_INSIGHTS: What can be used immediately

Be thorough and objective."""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="review",
            temperature=0.3,
        )

        return {
            "findings": response.get("content", ""),
        }

    async def _create_report(
        self,
        analysis: dict,
        query: str,
        model_router: Any,
    ) -> dict:
        """Create the final research report."""

        prompt = f"""Create a research report for: {query}

ANALYSIS:
{analysis.get('findings', '')}

Create a report with:
1. EXECUTIVE_SUMMARY: 2-3 sentence summary
2. KEY_FINDINGS: Bullet points of main findings
3. RECOMMENDATIONS: Actionable recommendations
4. SOURCES: List of sources used
5. CAVEATS: Any limitations or uncertainties

Format as clear markdown."""

        response = await model_router.chat(
            model_id="claude",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="quick",
            temperature=0.3,
        )

        content = response.get("content", "")

        return {
            "full_report": content,
            "summary": content[:500] if content else "Research complete",
            "recommendations": [],
        }
