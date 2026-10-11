from funding_tracker.http import RobotsRules

ROBOTS = """﻿User-agent: *
Crawl-delay: 20

User-agent: *
Allow: /
Disallow: /*?*
Disallow: /*page/*
Allow: /*?*utm_*
Disallow: /private/
Disallow: /*.php$

User-agent: SomeBot
Disallow: /
"""


def test_wildcards_and_longest_match():
    rules = RobotsRules(ROBOTS)
    assert rules.allowed("https://example.com/buzz/a-story/")
    assert not rules.allowed("https://example.com/tag/funding/page/2/")
    assert not rules.allowed("https://example.com/?s=funding")
    assert rules.allowed("https://example.com/a-story/?x=1&utm_source=feed")     # the longer Allow wins
    assert not rules.allowed("https://example.com/private/report")
    assert not rules.allowed("https://example.com/index.php")
    assert rules.allowed("https://example.com/index.php5")                       # "$" anchors the end


def test_groups_for_the_same_agent_are_merged_and_bom_is_ignored():
    rules = RobotsRules(ROBOTS)
    assert rules.crawl_delay == 20
    assert len(rules.rules) == 6


def test_own_group_wins_over_the_wildcard_group():
    assert not RobotsRules(ROBOTS, ua_token="somebot").allowed("https://example.com/anything")


def test_empty_robots_allows_everything():
    assert RobotsRules("").allowed("https://example.com/x")
