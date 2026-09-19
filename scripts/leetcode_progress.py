#!/usr/bin/env python3

from __future__ import annotations

import argparse
import calendar
import html
import json
import math
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError as exc:  # pragma: no cover - Python < 3.9
    raise SystemExit("Python 3.9 or newer is required.") from exc


GRAPHQL_URL = "https://leetcode.com/graphql"
DESIGN_VERSION = "pixel-platformer-v7"
DEFAULT_CONFIG = Path("../leetcode.json")
DEFAULT_OUTPUT = Path("../assets/leetcode-progress.svg")
DEFAULT_FETCH_LIMIT = 1_000
REQUEST_TIMEOUT = 25
DIFFICULTY_BATCH_SIZE = 40

ASCENT_TILES: list[tuple[float, float]] = [
    (65, 425),
    (125, 425),
    (125, 385),
    (195, 385),
    (195, 345),
    (265, 345),
    (265, 305),
    (335, 305),
    (335, 265),
    (405, 265),
    (405, 215),
    (475, 215),
    (475, 155),
    (495, 155),
]

BONUS_TILES: list[tuple[float, float]] = [
    (495, 155),
    (555, 155),
    (555, 195),
    (625, 195),
    (625, 235),
    (695, 235),
    (695, 275),
    (765, 275),
    (765, 315),
    (855, 315),
]


class ClimbError(RuntimeError):
    """A user-facing configuration or API error."""


@dataclass(frozen=True)
class Config:
    username: str
    timezone: str
    monthly_goal: int | None
    daily_goal: float | None
    fetch_limit: int = DEFAULT_FETCH_LIMIT


@dataclass(frozen=True)
class Problem:
    title: str
    title_slug: str
    timestamp: int
    difficulty: str = "Unknown"


@dataclass(frozen=True)
class Stats:
    solved: int
    goal: int
    percentage: float
    remaining: int
    overgoal: int
    days_in_month: int
    current_day: int
    expected: int
    difference: int
    pace_label: str
    difficulties: Counter[str]


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    """Read and validate leetcode.json."""
    if not path.is_file():
        raise ClimbError(f'Config file was not found: "{path}"')

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClimbError(
            f'Config file "{path}" contains invalid JSON: line {exc.lineno}, '
            f"column {exc.colno}."
        ) from exc
    except OSError as exc:
        raise ClimbError(f'Could not read config file "{path}": {exc}') from exc

    username = str(raw.get("username", "")).strip()
    if not username:
        raise ClimbError('"username" is required in leetcode.json.')

    monthly_goal = raw.get("monthly_goal")
    daily_goal = raw.get("daily_goal")

    if monthly_goal is None and daily_goal is None:
        raise ClimbError(
            'Set either "monthly_goal" or "daily_goal" in leetcode.json.'
        )

    if monthly_goal is not None and (
        isinstance(monthly_goal, bool)
        or not isinstance(monthly_goal, int)
        or monthly_goal <= 0
    ):
        raise ClimbError('"monthly_goal" must be a positive integer.')

    if daily_goal is not None and (
        isinstance(daily_goal, bool)
        or not isinstance(daily_goal, (int, float))
        or not math.isfinite(daily_goal)
        or daily_goal <= 0
    ):
        raise ClimbError('"daily_goal" must be a positive number.')

    if monthly_goal is not None and daily_goal is not None:
        print(
            'Warning: both goals are configured; "daily_goal" takes priority.',
            file=sys.stderr,
        )

    timezone_name = str(raw.get("timezone", "")).strip()
    if not timezone_name:
        raise ClimbError('"timezone" is required in leetcode.json.')

    fetch_limit = raw.get("fetch_limit", DEFAULT_FETCH_LIMIT)
    if isinstance(fetch_limit, bool) or not isinstance(fetch_limit, int) or fetch_limit <= 0:
        raise ClimbError('Optional "fetch_limit" must be a positive integer.')

    # Validate early so Windows users get a useful installation hint.
    get_timezone(timezone_name)
    return Config(
        username=username,
        timezone=timezone_name,
        monthly_goal=monthly_goal,
        daily_goal=float(daily_goal) if daily_goal is not None else None,
        fetch_limit=fetch_limit,
    )


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ClimbError(
            f'Timezone "{name}" was not found.\n\n'
            "Install the timezone database with:\n"
            "python -m pip install tzdata"
        ) from exc


def graphql(
    query: str,
    variables: dict[str, Any],
    *,
    timeout: int = REQUEST_TIMEOUT,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Run a GraphQL request against the official LeetCode endpoint."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://leetcode.com",
            "Referer": "https://leetcode.com/",
            "User-Agent": "leetcode-monthly-climb/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:300]
        suffix = f" Response: {details}" if details else ""
        raise ClimbError(f"LeetCode returned HTTP {exc.code}.{suffix}") from exc
    except urllib.error.URLError as exc:
        raise ClimbError(f"Could not connect to LeetCode: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ClimbError(f"LeetCode did not respond within {timeout} seconds.") from exc

    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ClimbError("LeetCode returned a response that was not valid JSON.") from exc

    errors = document.get("errors") or []
    if errors:
        messages = "; ".join(str(item.get("message", item)) for item in errors)
        if allow_partial and document.get("data") is not None:
            print(f"Warning: partial GraphQL response: {messages}", file=sys.stderr)
        else:
            raise ClimbError(f"LeetCode GraphQL error: {messages}")

    data = document.get("data")
    if not isinstance(data, dict):
        raise ClimbError("LeetCode response did not contain GraphQL data.")
    return data


def get_recent_accepted(username: str, limit: int = DEFAULT_FETCH_LIMIT) -> list[dict[str, Any]]:
    """Fetch the user's public accepted submissions."""
    query = """
    query RecentAccepted($username: String!, $limit: Int!) {
      matchedUser(username: $username) { username }
      recentAcSubmissionList(username: $username, limit: $limit) {
        title
        titleSlug
        timestamp
      }
    }
    """
    data = graphql(query, {"username": username, "limit": limit})
    if data.get("matchedUser") is None:
        raise ClimbError(
            f'LeetCode user "{username}" was not found or their profile is unavailable.'
        )

    submissions = data.get("recentAcSubmissionList")
    if submissions is None:
        raise ClimbError("LeetCode did not return accepted submissions for this user.")
    if not isinstance(submissions, list):
        raise ClimbError("LeetCode returned malformed submission data.")
    return submissions


def get_monthly_problems(
    submissions: Iterable[dict[str, Any]], now: datetime
) -> dict[str, Problem]:
    """Return unique problems accepted in now's calendar month and timezone."""
    unique: dict[str, Problem] = {}
    malformed = 0

    for item in submissions:
        try:
            title = str(item["title"]).strip()
            slug = str(item["titleSlug"]).strip()
            timestamp = int(item["timestamp"])
            if not title or not slug:
                raise ValueError("empty title or titleSlug")
            submitted_at = datetime.fromtimestamp(timestamp, tz=now.tzinfo)
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            malformed += 1
            continue

        if submitted_at.year == now.year and submitted_at.month == now.month:
            candidate = Problem(title, slug, timestamp)
            previous = unique.get(slug)
            if previous is None or timestamp < previous.timestamp:
                unique[slug] = candidate

    if malformed:
        print(
            f"Warning: skipped {malformed} malformed submission(s).",
            file=sys.stderr,
        )
    return unique


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def get_problem_difficulty(title_slugs: Sequence[str]) -> dict[str, str]:
    """Fetch difficulties in GraphQL batches; each slug is requested only once."""
    result: dict[str, str] = {}
    valid = {"Easy", "Medium", "Hard"}

    for batch in _chunks(list(dict.fromkeys(title_slugs)), DIFFICULTY_BATCH_SIZE):
        declarations: list[str] = []
        fields: list[str] = []
        variables: dict[str, str] = {}

        for index, slug in enumerate(batch):
            variable = f"slug{index}"
            alias = f"problem{index}"
            declarations.append(f"${variable}: String!")
            fields.append(f"{alias}: question(titleSlug: ${variable}) {{ difficulty }}")
            variables[variable] = slug

        query = (
            "query ProblemDifficulties("
            + ", ".join(declarations)
            + ") {\n"
            + "\n".join(fields)
            + "\n}"
        )

        try:
            data = graphql(query, variables, allow_partial=True)
        except ClimbError as exc:
            print(
                f"Warning: could not fetch a difficulty batch: {exc}",
                file=sys.stderr,
            )
            for slug in batch:
                result[slug] = "Unknown"
            continue

        for index, slug in enumerate(batch):
            problem_data = data.get(f"problem{index}")
            difficulty = (
                problem_data.get("difficulty") if isinstance(problem_data, dict) else None
            )
            if difficulty not in valid:
                result[slug] = "Unknown"
                print(
                    f'Warning: difficulty is unavailable for "{slug}"; using Unknown.',
                    file=sys.stderr,
                )
            else:
                result[slug] = difficulty

    return result


def add_difficulties(
    problems: dict[str, Problem], difficulties: dict[str, str]
) -> dict[str, Problem]:
    """Return Problem objects enriched with difficulty values."""
    return {
        slug: Problem(
            title=problem.title,
            title_slug=problem.title_slug,
            timestamp=problem.timestamp,
            difficulty=difficulties.get(slug, "Unknown"),
        )
        for slug, problem in problems.items()
    }


def resolve_monthly_goal(config: Config, now: datetime) -> int:
    """Return the explicit monthly goal or derive it from the daily target."""
    if config.daily_goal is not None:
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        return math.ceil(config.daily_goal * days_in_month)

    if config.monthly_goal is None:  # Defensive: load_config already rejects this.
        raise ClimbError('Set either "monthly_goal" or "daily_goal".')
    return config.monthly_goal


def calculate_stats(problems: Iterable[Problem], goal: int, now: datetime) -> Stats:
    problem_list = list(problems)
    solved = len(problem_list)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    expected = round(goal * now.day / days_in_month)
    difference = solved - expected

    if difference > 0:
        pace_label = "ABOVE SCHEDULE"
    elif difference < 0:
        pace_label = "BELOW SCHEDULE"
    else:
        pace_label = "ON SCHEDULE"

    return Stats(
        solved=solved,
        goal=goal,
        percentage=solved / goal * 100,
        remaining=max(goal - solved, 0),
        overgoal=max(solved - goal, 0),
        days_in_month=days_in_month,
        current_day=now.day,
        expected=expected,
        difference=difference,
        pace_label=pace_label,
        difficulties=Counter(problem.difficulty for problem in problem_list),
    )


def _segment_length(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def point_on_path(
    points: Sequence[tuple[float, float]], progress: float
) -> tuple[float, float]:
    """Interpolate a point by travelled length, with progress clamped to 0..1."""
    if not points:
        raise ValueError("A path needs at least one point.")
    if len(points) == 1:
        return points[0]

    progress = max(0.0, min(progress, 1.0))
    lengths = [_segment_length(a, b) for a, b in zip(points, points[1:])]
    target = sum(lengths) * progress

    for start, end, length in zip(points, points[1:], lengths):
        if length == 0:
            continue
        if target <= length:
            ratio = target / length
            return (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        target -= length
    return points[-1]


def partial_path(
    points: Sequence[tuple[float, float]], progress: float
) -> list[tuple[float, float]]:
    """Return the portion of a polyline travelled at normalized progress."""
    if not points:
        return []
    if len(points) == 1 or progress <= 0:
        return [points[0]]
    if progress >= 1:
        return list(points)

    lengths = [_segment_length(a, b) for a, b in zip(points, points[1:])]
    target = sum(lengths) * progress
    travelled: list[tuple[float, float]] = [points[0]]

    for start, end, length in zip(points, points[1:], lengths):
        if length == 0:
            continue
        if target >= length:
            travelled.append(end)
            target -= length
            continue
        ratio = target / length
        travelled.append(
            (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        )
        break
    return travelled


def points_to_path(points: Sequence[tuple[float, float]]) -> str:
    if not points:
        return ""
    first, *rest = points
    commands = [f"M {first[0]:.1f} {first[1]:.1f}"]
    commands.extend(f"L {x:.1f} {y:.1f}" for x, y in rest)
    return " ".join(commands)


def pixel_player_svg(x: float, y: float, color: str = "#58a6ff") -> str:
    """Draw a crisp pixel-art player standing on the current map tile."""
    c = html.escape(color, quote=True)
    return f"""
    <g transform="translate({x:.1f} {y:.1f})" shape-rendering="crispEdges"
       aria-label="pixel climber">
      <rect x="-16" y="-35" width="34" height="38" rx="2"
            fill="{c}" opacity="0.10" filter="url(#glow)"/>
      <!-- backpack -->
      <rect x="-11" y="-21" width="5" height="13" fill="#1f6feb"/>
      <rect x="-13" y="-18" width="3" height="7" fill="#0d419d"/>
      <!-- helmet and face -->
      <rect x="-6" y="-31" width="13" height="4" fill="{c}"/>
      <rect x="-8" y="-28" width="15" height="3" fill="#1f6feb"/>
      <rect x="-5" y="-25" width="11" height="8" fill="#c9d1d9"/>
      <rect x="3" y="-23" width="3" height="3" fill="#0d1117"/>
      <!-- jacket, arms, and belt -->
      <rect x="-6" y="-17" width="12" height="10" fill="{c}"/>
      <rect x="-10" y="-16" width="4" height="4" fill="#1f6feb"/>
      <rect x="-12" y="-13" width="4" height="7" fill="#c9d1d9"/>
      <rect x="6" y="-16" width="4" height="8" fill="#1f6feb"/>
      <rect x="8" y="-9" width="4" height="4" fill="#c9d1d9"/>
      <rect x="-6" y="-9" width="12" height="3" fill="#d29922"/>
      <!-- legs and boots -->
      <rect x="-5" y="-6" width="4" height="6" fill="#8b949e"/>
      <rect x="2" y="-6" width="4" height="6" fill="#8b949e"/>
      <rect x="-7" y="-2" width="6" height="2" fill="#c9d1d9"/>
      <rect x="2" y="-2" width="7" height="2" fill="#c9d1d9"/>
    </g>"""


def game_map_svg() -> str:
    """Return the block terrain for the ascent and the unlocked bonus level."""
    return """
  <!-- PIXEL PLATFORMER MAP: every corner is deliberately axis-aligned -->
  <path d="M 30 443 H 65 V 425 H 125 V 385 H 195 V 345 H 265 V 305
           H 335 V 265 H 405 V 215 H 475 V 155 H 555 V 195 H 625
           V 235 H 695 V 275 H 765 V 315 H 855 V 443 Z"
        fill="#161b22" stroke="#30363d" stroke-width="2"/>
  <path d="M 30 443 H 65 V 425 H 125 V 385 H 195 V 345 H 265 V 305
           H 335 V 265 H 405 V 215 H 475 V 155 H 555 V 195 H 625
           V 235 H 695 V 275 H 765 V 315 H 855 V 443 Z"
        fill="url(#block-grid)"/>

  <!-- breakable blocks / game decorations -->
  <g fill="#30363d" shape-rendering="crispEdges">
    <rect x="77" y="408" width="7" height="7"/>
    <rect x="214" y="328" width="7" height="7"/>
    <rect x="354" y="248" width="7" height="7"/>
    <rect x="647" y="218" width="7" height="7"/>
    <rect x="790" y="298" width="7" height="7"/>
  </g>
  <!-- level-number tiles make the redesign visible even without color -->
  <g class="small dim" text-anchor="middle">
    <text x="158" y="412">01</text>
    <text x="298" y="332">02</text>
    <text x="438" y="242">03</text>
    <text x="590" y="222">B1</text>
    <text x="730" y="302">B2</text>
  </g>"""


def trail_svg(
    ascent_done: Sequence[tuple[float, float]],
    bonus_done: Sequence[tuple[float, float]],
    bonus_opacity: str,
) -> str:
    """Draw locked and completed portions of the two game routes."""
    return f"""
  <g fill="none" stroke-width="6" stroke-linecap="square" stroke-linejoin="miter">
    <path d="{points_to_path(ASCENT_TILES)}" stroke="#30363d"/>
    <path d="{points_to_path(BONUS_TILES)}" stroke="#30363d"
          opacity="{bonus_opacity}"/>
    <path d="{points_to_path(ascent_done)}" stroke="#58a6ff"/>
    <path d="{points_to_path(bonus_done)}" stroke="#3fb950"
          opacity="{bonus_opacity}"/>
  </g>"""


def checkpoint_flag_svg(goal: int) -> str:
    """Draw a pixel checkpoint at the monthly goal."""
    return f"""
  <g shape-rendering="crispEdges" aria-label="goal checkpoint">
    <rect x="493" y="104" width="4" height="51" fill="#c9d1d9"/>
    <rect x="497" y="104" width="30" height="12" fill="#3fb950"/>
    <rect x="521" y="116" width="6" height="6" fill="#3fb950"/>
  </g>
  <text x="536" y="110" class="label" style="fill:#3fb950">CHECKPOINT</text>
  <text x="536" y="130" class="value">GOAL {goal}</text>"""


def _typing_messages(stats: Stats) -> list[str]:
    """Choose terminal commands from the user's current monthly state."""
    pace = stats.pace_label.lower().replace(" ", "_")
    if stats.solved == 0:
        return [
            "mission.start()",
            f"goal.set({stats.goal})",
            "solve.first_problem()",
        ]
    if stats.solved < stats.goal:
        return [
            f"climb.next({stats.remaining})",
            f'pace.status("{pace}")',
            f"progress.update({stats.percentage:.1f}%)",
        ]
    if stats.solved == stats.goal:
        return [
            "checkpoint.reached()",
            f"goal.complete({stats.solved}/{stats.goal})",
            "confetti.launch()",
        ]
    return [
        f"overgoal.enable(+{stats.overgoal})",
        "bonus.trail.run()",
        'fire.mode("pixel")',
    ]


def typing_terminal_svg(username: str, stats: Stats) -> str:
    """Generate SMIL per-character typing, reverse erasing, and a moving cursor."""
    messages = _typing_messages(stats)
    prefix = f"{username}@leetcode:~$ "
    x0 = 28.0
    y = 659
    char_width = 7.8
    command_x = x0 + len(prefix) * char_width
    type_delay = 0.075
    erase_delay = 0.04
    hold_time = 1.15
    gap_time = 0.35

    segments: list[tuple[str, float, float, float]] = []
    cursor_events: list[tuple[float, float]] = [(0.0, command_x)]
    clock = 0.8

    for message in messages:
        type_start = clock
        type_end = type_start + len(message) * type_delay
        erase_start = type_end + hold_time
        erase_end = erase_start + len(message) * erase_delay
        segments.append((message, type_start, erase_start, erase_end))

        for index in range(len(message)):
            cursor_events.append(
                (type_start + (index + 1) * type_delay, command_x + (index + 1) * char_width)
            )
        for index in range(len(message)):
            cursor_events.append(
                (erase_start + (index + 1) * erase_delay, command_x + (len(message) - index - 1) * char_width)
            )
        clock = erase_end + gap_time

    cycle = clock + 0.7
    cursor_events.append((cycle, command_x))
    chars: list[str] = []
    for message, type_start, erase_start, _erase_end in segments:
        for index, character in enumerate(message):
            show_at = type_start + index * type_delay
            # Erase from right to left, like a held backspace key.
            hide_at = erase_start + (len(message) - index - 1) * erase_delay
            key_times = f"0;{show_at / cycle:.6f};{hide_at / cycle:.6f};1"
            x = command_x + index * char_width
            chars.append(
                f'<text x="{x:.1f}" y="{y}" class="terminal" opacity="0">'
                f'{html.escape(character)}'
                f'<animate attributeName="opacity" values="0;1;0;0" '
                f'keyTimes="{key_times}" calcMode="discrete" dur="{cycle:.3f}s" '
                f'repeatCount="indefinite"/></text>'
            )

    # SMIL requires monotonically increasing keyTimes. Events are already chronological.
    cursor_events.sort(key=lambda event: event[0])
    cursor_values = ";".join(f"{x:.1f}" for _, x in cursor_events)
    cursor_times = ";".join(f"{time / cycle:.6f}" for time, _ in cursor_events)

    escaped_username = html.escape(username)
    return f"""
  <g aria-label="animated progress terminal">
    <text x="{x0:.1f}" y="{y}" class="terminal">
      <tspan style="fill:#3fb950">{escaped_username}@leetcode</tspan><tspan style="fill:#8b949e">:~$</tspan>
    </text>
    {''.join(chars)}
    <text x="{command_x:.1f}" y="{y}" class="terminal" style="fill:#58a6ff">█
      <animate attributeName="x" values="{cursor_values}" keyTimes="{cursor_times}"
               calcMode="discrete" dur="{cycle:.3f}s" repeatCount="indefinite"/>
      <animate attributeName="opacity" values="1;0;1" keyTimes="0;0.5;1"
               calcMode="discrete" dur="0.85s" repeatCount="indefinite"/>
    </text>
  </g>"""


def confetti_svg() -> str:
    """Return a short pixel-confetti burst followed by a long idle interval."""
    particles = [
        # Y coordinates below already include the requested -80 px offset.
        (432, 55, -30, 118, "#58a6ff", 0.00),
        (449, 46, -17, 132, "#3fb950", 0.02),
        (466, 62, -8, 105, "#d29922", 0.04),
        (483, 43, 8, 137, "#f85149", 0.01),
        (500, 53, 16, 112, "#58a6ff", 0.05),
        (517, 41, 28, 141, "#3fb950", 0.03),
        (534, 59, 37, 110, "#d29922", 0.06),
        (551, 48, 45, 130, "#f85149", 0.00),
        (442, 71, -38, 95, "#d29922", 0.07),
        (458, 68, -22, 121, "#f85149", 0.03),
        (476, 74, -10, 99, "#3fb950", 0.06),
        (494, 67, 10, 126, "#58a6ff", 0.02),
        (512, 71, 24, 102, "#f85149", 0.07),
        (530, 65, 34, 119, "#d29922", 0.04),
        (548, 73, 49, 94, "#3fb950", 0.01),
    ]
    cycle = 9.0
    items: list[str] = []
    for index, (x, y, dx, fall, color, delay) in enumerate(particles):
        start = 0.02 + delay
        end = min(start + 0.18 + (index % 3) * 0.012, 0.30)
        fade_in = min(start + 0.01, end - 0.01)
        key_move = f"0;{start:.3f};{end:.3f};1"
        key_fade = f"0;{start:.3f};{fade_in:.3f};{end:.3f};1"
        items.append(
            f'<rect x="{x}" y="{y}" width="6" height="6" fill="{color}" opacity="0">'
            f'<animate attributeName="x" values="{x};{x};{x + dx};{x + dx}" '
            f'keyTimes="{key_move}" dur="{cycle}s" begin="2s" repeatCount="indefinite"/>'
            f'<animate attributeName="y" values="{y};{y};{y + fall};{y + fall}" '
            f'keyTimes="{key_move}" dur="{cycle}s" begin="2s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0;0;1;0;0" '
            f'keyTimes="{key_fade}" dur="{cycle}s" begin="2s" repeatCount="indefinite"/>'
            "</rect>"
        )
    return (
        '<g shape-rendering="crispEdges" aria-label="pixel confetti">'
        + "".join(items)
        + "</g>"
    )


def pixel_fire_svg(x: float, y: float, *, blue: bool = False) -> str:
    """Draw one coherent, layered pixel flame behind the overgoal player."""
    if blue:
        outer, middle, inner, core = "#0d419d", "#1f6feb", "#58a6ff", "#c9d1d9"
        label = "pixel blue overgoal fire"
    else:
        outer, middle, inner, core = "#9e2a2b", "#f85149", "#d29922", "#f2cc60"
        label = "pixel overgoal fire"

    return f"""
  <g transform="translate({x:.1f} {y:.1f})" shape-rendering="crispEdges"
     aria-label="{label}">
    <!-- broad outer flame -->
    <g>
      <animateTransform attributeName="transform" type="scale"
        values="1 1;1 0.82;1 0.94;1 1" keyTimes="0;0.35;0.7;1"
        dur="0.72s" repeatCount="indefinite"/>
      <path d="M -16 0 V -7 H -13 V -17 H -9 V -30 H -5 V -22
               H -1 V -40 H 3 V -29 H 7 V -35 H 11 V -19
               H 15 V -10 H 18 V 0 Z" fill="{outer}"/>
    </g>
    <!-- hot middle flame -->
    <g>
      <animateTransform attributeName="transform" type="scale"
        values="1 0.88;1 1;1 0.78;1 0.88" keyTimes="0;0.3;0.68;1"
        dur="0.59s" repeatCount="indefinite"/>
      <path d="M -11 0 V -9 H -8 V -21 H -4 V -15 H 0 V -30
               H 4 V -20 H 8 V -25 H 11 V -11 H 14 V 0 Z"
            fill="{middle}"/>
    </g>
    <!-- inner flame and bright core -->
    <g>
      <animateTransform attributeName="transform" type="scale"
        values="1 1;1 0.76;1 0.91;1 1" keyTimes="0;0.4;0.75;1"
        dur="0.48s" repeatCount="indefinite"/>
      <path d="M -6 0 V -8 H -3 V -17 H 1 V -11 H 4 V -20
               H 7 V -10 H 10 V 0 Z" fill="{inner}"/>
      <path d="M -2 0 V -8 H 1 V -13 H 4 V -6 H 6 V 0 Z"
            fill="{core}"/>
    </g>

    <!-- detached embers -->
    <rect x="-11" y="-24" width="4" height="4" fill="{middle}">
      <animate attributeName="y" values="-24;-43;-24" dur="1.1s"
               repeatCount="indefinite"/>
      <animate attributeName="opacity" values="0.9;0;0.9" dur="1.1s"
               repeatCount="indefinite"/>
    </rect>
    <rect x="9" y="-20" width="3" height="3" fill="{inner}">
      <animate attributeName="y" values="-20;-36;-20" dur="0.83s"
               repeatCount="indefinite"/>
      <animate attributeName="opacity" values="0.8;0;0.8" dur="0.83s"
               repeatCount="indefinite"/>
    </rect>
  </g>"""


def _xml(value: object) -> str:
    return html.escape(str(value), quote=True)


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


def generate_svg(config: Config, stats: Stats, now: datetime) -> str:
    """Render the complete GitHub-safe SVG card."""
    username = _xml(config.username)
    month_label = _xml(now.strftime("%B %Y").upper())

    climb_progress = min(stats.solved / stats.goal, 1.0)
    bonus_progress = max(stats.solved - stats.goal, 0) / stats.goal

    if stats.overgoal > 0:
        marker_x, marker_y = point_on_path(BONUS_TILES, bonus_progress)
        climb_done = ASCENT_TILES
        bonus_done = partial_path(BONUS_TILES, min(bonus_progress, 1.0))
        summit_text = f"SUMMIT REACHED  •  +{stats.overgoal} OVERGOAL"
    else:
        marker_x, marker_y = point_on_path(ASCENT_TILES, climb_progress)
        climb_done = partial_path(ASCENT_TILES, climb_progress)
        bonus_done = [BONUS_TILES[0]]
        summit_text = (
            "SUMMIT REACHED"
            if stats.remaining == 0
            else f"{stats.remaining} PROBLEM{'S' if stats.remaining != 1 else ''} TO SUMMIT"
        )

    pace_color = {
        "ABOVE SCHEDULE": "#3fb950",
        "BELOW SCHEDULE": "#f85149",
        "ON SCHEDULE": "#d29922",
    }[stats.pace_label]
    bonus_opacity = "1" if stats.overgoal else "0.28"
    if stats.solved == stats.goal:
        celebration_effect = confetti_svg()
    elif stats.overgoal > 0:
        blue_fire = stats.solved >= stats.goal * 1.5
        celebration_effect = pixel_fire_svg(marker_x, marker_y, blue=blue_fire)
    else:
        celebration_effect = ""
    unknown = stats.difficulties.get("Unknown", 0)
    unknown_svg = (
        f'<text x="325" y="557" class="small dim">? UNKNOWN {unknown}</text>'
        if unknown
        else ""
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="690"
     viewBox="0 0 900 690" role="img"
     aria-labelledby="title description">
  <title id="title">LeetCode Monthly Climb for {username}</title>
  <desc id="description">{stats.solved} of {stats.goal} unique problems solved,
    {stats.percentage:.1f} percent, {stats.pace_label.lower()}.</desc>
  <defs>
    <filter id="glow" x="-100%" y="-100%" width="300%" height="300%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <pattern id="block-grid" width="16" height="16" patternUnits="userSpaceOnUse">
      <path d="M 16 0 H 0 V 16" fill="none" stroke="#30363d"
            stroke-width="1" opacity="0.55"/>
    </pattern>
    <pattern id="scanlines" width="4" height="4" patternUnits="userSpaceOnUse">
      <path d="M 0 3.5 H 900" stroke="#ffffff" stroke-opacity="0.016"/>
    </pattern>
    <style>
      text {{
        font-family: "JetBrains Mono", "Fira Code", "Courier New", monospace;
        fill: #c9d1d9;
      }}
      .title {{ font-size: 17px; font-weight: 700; letter-spacing: 1px; }}
      .terminal {{ font-size: 13px; }}
      .big {{ font-size: 30px; font-weight: 700; }}
      .percent {{ font-size: 27px; font-weight: 700; fill: #58a6ff; }}
      .label {{ font-size: 12px; font-weight: 700; letter-spacing: 1.2px; }}
      .small {{ font-size: 12px; }}
      .dim {{ fill: #8b949e; }}
      .value {{ font-size: 14px; font-weight: 700; }}
    </style>
  </defs>

  <rect x="1" y="1" width="898" height="688" rx="12" fill="#0d1117"
        stroke="#30363d" stroke-width="2"/>
  <path d="M 13 1 H 887 Q 899 1 899 13 V 43 H 1 V 13 Q 1 1 13 1 Z"
        fill="#161b22"/>
  <line x1="1" y1="43" x2="899" y2="43" stroke="#30363d"/>
  <circle cx="23" cy="22" r="6" fill="#f85149"/>
  <circle cx="43" cy="22" r="6" fill="#d29922"/>
  <circle cx="63" cy="22" r="6" fill="#3fb950"/>
  <text x="85" y="27" class="terminal dim">leetcode@{username}</text>

  <text x="28" y="79" class="title">LEETCODE MONTHLY CLIMB</text>
  <text x="872" y="79" class="label dim" text-anchor="end">{month_label}</text>
  <text x="28" y="119" class="big">{stats.solved} / {stats.goal}</text>
  <text x="872" y="116" class="percent" text-anchor="end">{stats.percentage:.1f}%</text>
  <text x="28" y="145" class="label" fill="#8b949e">{_xml(summit_text)}</text>

  {game_map_svg()}
  {trail_svg(climb_done, bonus_done, bonus_opacity)}

  <!-- game start marker -->
  <rect x="60" y="420" width="10" height="10" fill="#8b949e"/>
  <rect x="63" y="423" width="4" height="4" fill="#c9d1d9"/>
  <text x="50" y="459" class="label dim">LEVEL START</text>

  {checkpoint_flag_svg(stats.goal)}

  <text x="785" y="348" class="label dim" text-anchor="middle"
        opacity="{bonus_opacity}">OVERGOAL TRAIL</text>
  {celebration_effect}
  {pixel_player_svg(marker_x, marker_y)}

  <!-- lower panels -->
  <rect x="28" y="467" width="412" height="137" rx="8"
        fill="#161b22" stroke="#30363d"/>
  <text x="48" y="495" class="label dim">THIS MONTH</text>
  <circle cx="53" cy="525" r="4" fill="#3fb950"/>
  <text x="65" y="530" class="value" style="fill:#3fb950">{stats.difficulties.get('Easy', 0)} EASY</text>
  <circle cx="173" cy="525" r="4" fill="#d29922"/>
  <text x="185" y="530" class="value" style="fill:#d29922">{stats.difficulties.get('Medium', 0)} MEDIUM</text>
  <circle cx="313" cy="525" r="4" fill="#f85149"/>
  <text x="325" y="530" class="value" style="fill:#f85149">{stats.difficulties.get('Hard', 0)} HARD</text>
  {unknown_svg}
  <text x="48" y="579" class="small dim">DAY {stats.current_day}/{stats.days_in_month}</text>
  <text x="420" y="579" class="small dim" text-anchor="end">UNIQUE ACCEPTED</text>

  <rect x="460" y="467" width="412" height="137" rx="8"
        fill="#161b22" stroke="#30363d"/>
  <text x="480" y="495" class="label dim">CLIMB STATUS</text>
  <text x="480" y="524" class="small dim">Expected</text>
  <text x="585" y="524" class="value">{stats.expected}</text>
  <text x="480" y="547" class="small dim">Current</text>
  <text x="585" y="547" class="value">{stats.solved}</text>
  <text x="480" y="570" class="small dim">Delta</text>
  <text x="585" y="570" class="value" style="fill:{pace_color}">{_signed(stats.difference)}</text>
  <circle cx="670" cy="545" r="5" fill="{pace_color}"/>
  <text x="684" y="550" class="label" style="fill:{pace_color}">{stats.pace_label}</text>

  <line x1="28" y1="628" x2="872" y2="628" stroke="#21262d"/>
  {typing_terminal_svg(config.username, stats)}
  <rect x="1" y="1" width="898" height="688" rx="12" fill="url(#scanlines)"
        pointer-events="none"/>
</svg>
"""


def _demo_problems(solved: int) -> list[Problem]:
    difficulties = ("Easy", "Medium", "Medium", "Hard")
    return [
        Problem(
            title=f"Demo Problem {index + 1}",
            title_slug=f"demo-problem-{index + 1}",
            timestamp=0,
            difficulty=difficulties[index % len(difficulties)],
        )
        for index in range(max(solved, 0))
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--demo-solved",
        type=int,
        metavar="N",
        help="generate a preview with N fake solved problems and skip the API",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        config = load_config(args.config)
        timezone = get_timezone(config.timezone)
        now = datetime.now(timezone)
        effective_goal = resolve_monthly_goal(config, now)
        days_in_month = calendar.monthrange(now.year, now.month)[1]

        print("=" * 60)
        print("LeetCode Monthly Climb")
        print("=" * 60)
        print()
        print(f"Design:    {DESIGN_VERSION}")
        print(f"User:      {config.username}")
        print(f"Month:     {now.strftime('%B %Y')}")
        if config.daily_goal is not None:
            print("Goal mode: daily")
            print(f"Daily:     {config.daily_goal:g} problem(s)")
            print(f"Days:      {days_in_month}")
        else:
            print("Goal mode: monthly")
        print(f"Goal:      {effective_goal}")
        print(f"Timezone:  {config.timezone}")
        print()

        if args.demo_solved is not None:
            if args.demo_solved < 0:
                raise ClimbError("--demo-solved cannot be negative.")
            problems = _demo_problems(args.demo_solved)
            print(f"Demo mode: using {args.demo_solved} generated problem(s).")
        else:
            if config.username == "YOUR_LEETCODE_USERNAME":
                raise ClimbError(
                    'Set a real LeetCode username in "leetcode.json" before running the script.'
                )
            print("Fetching accepted submissions...")
            submissions = get_recent_accepted(config.username, config.fetch_limit)
            print(f"Received {len(submissions)} accepted submissions.")

            monthly = get_monthly_problems(submissions, now)
            print(f"Unique problems solved this month: {len(monthly)}")

            if len(submissions) >= config.fetch_limit and submissions:
                timestamps = []
                for item in submissions:
                    try:
                        timestamps.append(int(item["timestamp"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                if timestamps:
                    oldest = datetime.fromtimestamp(min(timestamps), timezone)
                    if oldest.year == now.year and oldest.month == now.month:
                        print(
                            "Warning: fetch_limit was reached inside the current month; "
                            "increase fetch_limit in leetcode.json to avoid undercounting.",
                            file=sys.stderr,
                        )

            print("Fetching difficulties...")
            difficulty_map = get_problem_difficulty(sorted(monthly))
            problems = list(add_difficulties(monthly, difficulty_map).values())

        stats = calculate_stats(problems, effective_goal, now)
        print()
        print("Monthly statistics")
        print("-" * 30)
        print(f"Solved:   {stats.solved}/{stats.goal}")
        print(f"Progress: {stats.percentage:.1f}%")
        print(f"Easy:     {stats.difficulties.get('Easy', 0)}")
        print(f"Medium:   {stats.difficulties.get('Medium', 0)}")
        print(f"Hard:     {stats.difficulties.get('Hard', 0)}")
        if stats.difficulties.get("Unknown", 0):
            print(f"Unknown:  {stats.difficulties['Unknown']}")

        print()
        print("Generating SVG...")
        svg = generate_svg(config, stats, now)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8", newline="\n")
        print()
        print("Generated:")
        print(args.output)
        return 0
    except ClimbError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error: could not write the SVG: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
