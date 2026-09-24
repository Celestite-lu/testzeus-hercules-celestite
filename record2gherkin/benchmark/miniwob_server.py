"""Patch-serving HTTP server for the vendored MiniWoB++ tree (spec §3).

The page itself cannot report its outcome: Hercules launches its own browser and closes it when the
run ends, so the episode state dies with the tab.  This server closes the loop by appending two
scripts to the served ``core/core.js`` (a pure append — the vendored bytes are never rewritten):

* **auto-start** (``r2g_seed`` in the query) — waits for the page ``onload`` *and* ``core.cover_div``
  before ``Math.seedrandom(seed); core.EPISODE_MAX_TIME = ms; core.startEpisodeReal()``, so the same
  ``(task, seed)`` always opens the same episode (review 必改 1.4).  It then applies the security
  hardening of 安全审查 R1 §2.V1/V2 (H1): the reward HUD (``#reward-display``) is hidden from
  ``innerText`` and both ``core.updateDisplay`` and ``core.startEpisode`` are stubbed out, so neither
  the official reward text nor a clickable ``START`` re-roll overlay reaches the agent's text view.
* **reward hook** — wraps ``core.endEpisode`` and POSTs the terminal state to ``/__r2g_reward``
  synchronously (the page may be destroyed right after).

Two opt-in (default off) r2 patch layers (spec-r2 §4), both server-level flags — the URL, the seed
and the episode budget semantics never change:

* ``--single-start`` (C3) — 补丁 A switches to ``AUTO_START_PATCH_SINGLE``: the episode auto-starts
  at most once per tab (``sessionStorage["r2g_started"]``); an intercepted re-load neither produces a
  reward record nor leaves a usable START overlay (M2 hardening).
* ``--terminal-cue`` (C2) — appends ``TERMINAL_CUE_PATCH``: a neutral, constant ``EPISODE ENDED``
  marker appears in the page corner when the episode ends.  No reward value, no success/failure word,
  no POST — the page reward stays the only judging authority.
* ``--offseed-beacon`` (r4 E1/E2, spec-r4 §3.1) — appends ``INTEGRITY_BEACON_PATCH``: integrity
  beacons, harness-only and judgement-neutral.  A page load without ``r2g_seed`` in the query POSTs
  ``/__r2g_offseed`` (off-seed evidence, 安全复审 M2); a seeded load POSTs ``/__r2g_epstart`` with the
  seed (episode-started evidence, distinguishing "never started" from "started, no reward").  Both
  land as append-only JSONL lines next to the rewards file; neither touches the episode or the
  reward chain.

Endpoints (spec §3.3)::

    GET  /miniwob/<file>.html        vendored bytes as-is (404 outside the root, no directory listing)
    GET  /core/core.js               vendored bytes + both patches
    POST /__r2g_reward               terminal record -> memory (last-wins) + one JSONL line
    GET  /__r2g_reward/latest?...    latest record for (task, seed), 404 ``{"error": "no_reward"}``
    POST /__r2g_offseed              r4: off-seed load -> one ``offseed.jsonl`` line (always 204)
    POST /__r2g_epstart              r4: seeded load -> one ``epstart.jsonl`` line (always 204)
    GET  /healthz                    ``{"served_root", "patched", "rewards"}``

CLI::

    python -m record2gherkin.benchmark.miniwob_server --root <abs> --port 8462 --rewards-file <abs>
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from testzeus_hercules.utils.logger import logger

DEFAULT_HOST = "127.0.0.1"
#: spec §3.1: the port is an experiment parameter (it is baked into every Generated feature URL).
DEFAULT_PORT = 8462

REWARD_PATH = "/__r2g_reward"
REWARD_LATEST_PATH = "/__r2g_reward/latest"
HEALTH_PATH = "/healthz"
CORE_JS_SUFFIX = "/core/core.js"
#: spec-r4 §3.1 (E1/E2): the two integrity-beacon endpoints and their JSONL files.
OFFSEED_PATH = "/__r2g_offseed"
EPSTART_PATH = "/__r2g_epstart"
OFFSEED_FILENAME = "offseed.jsonl"
EPSTART_FILENAME = "epstart.jsonl"

PATCH_START_MARKER = "__R2G_PATCH_START__"
PATCH_END_MARKER = "__R2G_PATCH_END__"
REWARD_HOOK_MARKER = "__R2G_REWARD_HOOK__"
#: spec-r2 §4.1/§4.2/§4.3 markers of the two opt-in (default off) r2 patch layers.
PATCH_SINGLE_START_MARKER = "__R2G_PATCH_SINGLE_START__"
TERMINAL_CUE_MARKER = "__R2G_TERMINAL_CUE__"

#: spec §3.2 补丁 A (auto-start), verbatim — the review-revised text (必改 1.4) plus the security
#: hardening of 安全审查 R1 §2.V1/V2 (H1).  The hardening runs *after* ``startEpisodeReal()`` succeeded:
#:
#: * ``#reward-display`` is hidden with ``display: none`` — ``innerText`` skips hidden subtrees, so the
#:   agent's text view (``get_page_text`` → ``root.innerText``) no longer carries ``Last reward: ...``,
#:   ``Time left: ...`` or ``Episodes done: ...``.  ``element.remove()`` is deliberately **not** used:
#:   ``endEpisode`` still writes ``#episode-id``, which would throw ``TypeError`` on a removed subtree.
#: * ``core.updateDisplay`` is stubbed so a terminal reward is never written back into the HUD.
#: * ``core.startEpisode`` is stubbed so the ``endEpisode`` tail (``core.js:145``) can no longer
#:   re-show the ``START`` cover (``#sync-task-cover``) — that overlay was a plain DOM click away from
#:   re-opening the ``(task, seed)`` instance with a fresh 240s timer (V2 re-roll).
#:
#: ``#query`` (the instruction area the goal pre-read depends on) is untouched.  The reward hook
#: (补丁 B) is appended after this block, so the terminal POST still fires.
AUTO_START_PATCH = """/* __R2G_PATCH_START__ */
(function () {
  function q(name) {
    var m = new RegExp("[?&]" + name + "=([^&#]*)").exec(location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }
  var seed = q("r2g_seed");
  if (seed === null) return;
  var ms = parseInt(q("r2g_ms") || "240000", 10);
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (window.WOB_TASK_READY === true && document.readyState === "complete" && core.cover_div) {
      try {
        Math.seedrandom(seed);
        core.EPISODE_MAX_TIME = ms;
        core.startEpisodeReal();
        /* 安全加固（R1 §2.V1/V2）：HUD 与 START 覆盖层对 agent 不可见/不可用。
           位置必须在 startEpisodeReal() 之后：此时页面自己的 startEpisode() 已建好
           #reward-display 与 #sync-task-cover（cover_div 非空是本分支成立的前提）。
           纯追加；用 display:none 而非 remove（endEpisode 仍要写 #episode-id 等子元素）。 */
        var hud = document.getElementById("reward-display");
        if (hud) { hud.style.display = "none"; }
        core.updateDisplay = function () {}; /* 终局不回写 HUD 文本 */
        core.startEpisode = function () {}; /* endEpisode 尾部不再重新显示 START 覆盖层 */
        clearInterval(timer); /* 只在开局成功后停止轮询 */
      } catch (e) {
        if (tries > 200) { clearInterval(timer); window.__R2G_START_ERROR = String(e); }
      }
    } else if (tries > 200) {
      clearInterval(timer);
      window.__R2G_START_ERROR = "WOB_TASK_READY timeout";
    }
  }, 50);
})();
/* __R2G_PATCH_END__ */"""

#: spec-r2 §4.2 新增① (C3 single-start, M2 加固): the interception branch of the single-start
#: variant, verbatim.  A second load of the same URL in a tab that already started its episode
#: must neither auto-start again **nor** leave a clickable START overlay: at patch-evaluation time
#: ``core`` is already defined, so both episode entry points are stubbed and the HUD/cover are
#: hidden with an injected stylesheet — same V2 hardening as the successful-start branch.
_SINGLE_START_GUARD_BLOCK = """  try {
    if (sessionStorage.getItem("r2g_started") !== null) {
      /* 被拦截加载面同样应用 V2 加固（review-r2 M2）：拦截态下 START 覆盖层可点，
         onclick 在点击期经 core.startEpisodeReal() 动态取函数（core.js:78-80），
         会重开无 Math.seedrandom(seed) 的随机实例并 POST 新 reward 记录，
         经 /latest last-wins 覆盖本格已有终局——必须同层封死。
         stub 在补丁求值期执行（core 此刻已定义，无需等 cover_div）。 */
      core.startEpisodeReal = function () {};
      core.startEpisode = function () {};
      core.updateDisplay = function () {};
      var harden = document.createElement("style");
      harden.textContent = "#reward-display, #sync-task-cover { display: none !important; }";
      (document.head || document.documentElement).appendChild(harden);
      return;
    }
  } catch (e) {}
  /* ↑ 新增①：本 tab 已开局过 → 本次加载不自动开局（页面停在未开局状态，不产生 reward 记录），
     且拦截态与开局成功态受同等 V2 加固（stub + CSS 隐藏，双保险不依赖 onclick 绑定形态） */"""

#: spec-r2 §4.2 新增②: the success branch registers the start so the next load hits 新增①.
_SINGLE_START_SETITEM_LINE = '        try { sessionStorage.setItem("r2g_started", seed); } catch (e) {}\n' "        /* ↑ 新增②：开局成功登记（同 tab 二次 load 被 新增① 拦截） */"

#: spec-r2 §4.2: the single-start variant of 补丁 A — derived from :data:`AUTO_START_PATCH` so it can
#: differ from it in exactly three places (marker line, 新增① guard, 新增② setitem); everything else
#: is byte-identical by construction (T6d).
AUTO_START_PATCH_SINGLE = (
    AUTO_START_PATCH.replace(f"/* {PATCH_START_MARKER} */", f"/* {PATCH_SINGLE_START_MARKER} */", 1)
    .replace(
        "  if (seed === null) return;\n",
        "  if (seed === null) return;\n" + _SINGLE_START_GUARD_BLOCK + "\n",
        1,
    )
    .replace(
        "        clearInterval(timer); /* 只在开局成功后停止轮询 */\n",
        "        clearInterval(timer); /* 只在开局成功后停止轮询 */\n" + _SINGLE_START_SETITEM_LINE + "\n",
        1,
    )
)

#: spec-r2 §4.3 终局信号 (C2, ``--terminal-cue``): a neutral, constant ``EPISODE ENDED`` marker is
#: injected into the page when the episode ends.  It carries no reward/success information (the page
#: reward stays the only authority), it does not POST and it does not touch ``WOB_RAW_REWARD_GLOBAL``
#: — it only lets the agent stop spinning once the episode is over.
TERMINAL_CUE_PATCH = """/* __R2G_TERMINAL_CUE__ */
(function () {
  var orig = core.endEpisode;
  if (typeof orig !== "function") return;
  core.endEpisode = function () {
    var ret = orig.apply(this, arguments);
    try {
      var cue = document.getElementById("r2g-terminal-cue");
      if (!cue) {
        cue = document.createElement("div");
        cue.id = "r2g-terminal-cue";
        cue.style.cssText =
          "position:fixed;left:8px;bottom:8px;font:12px monospace;color:#888;" +
          "background:#fff;padding:2px 6px;z-index:2147483647;";
        document.body.appendChild(cue);
      }
      cue.textContent = "EPISODE ENDED"; /* 中性：无数值、无成败、恒定文本 */
    } catch (e) { /* 绝不干扰 episode 本身 */ }
    return ret;
  };
})();"""

#: spec §3.2 补丁 B (reward hook), verbatim.
REWARD_HOOK_PATCH = """/* __R2G_REWARD_HOOK__ */
(function () {
  var orig = core.endEpisode;
  if (typeof orig !== "function") return;
  core.endEpisode = function () {
    var ret = orig.apply(this, arguments);
    try {
      var m = new RegExp("[?&]r2g_seed=([^&#]*)").exec(location.search);
      var payload = {
        path: location.pathname,
        seed: m ? decodeURIComponent(m[1]) : null,
        reward: arguments.length > 0 ? arguments[0] : null,
        raw: window.WOB_RAW_REWARD_GLOBAL,
        done: window.WOB_DONE_GLOBAL,
        reason: arguments.length > 2 && arguments[2] != null ? String(arguments[2]) : "",
        ts: new Date().toISOString()
      };
      var x = new XMLHttpRequest();
      x.open("POST", "/__r2g_reward", false); /* 同步：确保页面销毁前送达 */
      x.setRequestHeader("Content-Type", "application/json");
      x.send(JSON.stringify(payload));
    } catch (e) { /* 绝不干扰 episode 本身 */ }
    return ret;
  };
})();"""

#: spec-r4 §3.1 (E1/E2) integrity beacons, verbatim.  Runs once at page-load time (before the
#: episode logic): a load whose query carries no ``r2g_seed`` POSTs ``/__r2g_offseed`` (off-seed
#: navigation evidence); a seeded load POSTs ``/__r2g_epstart`` with the seed.  Neither beacon
#: touches the episode, the reward hook or any judging path — append-only server-side records only.
INTEGRITY_BEACON_PATCH = """/* r4 integrity beacons (harness-only): off-seed detection + seeded-load beacon */
(function () {
  var q = function (n) { var m = new RegExp("[?&]" + n + "=([^&#]*)").exec(location.search); return m ? m[1] : null; };
  var beep = function (ep, extra) {
    try { fetch("/__r2g_" + ep, { method: "POST", body: JSON.stringify(Object.assign({ path: location.pathname }, extra || {})) }); } catch (e) {}
  };
  if (!q("r2g_seed")) { beep("offseed"); } else { beep("epstart", { seed: q("r2g_seed") }); }
})();"""

CONTENT_TYPES: Mapping[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class PortInUseError(RuntimeError):
    """Raised when the configured port cannot be bound (spec §3.1: never auto-switch ports)."""


def patch_core_js(source: bytes, *, terminal_cue: bool = False, single_start: bool = False, offseed_beacon: bool = False) -> bytes:
    """Vendored ``core.js`` bytes + the patches as a pure append (spec §3.2, spec-r2 §4.1, spec-r4 §3.1).

    Patch order: vendored source → 补丁 A (``AUTO_START_PATCH_SINGLE`` when ``single_start`` else the
    r1 ``AUTO_START_PATCH``, byte-identical by default) → 补丁 B (reward hook) → ``TERMINAL_CUE_PATCH``
    (only when ``terminal_cue``) → ``INTEGRITY_BEACON_PATCH`` (only when ``offseed_beacon``).
    All flags default to off, which reproduces the r1/r3 bytes exactly (T8a golden).
    """
    auto_start = AUTO_START_PATCH_SINGLE if single_start else AUTO_START_PATCH
    patches = [auto_start, REWARD_HOOK_PATCH]
    if terminal_cue:
        patches.append(TERMINAL_CUE_PATCH)
    if offseed_beacon:
        patches.append(INTEGRITY_BEACON_PATCH)
    appended = "\n" + "\n".join(patches) + "\n"
    return source + appended.encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BeaconLog:
    """Append-only JSONL writer for the r4 integrity beacons (spec-r4 §3.1).

    One file per beacon kind (``offseed.jsonl`` / ``epstart.jsonl``) inside the exp root (the
    rewards file's directory), flushed per line like the rewards file, created on first write.
    ``directory=None`` (memory-only server, no ``--rewards-file``) keeps the endpoints answering
    204 without persisting — the benchmark orchestrator always passes a rewards file.
    """

    def __init__(self, directory: str | Path | None) -> None:
        self.directory = Path(directory) if directory is not None else None
        self._lock = threading.Lock()

    def append(self, filename: str, record: Mapping[str, Any]) -> dict[str, Any]:
        stored = dict(record)
        if self.directory is None:
            return stored
        line = json.dumps(stored, ensure_ascii=False)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / filename).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        return stored


class RewardCollector:
    """In-memory ``(path, seed) -> record`` state (last-wins) plus the append-only JSONL file."""

    def __init__(self, rewards_file: str | Path | None = None) -> None:
        self.rewards_file = Path(rewards_file) if rewards_file is not None else None
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Store one terminal record and append exactly one line to the rewards file (flush per line)."""
        record = dict(payload)
        record["received_at"] = _now()
        key = (str(record.get("path", "")), str(record.get("seed", "")))
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._records[key] = record
            if self.rewards_file is not None:
                self.rewards_file.parent.mkdir(parents=True, exist_ok=True)
                with self.rewards_file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
        return record

    def latest(self, task: str, seed: object) -> dict[str, Any] | None:
        """Latest record whose path ends in ``<task>.html`` and whose seed matches (spec §3.3)."""
        wanted_seed = str(seed)
        wanted_name = f"{task}.html"
        found: dict[str, Any] | None = None
        with self._lock:
            for (path, record_seed), record in self._records.items():
                if record_seed != wanted_seed:
                    continue
                if PurePosixPath(path).name == wanted_name:
                    found = record
        return found

    def count(self) -> int:
        with self._lock:
            return len(self._records)


def _is_valid_reward_payload(payload: Mapping[str, Any]) -> bool:
    """spec §3.3: a non-empty ``path`` and a non-empty ``seed`` are required, otherwise 400."""
    path = payload.get("path")
    seed = payload.get("seed")
    if not isinstance(path, str) or not path.strip():
        return False
    if seed is None or (isinstance(seed, str) and not seed.strip()):
        return False
    return isinstance(seed, (str, int, float))


def resolve_request_path(root: Path, raw_path: str) -> Path | None:
    """Map a URL path to a file inside ``root``; ``None`` for traversal attempts and directories."""
    decoded = urllib.parse.unquote(raw_path)
    if "\x00" in decoded:
        return None
    parts = [part for part in PurePosixPath(decoded).parts if part not in ("/", "")]
    if any(part == ".." for part in parts):
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root.joinpath(*parts)).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_CONTENT_TYPE)


def _make_handler(
    root: Path, collector: RewardCollector, *, terminal_cue: bool = False, single_start: bool = False, offseed_beacon: bool = False, beacons: BeaconLog | None = None
) -> type[BaseHTTPRequestHandler]:
    beacon_log = beacons if beacons is not None else BeaconLog(None)

    class MiniWobHandler(BaseHTTPRequestHandler):
        server_version = "MiniWobPatchServer/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            logger.debug("miniwob_server: %s", format % args)

        # -- responses ------------------------------------------------------------------------

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _send_no_content(self) -> None:
            """204 without a body (RFC 7230: no Content-Length/Content-Type on 204)."""
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _send_file(self, path: Path) -> None:
            try:
                body = path.read_bytes()
            except OSError:  # pragma: no cover - race with a deleted file
                self._send_json(404, {"error": "not_found", "path": self.path})
                return
            if self.path.split("?", 1)[0].endswith(CORE_JS_SUFFIX):
                body = patch_core_js(body, terminal_cue=terminal_cue, single_start=single_start, offseed_beacon=offseed_beacon)
                self._send(200, body, "text/javascript")
                return
            self._send(200, body, content_type_for(path))

        # -- GET ------------------------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            request_path, _, query = self.path.partition("?")
            if request_path == HEALTH_PATH:
                self._send_json(
                    200,
                    {"served_root": str(root), "patched": True, "rewards": collector.count()},
                )
                return
            if request_path == REWARD_LATEST_PATH:
                self._serve_latest(query)
                return
            if request_path == REWARD_PATH or request_path.startswith(REWARD_PATH + "/"):
                self._send_json(404, {"error": "not_found", "path": request_path})
                return
            candidate = resolve_request_path(root, request_path)
            if candidate is None:
                self._send_json(404, {"error": "not_found", "path": request_path})
                return
            self._send_file(candidate)

        def _serve_latest(self, query: str) -> None:
            params = urllib.parse.parse_qs(query)
            task = (params.get("task") or [""])[0]
            seed = (params.get("seed") or [""])[0]
            if not task or not seed:
                self._send_json(404, {"error": "no_reward"})
                return
            record = collector.latest(task, seed)
            if record is None:
                self._send_json(404, {"error": "no_reward"})
                return
            self._send_json(200, record)

        # -- POST -----------------------------------------------------------------------------

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            request_path, _, _ = self.path.partition("?")
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if request_path in (OFFSEED_PATH, EPSTART_PATH):
                self._serve_beacon(OFFSEED_FILENAME if request_path == OFFSEED_PATH else EPSTART_FILENAME, raw)
                return
            if request_path != REWARD_PATH:
                self._send_json(404, {"error": "not_found", "path": request_path})
                return
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json body"})
                return
            if not isinstance(payload, Mapping) or not _is_valid_reward_payload(payload):
                self._send_json(400, {"error": "path and seed are required"})
                return
            record = collector.record(payload)
            self._send_json(200, {"ok": True, "received_at": record["received_at"]})

        def _serve_beacon(self, filename: str, raw: bytes) -> None:
            """spec-r4 §3.1: best-effort JSON body (failure = empty strings), always 204."""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                payload = None
            if not isinstance(payload, Mapping):
                payload = {}
            path = payload.get("path")
            record: dict[str, Any] = {"ts": _now(), "path": path if isinstance(path, str) else ""}
            if filename == EPSTART_FILENAME:
                seed = payload.get("seed")
                record["seed"] = seed if isinstance(seed, str) else ("" if seed is None else str(seed))
            beacon_log.append(filename, record)
            self._send_no_content()

    return MiniWobHandler


class MiniWobServer:
    """Threaded patch-server over a vendored MiniWoB++ tree (spec §3.1)."""

    def __init__(
        self,
        root: str | Path,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        rewards_file: str | Path | None = None,
        terminal_cue: bool = False,
        single_start: bool = False,
        offseed_beacon: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        self.host = host
        self._requested_port = port
        self.terminal_cue = bool(terminal_cue)
        self.single_start = bool(single_start)
        self.offseed_beacon = bool(offseed_beacon)
        self.collector = RewardCollector(rewards_file)
        # spec-r4 §3.1: beacon JSONL files live in the exp root (the rewards file's directory).
        self.beacons = BeaconLog(Path(rewards_file).parent if rewards_file is not None else None)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self, *, background: bool = False) -> None:
        """Bind and start serving; ``background=True`` runs ``serve_forever`` in a daemon thread."""
        if self._httpd is not None:
            return
        if not self.root.is_dir():
            raise FileNotFoundError(f"serving root does not exist: {self.root}")
        try:
            self._httpd = ThreadingHTTPServer(
                (self.host, self._requested_port),
                _make_handler(self.root, self.collector, terminal_cue=self.terminal_cue, single_start=self.single_start, offseed_beacon=self.offseed_beacon, beacons=self.beacons),
            )
        except OSError as exc:
            raise PortInUseError(f"cannot bind {self.host}:{self._requested_port} ({exc}); free the port or pick another one explicitly") from exc
        self._httpd.daemon_threads = True
        if background:
            self._thread = threading.Thread(target=self._httpd.serve_forever, name="miniwob-patch-server", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._httpd = None

    def serve_forever(self) -> None:
        if self._httpd is None:
            self.start()
        assert self._httpd is not None  # for type checkers
        self._httpd.serve_forever()

    # -- introspection -----------------------------------------------------------------------

    @property
    def port(self) -> int:
        """Bound port (resolved when ``port=0`` was requested by the tests)."""
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self._requested_port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self) -> "MiniWobServer":
        self.start(background=True)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MiniWoB++ patch-serving HTTP server (spec §3)")
    parser.add_argument("--root", required=True, help="vendored miniwob_html/ directory")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--rewards-file", default=None, help="JSONL destination for terminal records (memory-only when omitted)")
    parser.add_argument("--terminal-cue", action="store_true", help="spec-r2 §4.3 (C2): append the neutral EPISODE ENDED terminal-cue patch")
    parser.add_argument("--single-start", action="store_true", help="spec-r2 §4.2 (C3): auto-start at most once per tab (sessionStorage gate)")
    parser.add_argument("--offseed-beacon", action="store_true", help="spec-r4 §3.1 (E1/E2): append the off-seed/epstart integrity beacons (judgement-neutral)")
    args = parser.parse_args(argv)

    server = MiniWobServer(
        args.root, host=args.host, port=args.port, rewards_file=args.rewards_file, terminal_cue=args.terminal_cue, single_start=args.single_start, offseed_beacon=args.offseed_beacon
    )
    try:
        server.start()
    except (PortInUseError, FileNotFoundError) as exc:
        logger.error("miniwob_server: %s", exc)
        return 2
    if args.rewards_file is None:
        logger.warning("miniwob_server: no --rewards-file given, terminal records stay in memory only")
    logger.info("miniwob_server: serving %s from %s", server.base_url, server.root)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("miniwob_server: interrupted, shutting down")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
