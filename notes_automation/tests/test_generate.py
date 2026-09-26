from upsc_notes.generate import postprocess, remove_known_bullets, select_existing_context, unverified_numbers


def test_postprocess_cleans_model_output():
    raw = (
        "```markdown\n# A title\n\n### Why in News\n* ISRO tested the crew module in 2026.\n\n"
        "### Challenges\n- Not mentioned in the source.\n\n### Way Forward\n\n### Key Facts\n- Crew of 3.\n```"
    )
    out = postprocess(raw, level=2)
    assert out.startswith("## Why in News")
    assert "- ISRO tested the crew module in 2026." in out
    assert "Challenges" not in out and "Way Forward" not in out  # emptied sections dropped
    assert "## Key Facts" in out and "# A title" not in out


def test_postprocess_update_level_and_keeps_none_of():
    out = postprocess("## Key Updates\n- None of the States objected to the plan.\n", level=3)
    assert out.startswith("### Key Updates")
    assert "None of the States" in out  # only whole-line "None"/"N/A" is treated as empty


def test_unverified_numbers():
    src = "Bilateral trade crossed US$326.61 million in FY 2025–26, a 55% growth. The 1,23,456 figure."
    note = "- Trade: US$326.61 million (FY 2025–26), growth 55%.\n- Target 2030 and 123456 units; 5 pillars."
    assert unverified_numbers(note, src) == ["2030"]


def test_remove_known_bullets():
    existing = "## Gaganyaan\n- Gaganyaan is India's human spaceflight programme led by ISRO.\n"
    update = "### Key Updates\n- Gaganyaan is India's human spaceflight programme led by ISRO.\n- The G1 flight splashed down in 2026 in the Bay of Bengal.\n"
    out, removed = remove_known_bullets(update, existing)
    assert removed == 1
    assert "G1 flight" in out and "led by ISRO" not in out


def test_select_existing_context_keeps_relevant_sections():
    body = "# T\n\n## Intro\nintro text\n\n" + "".join(f"## Section {i}\n" + ("filler words " * 200) + "\n\n" for i in range(10))
    body += "## Death penalty\nrarest of rare doctrine Bachan Singh\n"
    ctx = select_existing_context(body, "Bachan Singh rarest of rare death penalty verdict", max_chars=4000)
    assert "Bachan Singh" in ctx and "Outline of the full note" in ctx
    assert len(ctx) < 7000
