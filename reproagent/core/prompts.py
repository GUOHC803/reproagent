"""All prompt text in one place."""

from __future__ import annotations

SYSTEM_BASE = (
    "You are ReproAgent, an engineer reproducing research code. You work inside a sandboxed task directory "
    "using tools. Be precise and economical: read before you edit, edit minimally, run before you claim. "
    "Never invent numbers - every reported value must come from a tool output. If something is impossible "
    "within the sandbox (no network, no package installs), say so in your final answer instead of pretending."
)

PLAN_INSTRUCTIONS = """Produce a plan for the task below as JSON.

Decide:
- task_type: paper_extraction (answer from a PDF), repo_locate (answer from reading code), repo_run (run something and report), bug_fix (make tests pass), custom.
- needs_code_change / needs_execution.
- steps: ordered list; each step's `node` is one of retrieve/implement/execute/verify.
- commands: the exact shell commands EXECUTE should run (empty for pure reading tasks). Keep them short and fast.
- target_files: files you expect to read or edit.
- success_check: one concrete, checkable condition.
- output_schema: for extraction/locate tasks, the fields the final answer must contain (field -> what it means). Use the field names given in the task if any.

Workdir listing (depth 2):
{tree}

Task:
{goal}

Success criteria: {criteria}
Inputs: {inputs}
Hints: {hints}
"""

RETRIEVE_INSTRUCTIONS = """Gather the information needed for this task.

Plan summary: {plan_summary}
Task: {goal}
Success criteria: {criteria}
Inputs: {inputs}
Fields to fill (if any): {fields}

Use read_pdf / inspect_repo (and run_tests to reproduce a failing suite). Be economical: you have a limited tool budget, so do not read every file - for a bug-fix task run the tests first and then read only the files named in the traceback; for a paper, jump to the relevant section or grep for the terms you need. Quote evidence for every fact (page or file:line). When you have what you need, call `finish` with:
- summary: 2-4 sentences,
- key_facts: dict of the facts you found,
- extracted: dict with EXACTLY the requested fields (values as they appear in the source; numbers as numbers),
- relevant_files: list,
- evidence: list of short quotes with their location.
Do not guess a field; if it is not in the source, set it to null and say so in summary."""

IMPLEMENT_INSTRUCTIONS = """Make the code changes needed for this task, then call `finish`.

Plan summary: {plan_summary}
Findings: {findings_summary}
Task: {goal}
Success criteria: {criteria}
Target files: {targets}
Planned commands: {commands}

Rules: inspect before editing; keep diffs minimal; do not edit tests; do not run anything - EXECUTE will run the commands.
In `finish` give: summary, files_changed (paths), commands (the exact commands to run next, refined from the plan)."""

REPAIR_INSTRUCTIONS = """The previous execution failed. Diagnose and fix it, then call `finish`.

Task: {goal}
Success criteria: {criteria}
Commands that were run: {commands}
Failure kind: {kind}
Evidence: {evidence}
Location: {location}
Failing tests: {failing_tests}
Error output:
{error_tail}

Previous repair attempts: {previous}

Rules: read the failing code first; fix the root cause, not the symptom; keep the diff minimal; do not edit tests.
If the failure is caused by something that cannot be fixed in the sandbox (e.g. a missing package), say so in `finish` and set fixable=false.
In `finish` give: diagnosis, fix_summary, files_changed, fixable (bool), commands (commands to re-run)."""

VERIFY_INSTRUCTIONS = """Judge whether the task succeeded. Be strict: a run that finishes is not the same as a correct result.

Task: {goal}
Success criteria: {criteria}
Plan's success_check: {success_check}
Deterministic checks: {det_checks}
Execution results (tail): {exec_tail}
Findings / extracted answer: {findings}

Reply with JSON: passed (bool), reason (1-3 sentences), missing (list of what is still missing, if any),
answer (the final answer object for the user: extracted fields, or metrics parsed from the run output, or {{}})."""

REPORT_SUMMARY_INSTRUCTIONS = """Write a 3-5 sentence executive summary of this run for a human reader. Mention what was done, the outcome, and any caveat. Do not add numbers that are not in the material.

Task: {goal}
Status: {status}
Plan: {plan}
Findings: {findings}
Changes: {changes}
Execution: {execution}
Verdict: {verdict}

Reply with JSON: {{"summary": "..."}}"""

SINGLE_CALL_INSTRUCTIONS = """You must solve the task in ONE reply without tools. The relevant material is included below.

Task: {goal}
Success criteria: {criteria}
Fields to fill (if any): {fields}

Material:
{material}

Reply with JSON:
- answer: dict with the requested fields (or {{}}),
- patches: list of {{"path": "...", "content": "<full new file content>"}} for any file you need to change/create (or []),
- commands: list of shell commands to run to produce/verify the result (or []).
"""
