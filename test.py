"""Capture / validation harness for PR #15 on a 2021+ (new-style) Acaia scale.

PR #15 ("Decode button payloads by walking the record chain") was only tested on
an original Pearl (old-style, service 1820 / char 2a80). Its author flagged that
tag ``0x0b`` widths in particular need a second opinion on 2021+ firmware.

This script drives a *new-style* scale through the ``aioacaia`` connection stack
(so auth + heartbeats keep it streaming), feeds every notification through the
*new* record-chain parser, and logs, for each frame:

* the raw notification bytes,
* the decoded message object, and
* a per-record ``[tag][body]`` breakdown that stops and flags anything the walk
  can't explain (unknown tag, truncated body, unknown button key).

Everything is written verbatim to a ``capture-*.log`` file so real frames can be
turned into new fixtures afterwards. The console shows the interesting frames
(button presses and anomalies) so decoding can be validated live.

Usage
-----
    .venv/bin/python test.py [MAC_ADDRESS] [--old-style]

The address is taken from, in order: the CLI argument, a ``mac.txt`` file next to
this script (git-ignored, same convention as run.py), or a BLE scan.

While it runs, physically operate the scale and type short notes + Enter to drop
timestamped markers into the log (e.g. ``put 55g``, ``pressed stop``). Prefix a
line with ``!`` to send a command instead: ``!tare``, ``!timer`` (start/stop
toggle), ``!reset``. Type ``q`` + Enter (or Ctrl+C) to stop and print a summary.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bleak.backends.characteristic import BleakGATTCharacteristic

from aioacaia.const import HEADER1, HEADER2
from aioacaia.discovery import find_acaia_devices
from aioacaia.exceptions import (
    AcaiaMessageError,
    AcaiaMessageTooLong,
    AcaiaMessageTooShort,
)
from aioacaia.messages import (
    ButtonMessage,
    ButtonType,
    Settings,
    TimerMessage,
    WeightMessage,
)

# NOTE: the underscore-prefixed names below are internal to aioacaia. They are
# imported on purpose so this harness stays byte-for-byte in sync with the exact
# record widths and button map the PR #15 parser uses.
from aioacaia.parser import (  # noqa: PLC2701
    _BATTERY_TAG,
    _BUTTON_TYPES,
    _RECORD_WIDTHS,
    _UNKNOWN_TAG_0B,
    MessageType,
    decode,
    decode_time,
    decode_weight,
)
from aioacaia.scale import AcaiaScale

_HEADER = bytes((HEADER1, HEADER2))

# Command bytes (frame[2]) that carry a decodable body.
_EVENT_COMMAND = 0x0C
_SETTINGS_COMMAND = 0x08

# Human-readable names for the record tags used in the breakdown.
_TAG_NAMES: dict[int, str] = {
    int(MessageType.WEIGHT): "weight",
    int(MessageType.TIMER): "timer",
    int(MessageType.BUTTON): "button/key",
    _BATTERY_TAG: "battery",
    _UNKNOWN_TAG_0B: "0x0b (unexplained)",
}
_BUTTON_NAMES: dict[int, str] = {code: bt.value for code, (bt, _tr) in _BUTTON_TYPES.items()}


def _msg_type_name(value: int) -> str:
    try:
        return MessageType(value).name
    except ValueError:
        return f"0x{value:02x}"


def _summarize(msg: object) -> str:
    """One-line human summary of a decoded message."""
    if isinstance(msg, ButtonMessage):
        parts = [f"button={msg.button.value}"]
        if msg.timer_running is not None:
            parts.append(f"timer_running={msg.timer_running}")
        if msg.time is not None:
            parts.append(f"time={msg.time:g}s")
        if msg.weight is not None:
            parts.append(f"weight={msg.weight:g}g")
        return " ".join(parts)
    if isinstance(msg, WeightMessage):
        return f"weight={msg.weight:g}g"
    if isinstance(msg, TimerMessage):
        return f"time={msg.time:g}s"
    if isinstance(msg, Settings):
        return (
            f"battery={msg.battery}% units={msg.units} "
            f"auto_off={msg.auto_off} beep={msg.beep_on}"
        )
    return repr(msg)


@dataclass
class ChainReport:
    """Result of walking one ``[tag][body]`` record chain."""

    lines: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags


def annotate_chain(chain: bytes) -> ChainReport:
    """Describe every record in a chain, flagging anything the walk can't parse.

    Mirrors ``aioacaia.parser._walk_records`` but keeps going verbosely and, on
    the first thing it can't explain, prints the leftover bytes so a wrong width
    (e.g. tag ``0x0b`` on 2021+ firmware) is obvious.
    """
    report = ChainReport()
    index = 0
    total = len(chain)
    while index < total:
        tag = chain[index]
        if tag == MessageType.BUTTON:
            if index + 2 > total:
                flag = f"[{index}] tag 0x08 button/key truncated: {chain[index:].hex()}"
                report.lines.append(flag + "   <-- CHECK")
                report.flags.append(flag)
                break
            key = chain[index + 1]
            name = _BUTTON_NAMES.get(key, f"UNKNOWN key {key}")
            report.lines.append(f"[{index}] tag 0x08 button/key: key={key} ({name})")
            if key not in _BUTTON_NAMES:
                report.flags.append(f"[{index}] unknown button key {key}")
            index += 2
            continue
        width = _RECORD_WIDTHS.get(tag)
        name = _TAG_NAMES.get(tag, f"0x{tag:02x}")
        if width is None:
            flag = f"[{index}] tag 0x{tag:02x} UNKNOWN -> walk stops; leftover: {chain[index:].hex()}"
            report.lines.append(flag + "   <-- CHECK")
            report.flags.append(flag)
            break
        body = bytearray(chain[index + 1 : index + 1 + width])
        if len(body) < width:
            flag = (
                f"[{index}] tag 0x{tag:02x} {name} needs {width} bytes, "
                f"got {len(body)}: {body.hex()}"
            )
            report.lines.append(flag + "   <-- CHECK")
            report.flags.append(flag)
            break
        detail = f"body={body.hex()}"
        if tag == MessageType.WEIGHT:
            try:
                detail += f" -> {decode_weight(body):g} g"
            except AcaiaMessageError as ex:
                detail += f" -> WEIGHT DECODE ERROR: {ex.message}"
                report.flags.append(f"[{index}] weight decode error: {ex.message}")
        elif tag == MessageType.TIMER:
            try:
                detail += f" -> {decode_time(body):g} s"
            except AcaiaMessageError as ex:
                detail += f" -> TIME DECODE ERROR: {ex.message}"
                report.flags.append(f"[{index}] time decode error: {ex.message}")
        report.lines.append(f"[{index}] tag 0x{tag:02x} {name} {detail}")
        index += 1 + width
    return report


@dataclass
class FixtureCandidate:
    """A distinct button/heartbeat payload worth pasting into the fixtures."""

    msg_type: int
    payload_hex: str
    summary: str
    first_at: float
    clean: bool


class CaptureSession:
    """Reassembles notifications into frames and logs raw + decoded + records."""

    def __init__(self, log: logging.Logger) -> None:
        self.log = log
        self.log_path: Path | None = None
        self._buffer = bytearray()
        self._start_mono = time.monotonic()
        self._notif_count = 0
        self._frame_count = 0
        self._counts: dict[str, int] = {}
        self._fixtures: dict[str, FixtureCandidate] = {}
        self._attention: list[str] = []
        self._last_start_mono: float | None = None
        self._last_weight: float | None = None

    # --- clock helpers -----------------------------------------------------
    def _elapsed(self) -> float:
        return time.monotonic() - self._start_mono

    # --- notification entry point -----------------------------------------
    def handle_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Bleak notify callback: log the raw bytes and decode any full frames."""
        self._notif_count += 1
        self.log.debug(
            "NOTIFY #%d %s (+%.3fs) raw=%s",
            self._notif_count,
            datetime.now().strftime("%H:%M:%S.%f")[:-3],
            self._elapsed(),
            bytes(data).hex(),
        )
        self._buffer += data
        try:
            self._drain()
        except Exception:  # never let a parsing bug kill the capture
            self.log.exception("Unexpected error while decoding buffer")

    def marker(self, text: str) -> None:
        """Record a user note, timestamped, so presses map to frames."""
        self.log.info(">> MARKER (+%.1fs): %s", self._elapsed(), text)

    def tick(self) -> None:
        """Periodic liveness line so the console shows it is still capturing."""
        weight = "n/a" if self._last_weight is None else f"{self._last_weight:g}g"
        self.log.info(
            "... capturing (+%.0fs) notifications=%d frames=%d last_weight=%s",
            self._elapsed(),
            self._notif_count,
            self._frame_count,
            weight,
        )

    # --- framing (mirrors AcaiaScale._extract_messages) --------------------
    def _drain(self) -> None:
        while self._buffer:
            start = self._buffer.find(_HEADER)
            if start < 0:
                # No header: keep a trailing lone HEADER1, drop the rest.
                if self._buffer[-1] == HEADER1:
                    self._buffer = bytearray((HEADER1,))
                else:
                    self.log.debug("Ignoring non-header bytes: %s", self._buffer.hex())
                    self._buffer.clear()
                return
            if start > 0:
                self.log.debug(
                    "Dropping %d bytes before header: %s",
                    start,
                    self._buffer[:start].hex(),
                )
                del self._buffer[:start]

            pre = bytes(self._buffer)
            try:
                msg, remaining = decode(self._buffer)
            except AcaiaMessageTooShort:
                return  # partial frame, wait for the next notification
            except AcaiaMessageTooLong:
                if self._skip_to_next_frame() is None:
                    return
                continue
            except AcaiaMessageError as ex:
                hexb = bytes(ex.bytes_recvd).hex()
                self.log.warning("DECODE ERROR: %s bytes=%s", ex.message, hexb)
                self._attention.append(f"decode error '{ex.message}': {hexb}")
                del self._buffer[:2]
                continue

            consumed = pre[: len(pre) - len(remaining)]
            self._buffer = bytearray(remaining)
            self._on_frame(consumed, msg)

    def _skip_to_next_frame(self) -> int | None:
        nxt = self._buffer.find(_HEADER, 2)
        while nxt >= 0:
            try:
                decode(self._buffer[nxt:])
            except AcaiaMessageError:
                nxt = self._buffer.find(_HEADER, nxt + 2)
            else:
                break
        if nxt < 0:
            return None
        self.log.debug("Resyncing: discarding %d bytes before next header", nxt)
        del self._buffer[:nxt]
        return nxt

    # --- per-frame handling ------------------------------------------------
    def _on_frame(self, consumed: bytes, msg: object) -> None:
        self._frame_count += 1
        command = consumed[2]
        length = consumed[3]

        # Decide up front whether this frame is worth showing on the console.
        interesting = False
        if command == _EVENT_COMMAND:
            mt = consumed[4]
            payload = consumed[5:-2]
            interesting = mt == MessageType.BUTTON or (
                mt == MessageType.HEARTBEAT and payload[2:3] == bytes([MessageType.BUTTON])
            )
        level = logging.INFO if interesting else logging.DEBUG

        self.log.log(
            level,
            "FRAME #%d cmd=0x%02x len=%d raw=%s",
            self._frame_count,
            command,
            length,
            consumed.hex(),
        )
        if msg is not None:
            name = type(msg).__name__
            self._counts[name] = self._counts.get(name, 0) + 1
            self.log.log(level, "  DECODED %s -> %s", name, _summarize(msg))
            if isinstance(msg, WeightMessage):
                self._last_weight = msg.weight
        else:
            self.log.log(level, "  DECODED (no message for command 0x%02x)", command)

        if command != _EVENT_COMMAND:
            return

        msg_type = consumed[4]
        payload = consumed[5:-2]
        self.log.log(
            level,
            "  event msg_type=%d (%s) payload=%s",
            msg_type,
            _msg_type_name(msg_type),
            payload.hex(),
        )

        if msg_type == MessageType.BUTTON:
            report = annotate_chain(bytes([MessageType.BUTTON]) + payload)
            self._emit_records(report, level, consumed)
            self._record_fixture(msg_type, payload, msg, report)
            self._timer_crosscheck(msg)
        elif msg_type == MessageType.HEARTBEAT:
            wrapper, wrapped = payload[:2], payload[2:]
            self.log.log(
                level,
                "  heartbeat wrapper=%s wrapped=%s",
                wrapper.hex(),
                wrapped.hex(),
            )
            report = annotate_chain(bytes(wrapped))
            self._emit_records(report, level, consumed)
            if wrapped[:1] == bytes([MessageType.BUTTON]):
                self._record_fixture(msg_type, payload, msg, report)
                self._timer_crosscheck(msg)

    def _emit_records(
        self, report: ChainReport, level: int, consumed: bytes
    ) -> None:
        self.log.log(level, "  records:")
        for line in report.lines:
            self.log.log(level, "    %s", line)
        if not report.ok:
            for flag in report.flags:
                self.log.warning("  ANOMALY in %s -> %s", consumed.hex(), flag)
            self._attention.append(f"frame {consumed.hex()} -> {report.flags[0]}")

    def _record_fixture(
        self, msg_type: int, payload: bytes, msg: object, report: ChainReport
    ) -> None:
        key = f"{msg_type}:{payload.hex()}"
        if key in self._fixtures:
            return
        self._fixtures[key] = FixtureCandidate(
            msg_type=int(msg_type),
            payload_hex=payload.hex(),
            summary=_summarize(msg),
            first_at=self._elapsed(),
            clean=report.ok,
        )

    def _timer_crosscheck(self, msg: object) -> None:
        """Compare a decoded stop/reset time against wall-clock since START."""
        if not isinstance(msg, ButtonMessage):
            return
        if msg.button is ButtonType.START:
            self._last_start_mono = time.monotonic()
            self.log.info("  [timer] START captured; timing the next stop/reset")
        elif msg.button in (ButtonType.STOP, ButtonType.RESET) and msg.time is not None:
            if self._last_start_mono is not None:
                wall = time.monotonic() - self._last_start_mono
                verdict = "OK" if abs(msg.time - wall) < 2.0 else "MISMATCH <-- CHECK"
                self.log.info(
                    "  [timer] decoded=%.1fs vs wall-clock=%.1fs -> %s",
                    msg.time,
                    wall,
                    verdict,
                )
                self._last_start_mono = None
            else:
                self.log.info(
                    "  [timer] decoded=%.1fs (no START captured to compare)", msg.time
                )

    # --- end of session ----------------------------------------------------
    def summary(self) -> None:
        log = self.log
        log.info("")
        log.info("=" * 72)
        log.info("CAPTURE SUMMARY")
        log.info(
            "notifications=%d frames=%d duration=%.1fs",
            self._notif_count,
            self._frame_count,
            self._elapsed(),
        )
        if self._counts:
            log.info("message types:")
            for name, count in sorted(self._counts.items()):
                log.info("  %-16s %d", name, count)

        log.info("")
        if self._fixtures:
            log.info("FIXTURE CANDIDATES (paste into tests/fixtures/messages.py):")
            for fc in self._fixtures.values():
                flag = (
                    ""
                    if fc.clean
                    else "   # <-- record walk incomplete; CHECK widths (e.g. tag 0x0b)"
                )
                log.info(
                    '    frame(%d, bytes.fromhex("%s"))  # %s%s',
                    fc.msg_type,
                    fc.payload_hex,
                    fc.summary,
                    flag,
                )
        else:
            log.info(
                "No button/heartbeat frames captured -- press the scale's "
                "buttons (tare/start/stop/reset) during the next run."
            )

        log.info("")
        if self._attention:
            log.info("NEEDS ATTENTION (%d):", len(self._attention))
            for item in self._attention:
                log.info("  %s", item)
        else:
            log.info(
                "No anomalies flagged: the PR #15 parser explained every captured "
                "record chain on this scale."
            )
        log.info("")
        if self.log_path is not None:
            log.info("Full raw + debug log: %s", self.log_path)
        log.info("=" * 72)


