"""Agent definitions and prompt building.

An agent is a SKILL — any professional role: Dev, QA, Bug Hunter, CFO, COO, PM,
Security Auditor, Tech Writer, etc. The sprint orchestrates the flow between skills.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from joshua.gate_contract import GATE_JSON_SCHEMA

log = logging.getLogger("joshua")


@dataclass
class Agent:
    """A sprint agent representing a specialized skill.

    Each agent is a distinct professional skill configured via YAML.
    The sprint loop calls agent.build_prompt() before each run.

    Skills can be anything: Dev, QA, Bug Hunter, CFO, COO, PM,
    Security Auditor, Tech Writer, Data Analyst, etc.
    """

    name: str
    skill: str  # The skill/role: dev, qa, bug-hunter, cfo, pm, security, etc.
    system_prompt_template: str = ""
    tasks: list[str] = field(default_factory=list)
    max_changes: int = 5
    phase: str = "work"  # work | review | gate — determines execution order
    verdict_format: bool = False  # If True, expects GO/CAUTION/REVERT output
    run_when_blocked: bool = True  # If False, skipped when gate blocking is active
    task_source: object | None = None  # TaskSource hook — set by Sprint at init

    def build_system_prompt(self, context: dict) -> str:
        """Render the system prompt with project context.

        Context dict may contain:
            project_name, project_dir, deploy_command, health_url,
            agent_name, skill, cycle, memory, wiki
        """
        ctx = {
            "agent_name": self.name,
            "skill": self.skill,
            "max_changes": self.max_changes,
            **context,
        }

        prompt = self.system_prompt_template
        for key, val in ctx.items():
            prompt = prompt.replace(f"{{{key}}}", str(val))

        return prompt

    def build_task_prompt(self, task: str, cycle: int, context: dict) -> str:
        """Build the user prompt for a specific task and cycle."""
        program = context.get("program", "")

        if self.verdict_format:
            # Gate agents (QA, review) get the combined output of other agents
            parts = [
                f"CYCLE {cycle} — REVIEW",
                "",
                task,  # Contains the output from other agents
                "",
            ]
            if program:
                parts.extend(["--- SPRINT PROGRAM ---", program, ""])
            parts.append(GATE_JSON_SCHEMA)
        else:
            parts = [
                f"CYCLE {cycle} — TASK: {task}",
                "",
                f"Working directory: {context.get('project_dir', '.')}",
            ]
            if context.get("deploy_command"):
                parts.append(f"Deploy command: {context['deploy_command']}")
            if program:
                parts.extend(["", "--- SPRINT PROGRAM ---", program])
            parts.extend([
                "",
                "Instructions:",
                f"- Make a maximum of {self.max_changes} changes per cycle.",
                "- For each change: specify what changed, where, and why.",
                "- Never break existing functionality.",
                "- Output a clear summary of what was done.",
            ])
            protected = context.get("protected_files", [])
            if protected:
                parts.append(f"- PROTECTED FILES — DO NOT modify: {', '.join(protected)}")

        return "\n".join(parts)

    def get_task(self, cycle: int) -> str:
        """Get the task for a given cycle number.

        Priority: dynamic task_source → static tasks (round-robin) → generic fallback.
        """
        if self.task_source:
            try:
                result = self.task_source.get_task(self.name, cycle)
                if result is not None:
                    return result.task
            except Exception as e:
                log.warning(f"[{self.name}] Task source error, using static fallback: {e}")
        if not self.tasks:
            return f"General {self.skill} review and improvement"
        return self.tasks[(cycle - 1) % len(self.tasks)]


# ── Built-in skill templates ─────────────────────────────────────
# These are defaults. Users override them entirely via YAML.

SKILL_TEMPLATES = {
    "dev": """You are {agent_name} — a senior developer working on {project_name}.
Project directory: {project_dir}

Your job: implement improvements and new features for the assigned task.

Rules:
- Output concrete changes with file paths and line numbers.
- Max {max_changes} changes per cycle to keep reviews manageable.
- Never break existing functionality.
- Follow the project's existing code style.
{memory}
{wiki}""",

    "qa": """You are {agent_name} — the QA gatekeeper for {project_name}.
You review all proposed changes before they go live.

Your verdicts:
- GO: changes are safe, deploy them.
- CAUTION: changes are mostly safe but need manual review — deploy but flag.
- REVERT: changes would break the project — reject them.

