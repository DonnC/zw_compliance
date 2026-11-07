"""The agent skills state facts, so tie them to the repository: a path or rule
code that stops existing must fail here, not mislead the next developer."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from zw_compliance.fdms import errors

ROOT = Path(__file__).resolve().parents[3]
SKILLS = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
NAMES = {skill.parent.name for skill in SKILLS}
ERROR_CODES = set(re.findall(r"\b(?:RCPT|DEV|FISC|FILE)\d{2,3}\b", Path(errors.__file__).read_text()))


def test_skills_exist():
	assert len(SKILLS) >= 6


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_frontmatter(skill):
	text = skill.read_text()
	head = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)
	assert head, "needs name and description"
	assert head[1] == skill.parent.name
	assert len(head[2]) <= 1024


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_repository_paths_exist(skill):
	for path in re.findall(r"`((?:docs|zw_compliance)/[^`\s<*]+)`", skill.read_text()):
		assert (ROOT / path).exists(), path


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_rule_codes_exist(skill):
	unknown = set(re.findall(r"\b(?:RCPT|DEV|FISC|FILE)\d{2,3}\b", skill.read_text())) - ERROR_CODES
	# §8.2.1 has no RCPT044-046; the API skill says so.
	assert not unknown - {"RCPT044", "RCPT046"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_links_between_skills_resolve(skill):
	assert set(re.findall(r"\[\[([a-z0-9-]+)\]\]", skill.read_text())) <= NAMES


def test_agents_lists_every_skill():
	agents = (ROOT / "AGENTS.md").read_text()
	assert all(f"`{name}`" in agents for name in NAMES)