def _setup_logging() -> tuple[logging.Logger, Path]:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = Path(__file__).with_name(f"capture-{ts}.log")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    log = logging.getLogger("capture")
    log.setLevel(logging.DEBUG)
    log.addHandler(file_handler)
    log.addHandler(console)
    log.propagate = False

    # Send aioacaia's own debug logs to the same file for cross-reference.
    lib = logging.getLogger("aioacaia")
    lib.setLevel(logging.DEBUG)
    lib.addHandler(file_handler)
    lib.propagate = False

    return log, log_path


def _parse_args() -> tuple[str | None, bool]:
    address: str | None = None
    old_style = False
    for arg in sys.argv[1:]:
        if arg in ("--old-style", "--old"):
            old_style = True
        elif arg in ("--new-style", "--new"):
            old_style = False
        elif not arg.startswith("-"):
            address = arg
    return address, old_style


async def _resolve_address(address: str | None, log: logging.Logger) -> str:
    if address:
        return address
    mac_file = Path(__file__).with_name("mac.txt")
    if mac_file.exists():
        address = mac_file.read_text(encoding="utf-8").strip()
        log.info("Using MAC from mac.txt: %s", address)
        return address
    log.info("No address given and no mac.txt found; scanning for Acaia devices...")
    found = await find_acaia_devices()
    if not found:
        raise SystemExit(
            "No Acaia scale found. Pass a MAC address, or write one to mac.txt."
        )
    log.info("Discovered %d device(s); using the first: %s", len(found), found[0])
    return found[0]