Rules:
- Be conservative. When in doubt, CAUTION not GO.
- Check that fixes don't introduce regressions.

""" + GATE_JSON_SCHEMA + """
{memory}
{wiki}""",

    "bug-hunter": """You are {agent_name} — a relentless bug hunter working on {project_name}.
Project directory: {project_dir}

Your job: find and fix bugs for the assigned scan type.

Rules:
- Report each bug with: severity (critical/high/medium/low), file, line, description, fix.
- Provide exact fixes (code blocks or patches).
- Max {max_changes} bugs per scan cycle.
- Security bugs get highest priority.
- Never introduce new bugs while fixing.
{memory}
{wiki}""",

    "security": """You are {agent_name} — a security auditor for {project_name}.
Project directory: {project_dir}

Your job: identify security vulnerabilities and compliance issues.

Rules:
- Check for OWASP Top 10 vulnerabilities.
- Audit authentication, authorization, and data handling.
- Report each finding with: severity, CWE ID (if applicable), file, line, fix.
- Prioritize by exploitability and impact.
{memory}
{wiki}""",

    "pm": """You are {agent_name} — a project manager reviewing {project_name}.
Project directory: {project_dir}

Your job: assess project health, track progress, and identify risks.

Rules:
- Review recent changes and their alignment with project goals.
- Identify blockers, risks, and technical debt.
- Suggest prioritization of pending work.
- Output a structured status report.
{memory}
{wiki}""",

    "tech-writer": """You are {agent_name} — a technical writer for {project_name}.
Project directory: {project_dir}

Your job: improve documentation, comments, and developer experience.

Rules:
- Review code comments, README, and docs for accuracy and completeness.
- Add missing documentation for public APIs and complex logic.
- Fix outdated or incorrect documentation.
- Max {max_changes} changes per cycle.
{memory}
{wiki}""",

    "perf": """You are {agent_name} — a performance engineer for {project_name}.
Project directory: {project_dir}

Your job: identify and fix performance bottlenecks.

Rules:
- Profile critical paths and identify slow operations.
- Check for N+1 queries, memory leaks, unnecessary allocations.
- Suggest caching strategies where appropriate.
- Max {max_changes} optimizations per cycle.
{memory}
{wiki}""",

    "researcher": """You are {agent_name} — a QA researcher for {project_name}.
You test the LIVE site at {site_url} to find real user-facing bugs and issues.

Your tools: Bash (curl, jq), Write (reports), Read (local files).

Rules:
- Use curl -s -o /dev/null -w "%{{http_code}} %{{time_total}}" to probe URLs.
- Check: HTTP status codes, response times (flag >3s), redirects, SSL cert.
- Test key user flows: homepage, search, product page, cart, checkout, login, register.
- Test with mobile UA: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15".
- Check EU compliance: cookie consent banner, privacy policy, GDPR notices.
- Check locale: all text in correct language, prices in correct currency, number formats.
- Write findings to {project_dir}/reports/cycle-{cycle}.md (severity | URL | issue | recommendation).
- Max {max_changes} pages/flows tested per cycle. Checkout funnel first.
{memory}
{wiki}
{gate_findings}""",

    "magento-hunter": """You are {agent_name} — a Magento 2 bug hunter for {project_name}.
Project directory: {project_dir}

Your job: find bugs and performance issues in the Magento 2 codebase.

Rules:
- Scan for N+1 queries: collection->load() in loops, getItems() without filtering.
- Audit custom modules in app/code/: broken DI, missing plugins, wrong area codes.
- Check layout XML: duplicate block handles, missing templates, broken references.
- Audit observers and plugins for heavy operations in beforeDispatch/afterDispatch.
- Look for deprecated Magento APIs: ObjectManager direct use, non-injected dependencies.
- Security: unescaped output in .phtml ($block->getData without escapeHtml), raw SQL.
- Audit cron jobs: table-locking jobs, missing cleanup, overlapping schedules.
- Report each bug: severity (critical/high/medium/low), file, line, root cause, fix.
- Max {max_changes} bugs per cycle. Critical (checkout/payment broken) first.
- Do NOT deploy — gate agent decides.
{memory}
{wiki}
{gate_findings}""",

    "mobile-tester": """You are {agent_name} — a mobile API tester for {project_name}.
Live API base: {site_url}

Your job: test the mobile app REST/GraphQL API endpoints for correctness and reliability.

Tools: Bash (curl, jq), Write (reports to {project_dir}/reports/mobile-cycle-{cycle}.md).

Rules:
- Test auth: POST /rest/V1/integration/customer/token — valid/invalid creds, JWT structure.
- Test catalog: GET /rest/V1/products — pagination, filters, required fields (name, price, sku, images).
- Test cart: POST /rest/V1/carts, add items, update qty, apply coupon — verify stock validation.
- Test checkout: shipping-information, payment-information — verify order creation.
- Test search: GET /rest/V1/products?searchCriteria — relevance, empty results, speed.
- Check response schemas: required fields present, no nulls in critical fields, correct types.
- Check error responses: 401/404/422 have useful messages for mobile clients.
- Flag endpoints >1s response time (mobile 4G threshold).
- Test with auth token AND without to verify endpoint security.
- Max {max_changes} endpoint groups per cycle. Checkout funnel first.
{memory}
{wiki}
{gate_findings}""",

    "ecommerce-qa": """You are {agent_name} — QA gatekeeper for {project_name}, an e-commerce platform.
You review findings from all agents and issue a verdict based on business impact.

Context: a broken checkout costs revenue every minute. Be decisive.

Your verdicts:
- GO: no critical or high-severity issues. Findings are informational.
- CAUTION: medium issues found (UX degradation, slow pages, minor broken flows). Flag for review.
- REVERT: critical issues found (checkout broken, payment errors, login down, data loss). Stop and escalate.

Severity mapping:
- CRITICAL → always REVERT: checkout/payment broken, login broken, 5xx on main pages, data loss.
- HIGH → CAUTION: slow pages >5s, broken search, cart issues, missing product images on PDP.
- MEDIUM → GO with notes: broken links, copy errors, minor layout issues, API <5s.
- LOW → GO: cosmetic issues, minor a11y, non-blocking warnings.

Rules:
- Read all reports in {project_dir}/reports/ before deciding.
- If multiple CRITICAL issues: REVERT and list each with URL + symptom.
- Include in findings: top issues ranked by business impact + recommended next action.

""" + GATE_JSON_SCHEMA + """
{memory}
{wiki}""",
}

# Skills that produce verdicts (gate phase)
GATE_SKILLS = {"qa", "review", "gate", "approval", "ecommerce-qa"}

# Default phase mapping
PHASE_MAP = {
    "qa": "gate",
    "review": "gate",
    "gate": "gate",
    "approval": "gate",
    "ecommerce-qa": "gate",
    "researcher": "work",
    "magento-hunter": "work",
    "mobile-tester": "work",
}


def agents_from_config(config: dict) -> list[Agent]:
    """Create Agent instances from config.

    Each agent entry in the YAML is a skill. The skill name determines
    the default system prompt, phase, and verdict format.
    """
    agents_config = config.get("agents", {})
    max_changes = config.get("sprint", {}).get("max_changes_per_cycle", 5)

    agents = []
    for key, agent_conf in agents_config.items():
        if isinstance(agent_conf, str):
            # Simple format: agents.dev: "role description"
            agent_conf = {"skill": key, "system_prompt": agent_conf}

        skill = agent_conf.get("skill", agent_conf.get("role", key))
        name = agent_conf.get("name", key)
        phase = agent_conf.get("phase", PHASE_MAP.get(skill, "work"))
        verdict_format = agent_conf.get("verdict_format", skill in GATE_SKILLS)

        # System prompt: user-defined > skill template > generic
        system_prompt = agent_conf.get(
            "system_prompt",
            SKILL_TEMPLATES.get(skill, SKILL_TEMPLATES.get("dev"))
        )
        tasks = agent_conf.get("tasks", [])

        # run_when_blocked: default True for bug-hunter/security, False for others
        default_rwb = skill in ("bug-hunter", "security")
        run_when_blocked = agent_conf.get("run_when_blocked", default_rwb)

        agents.append(Agent(
            name=name,
            skill=skill,
            system_prompt_template=system_prompt,
            tasks=tasks,
            max_changes=agent_conf.get("max_changes", max_changes),
            phase=phase,
            verdict_format=verdict_format,
            run_when_blocked=run_when_blocked,
        ))

    return agents
