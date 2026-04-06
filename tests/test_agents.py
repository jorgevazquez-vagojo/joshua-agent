"""Tests for agent definitions and skill system."""

import pytest

from joshua.agents import Agent, agents_from_config, SKILL_TEMPLATES, GATE_SKILLS


class TestAgent:
    def test_basic_creation(self):
        agent = Agent(name="dev1", skill="dev")
        assert agent.name == "dev1"
        assert agent.skill == "dev"
        assert agent.phase == "work"
        assert agent.verdict_format is False

    def test_gate_agent(self):
        agent = Agent(name="qa1", skill="qa", phase="gate", verdict_format=True)
        assert agent.phase == "gate"
        assert agent.verdict_format is True

    def test_get_task_round_robin(self):
        agent = Agent(name="dev", skill="dev", tasks=["a", "b", "c"])
        assert agent.get_task(0) == "a"
        assert agent.get_task(1) == "b"
        assert agent.get_task(2) == "c"
        assert agent.get_task(3) == "a"  # wraps around
        assert agent.get_task(100) == "b"  # 100 % 3 == 1

    def test_get_task_empty_list(self):
        agent = Agent(name="dev", skill="dev", tasks=[])
        task = agent.get_task(0)
        assert "dev" in task.lower()

    def test_build_system_prompt_interpolation(self):
        agent = Agent(
            name="lightman",
            skill="dev",
            system_prompt_template="I am {agent_name}, a {skill} for {project_name}.",
        )
        result = agent.build_system_prompt({"project_name": "MyApp"})
        assert result == "I am lightman, a dev for MyApp."

    def test_build_task_prompt_work(self):
        agent = Agent(name="dev", skill="dev", max_changes=3)
        prompt = agent.build_task_prompt("fix bugs", 5, {"project_dir": "/app"})
        assert "CYCLE 5" in prompt
        assert "fix bugs" in prompt
        assert "/app" in prompt
        assert "3 changes" in prompt

    def test_build_task_prompt_gate(self):
        agent = Agent(name="qa", skill="qa", verdict_format=True)
        prompt = agent.build_task_prompt("report content here", 5, {})
        # JSON contract: prompt must instruct the gate agent to output a JSON block
        assert "```json" in prompt
        assert '"verdict"' in prompt
        assert "GO" in prompt
        assert "CAUTION" in prompt
        assert "REVERT" in prompt
        assert "report content here" in prompt


class TestAgentsFromConfig:
    def test_minimal_config(self):
        config = {
            "agents": {
                "dev": {"skill": "dev", "tasks": ["task1"]},
                "qa": {"skill": "qa"},
            }
        }
        agents = agents_from_config(config)
        assert len(agents) == 2

        dev = next(a for a in agents if a.name == "dev")
        assert dev.skill == "dev"
        assert dev.phase == "work"
        assert dev.verdict_format is False

        qa = next(a for a in agents if a.name == "qa")
        assert qa.skill == "qa"
        assert qa.phase == "gate"
        assert qa.verdict_format is True

    def test_custom_skill(self):
        config = {
            "agents": {
                "cfo": {
                    "skill": "cfo",
                    "system_prompt": "You are a CFO.",
                    "tasks": ["audit costs"],
                }
            }
        }
        agents = agents_from_config(config)
        assert len(agents) == 1
        assert agents[0].skill == "cfo"
        assert agents[0].phase == "work"
        assert "CFO" in agents[0].system_prompt_template

    def test_custom_name(self):
        config = {
            "agents": {
                "dev": {"skill": "dev", "name": "lightman"},
            }
        }
        agents = agents_from_config(config)
        assert agents[0].name == "lightman"
        assert agents[0].skill == "dev"

    def test_role_as_alias_for_skill(self):
        config = {
            "agents": {
                "builder": {"role": "dev"},
            }
        }
        agents = agents_from_config(config)
        assert agents[0].skill == "dev"

    def test_all_builtin_skills_have_templates(self):
        for skill in SKILL_TEMPLATES:
            template = SKILL_TEMPLATES[skill]
            assert "{agent_name}" in template
            assert len(template) > 50

    def test_gate_skills_set(self):
        assert "qa" in GATE_SKILLS
        assert "review" in GATE_SKILLS
        assert "dev" not in GATE_SKILLS

    def test_max_changes_from_sprint_config(self):
        config = {
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_changes_per_cycle": 7},
        }
        agents = agents_from_config(config)
        assert agents[0].max_changes == 7

    def test_max_changes_per_agent_override(self):
        config = {
            "agents": {"dev": {"skill": "dev", "max_changes": 2}},
            "sprint": {"max_changes_per_cycle": 7},
        }
        agents = agents_from_config(config)
        assert agents[0].max_changes == 2

    def test_run_when_blocked_default_bug_hunter(self):
        config = {"agents": {"bh": {"skill": "bug-hunter"}}}
        agents = agents_from_config(config)
        assert agents[0].run_when_blocked is True

    def test_run_when_blocked_default_dev(self):
        config = {"agents": {"dev": {"skill": "dev"}}}
        agents = agents_from_config(config)
        assert agents[0].run_when_blocked is False

    def test_run_when_blocked_override(self):
        config = {"agents": {"dev": {"skill": "dev", "run_when_blocked": True}}}
        agents = agents_from_config(config)
        assert agents[0].run_when_blocked is True