_INSTRUCTIONS = """
Suggested validation sequence (narrate each step with a typed marker):
  1. Put a known weight on the scale (e.g. 55 g)   -> type: put 55g
  2. Press TARE on the scale                       -> type: tare
  3. Press START, wait ~10 s, press STOP           -> type: start / stop
     (the decoded stop time is cross-checked against the wall clock)
  4. Press RESET                                   -> type: reset
  5. Repeat 3-4 with weight still on the scale     (captures weight+time chains)
Watch the START frames for tag 0x0b -- that is the width PR #15 is unsure about.
Type a note + Enter for a marker, '!tare/!timer/!reset' to send a command,
or 'q' to stop.
"""


async def _run_command(scale: AcaiaScale, log: logging.Logger, cmd: str) -> None:
    try:
        if cmd == "tare":
            await scale.tare()
        elif cmd in ("timer", "start", "stop", "toggle"):
            await scale.start_stop_timer()
        elif cmd == "reset":
            await scale.reset_timer()
        else:
            log.info("Unknown command '!%s' (try !tare, !timer, !reset)", cmd)
            return
        log.info(">> sent command: !%s", cmd)
    except Exception as ex:  # noqa: BLE001 - diagnostic tool, surface anything
        log.warning("command !%s failed: %s", cmd, ex)


def _install_stdin(
    loop: asyncio.AbstractEventLoop,
    session: CaptureSession,
    scale: AcaiaScale,
    stop_event: asyncio.Event,
) -> bool:
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError):
        return False

    def _on_readable() -> None:
        line = sys.stdin.readline()
        if line == "":  # EOF
            loop.remove_reader(fd)
            stop_event.set()
            return
        text = line.strip()
        if not text:
            return
        if text.lower() in ("q", "quit", "exit"):
            stop_event.set()
            return
        if text.startswith("!"):
            session.marker(f"command {text}")
            loop.create_task(_run_command(scale, session.log, text[1:].lower()))
        else:
            session.marker(text)

    try:
        loop.add_reader(fd, _on_readable)
    except (NotImplementedError, ValueError, OSError):
        return False
    return True


async def _ticker(session: CaptureSession, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10.0)
        except TimeoutError:
            session.tick()


async def main() -> None:
    log, log_path = _setup_logging()
    session = CaptureSession(log)
    session.log_path = log_path

    address_arg, old_style = _parse_args()
    address = await _resolve_address(address_arg, log)

    scale = AcaiaScale(address, is_new_style_scale=not old_style)
    log.info(
        "Connecting to %s as %s-style scale (notify=%s, write=%s)...",
        address,
        "old" if old_style else "new",
        scale._notify_char_id,  # noqa: SLF001 - diagnostic visibility
        scale._default_char_id,  # noqa: SLF001
    )

    await scale.connect(callback=session.handle_notification)
    log.info("Connected. Logging to %s", log_path)
    log.info(_INSTRUCTIONS)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if not _install_stdin(loop, session, scale, stop_event):
        log.info("(stdin markers unavailable; run interactively for markers)")
    ticker = asyncio.create_task(_ticker(session, stop_event))

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        ticker.cancel()
        try:
            await scale.disconnect()
        except Exception as ex:  # noqa: BLE001
            log.debug("Error during disconnect: %s", ex)
        session.summary()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass