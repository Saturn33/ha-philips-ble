"""The hub: one per config entry; owns the boards, the lamps and the stores."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import addressing, planner, transport
from .beacons import RESTORED, Beacon, PresenceTracker, parse_beacon
from .const import (
    AFTER_OUTAGE_RESTORE,
    CONF_BASE_ADDRESS,
    CONF_BOARDS,
    CONF_COMMAND_INTERVAL,
    CONF_OUTAGE_SENSOR,
    CONF_POWER_CYCLE_GAP,
    DATA_USED_INDICES,
    DEFAULT_BASE_ADDRESS,
    DEFAULT_COMMAND_INTERVAL,
    DEFAULT_POWER_CYCLE_GAP,
    DOMAIN,
    HEARTBEAT_EVENT,
    HEARTBEAT_TIMEOUT,
    LAMP_ADDRESS,
    LAMP_AFTER_OUTAGE,
    LAMP_MAC,
    LAMP_PROFILE,
    LAMP_REMOTE,
    PRESENCE_INTERVAL,
    RAW_ADV_EVENT,
    REQUIRED_PACKAGE_VERSION,
    SIGNAL_LAMP,
    SIGNAL_REMOTE,
    SIGNAL_REMOTE_ADDED,
    STORAGE_SEQUENCE,
    STORAGE_STATE,
    STORAGE_VERSION,
    SUBENTRY_LAMP,
    TRANSMIT_DURATION,
    TRANSMIT_REPEAT,
)
from .mesh import MeshCodec, MeshMessage
from .profile import PROFILES_DIR, Profile, load_profiles
from .remotes import RemotePress, RemoteTracker
from .sequence import SequenceError, SequenceStore
from .state import UNKNOWN, LampState, apply, apply_press, power_restored
from .transport import Board, BoardUnavailable

_LOGGER = logging.getLogger(__name__)

monotonic = time.monotonic
sleep = asyncio.sleep

SAVE_DELAY = 5.0
OWN_ECHO_WINDOW = 30.0  # seconds: our own advert may come back through another board
OUTAGE_WINDOW = 600.0  # seconds after an outage in which a returning lamp is restored
REBIND_WINDOW = 60.0  # seconds after a lamp got power in which a remote's pairing binds it
PRESS_QUEUE_LIMIT = 10  # card presses waiting for one lamp, the one on the air included
TX_LISTEN_MARGIN = 0.2  # s: the board resumes listening 150 ms after its last advert
BOARD_HYSTERESIS = 6  # dB another board must be better by (smoothed) before a lamp moves to it
BOARD_MIN_DWELL = 30.0  # s on a board before a lamp moves to a better one (not when it is gone)
RSSI_SMOOTHING = 0.2  # weight of a new reading in the smoothed RSSI (two readings a second)
RSSI_REPORT_STEP = 2  # dB change of the smoothed RSSI before the sensor is updated
BOARD_READY_GRACE = 60.0  # seconds after setup before an unready board raises a repair issue
LAST_SEEN_STEP = 60.0  # seconds: "Last beacon" moves on at most this often (fewer state writes)
NEW_LAMP_POLL = 0.5  # seconds between looks for a power-cycled lamp
NEW_LAMP_GAP = 3.0  # seconds of silence that count as "switched off" before the lamp reappears


def remote_key(profile_id: str, source: int) -> str:
    return f"{profile_id}:{source:04x}"


class NoAddressLeft(HomeAssistantError):
    """No unicast address is left above the base address."""


class HubClosed(HomeAssistantError):
    """The hub was unloaded (the entry reloads or stops): it sends and saves nothing any more."""


class PressQueueFull(HomeAssistantError):
    """Too many card presses are waiting for one lamp."""


@dataclass
class RemoteInfo:
    key: str
    profile: str
    source: int
    lamps: list[str]
    missed_total: int = 0
    last_press: datetime | None = None


@dataclass
class LampRuntime:
    subentry_id: str
    profile: Profile
    mac: str
    address: int
    state: LampState
    uncertain: bool = False
    present: bool | None = None
    before_loss: LampState | None = None
    rssi: int | None = None
    board: str | None = None  # device id of the board that last reported RSSI for the lamp
    last_beacon: float | None = None  # time of the last advert taken into account
    after_outage: str = "nothing"
    quiet: bool | None = None  # flag of the last beacon
    last_seen: datetime | None = None  # wall-clock time of the last advert
    silent: bool = False  # silent for the power cycle gap while in active mode
    lost_at_start: bool = False  # without power when the entry stopped, not seen since


def _state_to_dict(state: LampState | None) -> dict[str, Any] | None:
    return None if state is None else asdict(state)


def _state_from_dict(data: dict[str, Any] | None) -> LampState | None:
    if not data:
        return None
    return LampState(data.get("on"), data.get("color_temp"), data.get("level"))  # a stored "scene" is ignored


class Hub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.boards: list[Board] = []
        self.profiles: dict[str, Profile] = {}
        self.profile_errors: list[str] = []
        self.lamps: dict[str, LampRuntime] = {}
        self.heard_sources: set[int] = set()
        self.remotes: dict[str, RemoteInfo] = {}
        self.unknown_remotes: dict[str, float] = {}
        self.ignored_remotes: set[str] = set()
        self._trackers: dict[str, RemoteTracker] = {}
        self._sent_log: dict[tuple[int, int], float] = {}
        self._press_waiters: list[tuple[Profile, asyncio.Future[tuple[str, str]]]] = []
        self._presence: dict[str, PresenceTracker] = {}
        self._pending_restore: dict[str, float] = {}  # lamp → deadline (monotonic)
        self._restored_at: dict[str, float] = {}  # lamp → when it last got power
        self.counters = SequenceStore(Store(hass, STORAGE_VERSION, STORAGE_SEQUENCE))
        self._state_store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_STATE)
        self._stored: dict[str, Any] = {}
        self._codecs: dict[str, MeshCodec] = {}
        self._board_locks: dict[str, asyncio.Lock] = {}
        self._lamp_locks: dict[str, asyncio.Lock] = {}
        self._generation: dict[str, int] = {}
        self._running: dict[str, int] = {}  # lamp → generation of the chain that is running
        self._presses: dict[str, int] = {}  # lamp → card presses accepted and not finished
        self._last_sent: dict[int, float] = {}
        self._last_tx = float("-inf")  # last time a board was sending (it does not listen then)
        self._rssi_avg: dict[tuple[str, str], float] = {}  # (lamp MAC, board) → smoothed RSSI
        self._board_since: dict[str, float] = {}  # lamp → when it last moved to another board
        self._heartbeat: dict[str, float] = {}  # board → last heartbeat (or start/connect)
        self._unsubscribe: list[Callable[[], None]] = []
        self.closed = False

    @property
    def power_cycle_gap(self) -> float:
        return float(self.entry.options.get(CONF_POWER_CYCLE_GAP, DEFAULT_POWER_CYCLE_GAP))

    @property
    def command_interval(self) -> float:
        return float(self.entry.options.get(CONF_COMMAND_INTERVAL, DEFAULT_COMMAND_INTERVAL))

    # Setup and unload

    async def async_setup(self) -> None:
        loaded = await self.hass.async_add_executor_job(load_profiles, PROFILES_DIR)
        self.profiles, self.profile_errors = loaded
        for error in self.profile_errors:
            _LOGGER.error("Profile not loaded: %s", error)
        if self.profile_errors:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "profile_errors",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="profile_errors",
                translation_placeholders={"errors": "\n".join(self.profile_errors)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "profile_errors")
        lamp_subentries = [s for s in self.entry.subentries.values() if s.subentry_type == SUBENTRY_LAMP]
        try:
            await self.counters.async_load(expect_existing=bool(lamp_subentries))
        except SequenceError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "sequence_store_missing",
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="sequence_store_missing",
                data={"entry_id": self.entry.entry_id},
            )
            raise ConfigEntryError(str(err)) from err
        ir.async_delete_issue(self.hass, DOMAIN, "sequence_store_missing")

        self._stored = await self._state_store.async_load() or {}
        self._stored.setdefault("lamps", {})
        self.heard_sources = set(self._stored.get("heard_sources", []))
        for subentry in lamp_subentries:
            profile = self.profiles.get(subentry.data[LAMP_PROFILE])
            if profile is None:
                continue  # its entities stay unavailable; the profile_errors issue explains why
            self.codec(profile)
            seed = self._stored.get("seeds", {}).pop(subentry.data[LAMP_MAC].upper(), {})
            stored = self._stored["lamps"].get(subentry.subentry_id) or seed
            self.lamps[subentry.subentry_id] = LampRuntime(
                subentry_id=subentry.subentry_id,
                profile=profile,
                mac=subentry.data[LAMP_MAC],
                address=subentry.data[LAMP_ADDRESS],
                state=_state_from_dict(stored) or UNKNOWN,
                uncertain=bool(stored.get("uncertain", False)),
                before_loss=_state_from_dict(stored.get("before_loss")),
                board=stored.get("board"),
                after_outage=subentry.data.get(LAMP_AFTER_OUTAGE, "nothing"),
                # Without power when the entry stopped: stays so until a beacon says otherwise
                # (a reload or restart must not show it on for the quiet timeout).
                present=False if stored.get("present") is False else None,
                lost_at_start=stored.get("present") is False,
            )

        self._setup_remotes(lamp_subentries)
        self._unsubscribe.append(self.hass.bus.async_listen(RAW_ADV_EVENT, self._handle_raw_adv))
        self._unsubscribe.append(self.hass.bus.async_listen(HEARTBEAT_EVENT, self._handle_heartbeat))
        await self._async_setup_boards()
        self._setup_presence()

    def _setup_presence(self) -> None:
        now = monotonic()
        for lamp in self.lamps.values():
            tracker = self._presence.setdefault(lamp.profile.id, PresenceTracker(lamp.profile))
            tracker.watch(lamp.mac, now, lost=lamp.present is False)
            # HA keeps a lamp's last advert for minutes, and across a restart for remote scanners
            # (components/bluetooth: the remote scanners' history is stored): one held when the
            # entry starts proves nothing about the lamp now, so only newer adverts count.
            held = transport.last_advert(self.hass, lamp.mac)
            if held is not None:
                lamp.last_beacon = held[1]
        # A @callback, so HA runs it in the event loop (a plain function would go to a worker thread).
        self._unsubscribe.append(
            async_track_time_interval(self.hass, self._presence_timer, timedelta(seconds=PRESENCE_INTERVAL))
        )
        sensor = self.entry.options.get(CONF_OUTAGE_SENSOR)
        if sensor:
            state = self.hass.states.get(sensor)
            if (
                state is not None
                and state.state == STATE_ON
                and dt_util.utcnow() - state.last_changed < timedelta(seconds=OUTAGE_WINDOW)
            ):
                self._outage_started()
            self._unsubscribe.append(
                async_track_state_change_event(self.hass, [sensor], self._outage_changed)
            )

    def _setup_remotes(self, lamp_subentries) -> None:
        self.ignored_remotes = set(self._stored.get("ignored_remotes", []))
        for key, data in self._stored.get("remotes", {}).items():
            lamps = [sub_id for sub_id in data.get("lamps", []) if sub_id in self.entry.subentries]
            last_press = dt_util.parse_datetime(data["last_press"]) if data.get("last_press") else None
            self.remotes[key] = RemoteInfo(
                key, data["profile"], data["source"], lamps, data.get("missed_total", 0), last_press
            )
        # The wizard's binding is taken once; later changes (options, removal) win over it.
        merged = self._stored.setdefault("merged_bindings", [])
        for subentry in lamp_subentries:
            key = subentry.data.get(LAMP_REMOTE)
            if not key or subentry.subentry_id in merged:
                continue
            merged.append(subentry.subentry_id)
            profile_id, source = key.split(":")
            remote = self.remotes.setdefault(key, RemoteInfo(key, profile_id, int(source, 16), []))
            if subentry.subentry_id not in remote.lamps:
                remote.lamps.append(subentry.subentry_id)
        for key in self.remotes:
            # Heard as unknown before it was bound (e.g. pressed in the wizard before "Finish").
            ir.async_delete_issue(self.hass, DOMAIN, f"new_remote_{key}")
        stored_trackers = self._stored.get("trackers", {})
        for profile in self.profiles.values():
            tracker = RemoteTracker(profile, self.own_addresses())
            tracker.restore(stored_trackers.get(profile.id, {}))
            self._trackers[profile.id] = tracker

    async def _async_setup_boards(self) -> None:
        self.boards = await transport.async_boards(self.hass, self.entry.options.get(CONF_BOARDS, []))
        sensors = {}
        for board in self.boards:
            entity_id = transport.package_sensor(self.hass, board)
            sensor_disabled = transport.package_sensor_disabled(self.hass, board)
            self._board_sensor_disabled(board, sensor_disabled)
            if sensor_disabled:
                continue
            if entity_id is None:
                _LOGGER.warning("%s has no BLE TX package sensor (ble-adv-tx package missing?)", board.name)
                self._board_outdated(board, None)
                continue
            sensors[entity_id] = board
            if transport.package_version(self.hass, board) is not None:
                await self._async_board_connected(board)
        if sensors:
            self._unsubscribe.append(
                async_track_state_change_event(self.hass, list(sensors), self._make_sensor_listener(sensors))
            )
        self._unsubscribe.append(async_call_later(self.hass, BOARD_READY_GRACE, self._check_boards_ready))

    @callback
    def _check_boards_ready(self, _now=None) -> None:
        for board in self.boards:
            # A disabled package sensor already has its own repair (_board_sensor_disabled).
            if transport.package_sensor_disabled(self.hass, board):
                continue
            if not self.board_ready(board):
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"board_not_ready_{board.device_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="board_not_ready",
                    translation_placeholders={"board": board.name, "action": f"esphome.{board.service}"},
                )

    @callback
    def _board_sensor_disabled(self, board: Board, disabled: bool) -> None:
        issue_id = f"board_sensor_disabled_{board.device_id}"
        if not disabled:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="board_sensor_disabled",
            translation_placeholders={"board": board.name},
        )

    @callback
    def _board_outdated(self, board: Board, version: str | None) -> None:
        """Raise or clear the "update the board's firmware" issue for the reported package version."""
        try:
            current = int(version) if version is not None else None
        except ValueError:
            current = None
        issue_id = f"board_outdated_{board.device_id}"
        if current is not None and current >= REQUIRED_PACKAGE_VERSION:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="board_outdated",
            translation_placeholders={
                "board": board.name,
                "version": version or "-",
                "required": str(REQUIRED_PACKAGE_VERSION),
            },
        )

    def _make_sensor_listener(self, sensors: dict[str, Board]):
        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            # `unknown`: connected but the package has not reported yet (see transport.package_version).
            was = old is not None and old.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            now = new is not None and new.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            board = sensors[event.data["entity_id"]]
            if now and not was:
                self.entry.async_create_background_task(
                    self.hass, self._async_board_connected(board), f"ble_mesh_lights connect {board.node}"
                )
            elif now:
                self._board_outdated(board, new.state)  # a new value without a reconnect

        return _changed

    def board_ready(self, board: Board) -> bool:
        """Connected (action and package sensor) and, on package 2 or later, a heartbeat within
        HEARTBEAT_TIMEOUT. HA notices a lost ESPHome connection only after up to a minute, and an
        action called meanwhile is lost without an error. The heartbeat is an event because HA's
        ESPHome integration drops a re-sent unchanged state (components/esphome/entry_data.py,
        async_update_state), while it fires every ESPHome event (manager.py, async_on_service_call)."""
        if not transport.board_ready(self.hass, board):
            return False
        if not transport.has_heartbeat(self.hass, board):
            return True
        last = self._heartbeat.setdefault(board.device_id, monotonic())  # a new board gets its grace
        return monotonic() - last < HEARTBEAT_TIMEOUT

    @callback
    def _handle_heartbeat(self, event: Event) -> None:
        device_id = event.data.get("device_id")
        for board in self.boards:
            if board.device_id == device_id:
                self._heartbeat[device_id] = monotonic()
                if self.board_ready(board):
                    ir.async_delete_issue(self.hass, DOMAIN, f"board_not_ready_{device_id}")

    async def _async_board_connected(self, board: Board) -> None:
        """A board (re)connected: it forwards nothing until the receiver is set up again."""
        self._heartbeat[board.device_id] = monotonic()  # time for its first heartbeat
        self._board_outdated(board, transport.package_version(self.hass, board))
        if self.board_ready(board):
            ir.async_delete_issue(self.hass, DOMAIN, f"board_not_ready_{board.device_id}")
        try:
            await transport.async_setup_receiver(self.hass, board)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not set up receiving on %s: %s", board.name, err)

    async def async_unload(self) -> None:
        """Stop for good. A reloaded entry gets a new hub on the same stores: this one must not send
        or save after it, or its older counters could overwrite the new hub's (and a counter that
        goes back makes a lamp ignore HA)."""
        self.closed = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for sub_id in self._generation:
            self._generation[sub_id] += 1  # running chains stop before their next command
        for _, future in self._press_waiters:
            if not future.done():
                future.set_exception(HubClosed("BLE Mesh Lights reloaded"))
        # Let a command that is on the air finish and be applied before the final save.
        for lock in [*self._lamp_locks.values(), *self._board_locks.values()]:
            async with lock:
                pass
        await self._state_store.async_save(self._data_to_store())

    # State

    def _data_to_store(self) -> dict[str, Any]:
        lamps = {k: v for k, v in self._stored.get("lamps", {}).items() if k in self.entry.subentries}
        for sub_id, lamp in self.lamps.items():
            lamps[sub_id] = {
                **asdict(lamp.state),
                "uncertain": lamp.uncertain,
                "before_loss": _state_to_dict(lamp.before_loss),
                "board": lamp.board,
                "present": lamp.present,
            }
        return {
            **self._stored,
            "lamps": lamps,
            "heard_sources": sorted(self.heard_sources),
            "remotes": {
                key: {
                    "profile": r.profile,
                    "source": r.source,
                    "lamps": list(r.lamps),
                    "missed_total": r.missed_total,
                    "last_press": r.last_press.isoformat() if r.last_press else None,
                }
                for key, r in self.remotes.items()
            },
            "merged_bindings": [
                sub_id
                for sub_id in self._stored.get("merged_bindings", [])
                if sub_id in self.entry.subentries
            ],
            "ignored_remotes": sorted(self.ignored_remotes),
            "trackers": {
                **self._stored.get("trackers", {}),
                **{profile_id: tracker.snapshot() for profile_id, tracker in self._trackers.items()},
            },
        }

    @callback
    def async_schedule_save(self) -> None:
        if not self.closed:
            self._state_store.async_delay_save(self._data_to_store, SAVE_DELAY)

    @callback
    def lamp_changed(self, subentry_id: str) -> None:
        async_dispatcher_send(self.hass, SIGNAL_LAMP.format(subentry_id))
        self.async_schedule_save()

    def set_assumed_state(self, subentry_id: str, state: LampState) -> None:
        """The user says what the lamp looks like now (nothing is sent); a running chain stops."""
        self.cancel_pending(subentry_id)
        lamp = self.lamps[subentry_id]
        lamp.state = state
        lamp.uncertain = False
        self.lamp_changed(subentry_id)

    # Sending

    def remote_sources(self) -> set[int]:
        """Every address a remote has sent as: never send as one of them."""
        return self.heard_sources | {remote.source for remote in self.remotes.values()}

    def own_addresses(self) -> set[int]:
        return {lamp.address for lamp in self.lamps.values()} | set(self._last_sent)

    def choose_board(self, lamp: LampRuntime) -> Board:
        """The lamp's board as the presence check keeps it (smoothed, see _update_rssi); if that
        board is not ready, the best ready one now."""
        for board in self.boards:
            if board.device_id == lamp.board and self.board_ready(board):
                return board
        board, rssi = self.choose_board_for(lamp.mac, lamp.board)
        if board.device_id != lamp.board:
            lamp.board = board.device_id
            self._board_since[lamp.subentry_id] = monotonic()
        if rssi is not None:
            lamp.rssi = rssi
        return board

    def choose_board_for(self, mac: str, last_board: str | None = None) -> tuple[Board, int | None]:
        """The ready board that hears `mac` best (and its RSSI), keeping `last_board` unless another
        is clearly better; else the board that last did; else any ready one."""
        ready = [board for board in self.boards if self.board_ready(board)]
        if not ready:
            raise BoardUnavailable("No board is connected")
        heard = self._heard_by_ready_boards(mac, ready)
        if heard:
            best = self._best_board(heard, last_board)
            return next(board for board in ready if board.device_id == best), heard[best]
        for board in ready:
            if board.device_id == last_board:
                return board, None
        return ready[0], None

    def _heard_by_ready_boards(self, mac: str, ready: list[Board] | None = None) -> dict[str, int]:
        """Board device id → RSSI, for connected boards only: HA keeps an unplugged board's last
        RSSI for a while."""
        if ready is None:
            ready = [board for board in self.boards if self.board_ready(board)]
        ready_ids = {board.device_id for board in ready}
        return {d: rssi for d, rssi in transport.rssi_by_board(self.hass, mac).items() if d in ready_ids}

    @staticmethod
    def _best_board(heard: dict[str, int], current: str | None) -> str:
        """Best RSSI, but the current board stays unless another is BOARD_HYSTERESIS dB better:
        two boards hearing a lamp about equally would otherwise swap on every small change."""
        best = max(heard, key=heard.__getitem__)
        if current in heard and heard[best] - heard[current] < BOARD_HYSTERESIS:
            return current
        return best

    def codec(self, profile: Profile) -> MeshCodec:
        if profile.id not in self._codecs:
            self._codecs[profile.id] = MeshCodec(profile.net_key, profile.app_key, profile.iv_index)
        return self._codecs[profile.id]

    def _board_lock(self, board: Board) -> asyncio.Lock:
        return self._board_locks.setdefault(board.device_id, asyncio.Lock())

    async def async_transmit(
        self,
        profile: Profile,
        address: int,
        command: str,
        board: Board,
        repeat: int = TRANSMIT_REPEAT,
        still_wanted: Callable[[], bool] | None = None,
    ) -> bool:
        """Send one command as `address`. The sequence number is used up even if sending fails.
        False: not sent, because `still_wanted()` turned false while waiting out the interval."""
        if self.closed:
            raise HubClosed("BLE Mesh Lights reloaded")
        addressing.check_sender(address, self.remote_sources())
        wait = self.command_interval - (monotonic() - self._last_sent.get(address, float("-inf")))
        if wait > 0:
            await sleep(wait)
        if still_wanted is not None and not still_wanted():
            return False
        codec = self.codec(profile)
        async with self._board_lock(board):
            if self.closed:
                raise HubClosed("BLE Mesh Lights reloaded")
            if still_wanted is not None and not still_wanted():
                return False  # superseded while another lamp's command had the board
            self._last_tx = monotonic()
            sequence = await self.counters.async_next(address)
            self._sent_log[(address, sequence)] = monotonic()
            payload = profile.commands[command].payload(0x80 | (sequence & 0x7F))
            raw = codec.encode(payload, sequence, address, profile.destination, profile.ttl).hex()
            _LOGGER.debug(
                "Send %s as 0x%04X, sequence %d, through %s", command, address, sequence, board.name
            )
            try:
                await transport.async_send(self.hass, board, raw, repeat=repeat, duration=TRANSMIT_DURATION)
            finally:
                self._last_sent[address] = self._last_tx = monotonic()
        return True

    async def _async_run(self, subentry_id: str, make_commands: Callable[[LampRuntime], list[str]]) -> None:
        """Run a chain for one lamp. A newer chain for the lamp stops this one before its next
        command; the superseded call then returns quietly."""
        lamp = self.lamps[subentry_id]
        generation = self._generation.get(subentry_id, 0) + 1
        self._generation[subentry_id] = generation
        async with self._lamp_locks.setdefault(subentry_id, asyncio.Lock()):
            if self._generation[subentry_id] != generation:
                return
            self._running[subentry_id] = generation
            try:
                await self._async_run_chain(subentry_id, lamp, generation, make_commands)
            finally:
                if self._running.get(subentry_id) == generation:
                    del self._running[subentry_id]

    async def _async_run_chain(
        self,
        subentry_id: str,
        lamp: LampRuntime,
        generation: int,
        make_commands: Callable[[LampRuntime], list[str]],
    ) -> None:
        try:
            commands = make_commands(lamp)
        except planner.PlanError as err:
            raise ServiceValidationError(str(err)) from err
        _LOGGER.debug("%s: from %s, commands %s", lamp.mac, lamp.state, commands)
        for command in commands:
            if self._generation[subentry_id] != generation:
                _LOGGER.debug("%s: chain superseded before %s", lamp.mac, command)
                return
            board = self.choose_board(lamp)
            sent = await self.async_transmit(
                lamp.profile,
                lamp.address,
                command,
                board,
                still_wanted=lambda: self._generation[subentry_id] == generation,
            )
            if not sent:
                _LOGGER.debug("%s: chain superseded before %s", lamp.mac, command)
                return
            self._command_sent(subentry_id, lamp, command)

    def _command_sent(self, subentry_id: str, lamp: LampRuntime, command: str) -> None:
        lamp.state = apply(lamp.profile, lamp.state, command)
        effect = lamp.profile.commands[command].effect
        if effect is not None and effect.color_temp is not None and effect.level is not None:
            lamp.uncertain = False  # a scene sets everything: the state is known again
        self.lamp_changed(subentry_id)

    async def async_press(self, subentry_id: str, command: str) -> None:
        """A press on the card, sent like a remote's: it stops a running chain, then waits for the
        lamp in arrival order (asyncio.Lock is FIFO) and is always sent; presses never replace each
        other. At most PRESS_QUEUE_LIMIT per lamp, counting the one on the air."""
        if self._presses.get(subentry_id, 0) >= PRESS_QUEUE_LIMIT:
            raise PressQueueFull(translation_domain=DOMAIN, translation_key="press_queue_full")
        self._presses[subentry_id] = self._presses.get(subentry_id, 0) + 1
        self.cancel_pending(subentry_id)
        try:
            lamp = self.lamps[subentry_id]
            async with self._lamp_locks.setdefault(subentry_id, asyncio.Lock()):
                board = self.choose_board(lamp)
                await self.async_transmit(lamp.profile, lamp.address, command, board)
                self._command_sent(subentry_id, lamp, command)
        finally:
            self._presses[subentry_id] -= 1

    def chain_running(self, subentry_id: str) -> bool:
        """A chain for the lamp's current request is running (not one superseded or cancelled)."""
        running = self._running.get(subentry_id)
        return running is not None and running == self._generation.get(subentry_id)

    def cancel_pending(self, subentry_id: str) -> None:
        """Stop the lamp's running chain before its next command (a remote press wins)."""
        if subentry_id in self._generation:
            self._generation[subentry_id] += 1

    async def async_set_target(self, subentry_id: str, target: planner.Target) -> None:
        await self._async_run(subentry_id, lambda lamp: planner.plan(lamp.profile, lamp.state, target))

    async def async_power(self, subentry_id: str, on: bool) -> None:
        """Plain on/off: always the power command, whatever HA believes the state is."""

        def commands(lamp: LampRuntime) -> list[str]:
            command = planner.power_command(lamp.profile, on)
            if command is None:
                return planner.plan(lamp.profile, lamp.state, planner.Target(on))
            return [command]

        await self._async_run(subentry_id, commands)

    async def async_command(self, subentry_id: str, command: str) -> None:
        await self._async_run(subentry_id, lambda lamp: [command])

    # Adding lamps

    def configured_macs(self) -> set[str]:
        return {
            sub.data[LAMP_MAC].upper()
            for sub in self.entry.subentries.values()
            if sub.subentry_type == SUBENTRY_LAMP
        }

    @property
    def base_address(self) -> int:
        return int(self.entry.options.get(CONF_BASE_ADDRESS, DEFAULT_BASE_ADDRESS))

    def used_indices(self) -> set[int]:
        """Indices ever handed out: from the entry, and from counters of addresses in the
        integration's range (the integration may have been deleted and added again)."""
        base = self.base_address
        used = set(self.entry.data.get(DATA_USED_INDICES, []))
        used |= {
            a - base for a in self.counters.high_watermarks() if addressing.in_integration_range(a, base)
        }
        return used

    def allocate_address(self) -> tuple[int, int]:
        """The next (index, address), recorded in the entry at once so it is never handed out again."""
        used = self.used_indices()
        try:
            index, address = addressing.allocate(self.base_address, used, avoid=self.remote_sources())
        except ValueError as err:
            raise NoAddressLeft(str(err)) from err
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, DATA_USED_INDICES: sorted(used | {index})}
        )
        return index, address

    async def async_wait_for_new_lamp(self, profile: Profile, timeout: float, mac: str | None = None) -> str:
        """MAC of a lamp not yet configured whose active beacon appears after the call, for the first
        time or after a silence of NEW_LAMP_GAP (it was switched off and on at the wall). With `mac`,
        only that lamp counts, configured or not (pairing it again)."""
        wanted = mac.upper() if mac else None
        configured = self.configured_macs() if wanted is None else set()
        deadline = monotonic() + timeout
        last_seen = {address.upper(): t for address, _raw, t in transport.discovered_beacons(self.hass)}
        returned: set[str] = set()
        while monotonic() < deadline:
            await sleep(NEW_LAMP_POLL)
            if self.closed:
                raise HubClosed("BLE Mesh Lights reloaded")
            for address, raw, advert_time in transport.discovered_beacons(self.hass):
                mac = address.upper()
                previous = last_seen.get(mac)
                if previous is not None and advert_time <= previous:
                    continue
                if previous is None or advert_time - previous >= NEW_LAMP_GAP:
                    returned.add(mac)
                last_seen[mac] = advert_time
                if mac in configured or mac not in returned or (wanted and mac != wanted):
                    continue
                beacon = parse_beacon(profile, raw)
                if beacon is not None and not beacon.quiet and beacon.mac == mac:
                    return mac
        raise TimeoutError

    async def async_seed_lamp_state(self, mac: str, state: LampState) -> None:
        """What the wizard knows about a lamp it is about to create (used at the next setup)."""
        self._stored.setdefault("seeds", {})[mac.upper()] = {**asdict(state), "uncertain": False}
        await self._state_store.async_save(self._data_to_store())

    # Power presence

    @callback
    def _presence_timer(self, _now: datetime) -> None:
        self.presence_tick()

    @callback
    def presence_tick(self) -> None:
        """Read each lamp's last advert; mark lamps lost or back; restore after an outage."""
        now = monotonic()
        for lamp in self.lamps.values():
            rssi_before, board_before = lamp.rssi, lamp.board
            self._update_rssi(lamp)
            if lamp.board != board_before or (
                lamp.rssi is not None
                and (rssi_before is None or abs(lamp.rssi - rssi_before) >= RSSI_REPORT_STEP)
            ):
                async_dispatcher_send(self.hass, SIGNAL_LAMP.format(lamp.subentry_id))
            elif rssi_before is not None:
                lamp.rssi = rssi_before  # small changes are not reported
            advert = transport.last_advert(self.hass, lamp.mac)
            if advert is None:
                continue
            raw, advert_time = advert
            if lamp.last_beacon is not None and advert_time <= lamp.last_beacon:
                # HA keeps the last advert for minutes after the lamp went silent. In active mode
                # the lamp sends several messages a second: a silence this long may be a short
                # switch-off at the wall, unless a board was sending (it does not listen then).
                if (
                    lamp.present
                    and lamp.quiet is False
                    and now - advert_time >= self.power_cycle_gap
                    and self._last_tx < advert_time - TX_LISTEN_MARGIN
                ):
                    lamp.silent = True
                continue
            seen_at = dt_util.utcnow() - timedelta(seconds=max(now - advert_time, 0))
            if lamp.last_seen is None or (seen_at - lamp.last_seen).total_seconds() >= LAST_SEEN_STEP:
                lamp.last_seen = seen_at
                async_dispatcher_send(self.hass, SIGNAL_LAMP.format(lamp.subentry_id))
            beacon = parse_beacon(lamp.profile, raw)
            if beacon is None or beacon.mac != lamp.mac.upper():
                if lamp.quiet is None:
                    continue  # a scan response before any beacon: the flag is unknown
                beacon = Beacon(lamp.mac.upper(), lamp.quiet)
            if beacon.quiet != lamp.quiet:
                _LOGGER.debug(
                    "%s: %s mode (advert %.1f s after the previous one)",
                    lamp.mac,
                    "quiet" if beacon.quiet else "active",
                    advert_time - lamp.last_beacon if lamp.last_beacon is not None else 0.0,
                )
            was_quiet, was_silent = lamp.quiet, lamp.silent
            lamp.last_beacon, lamp.quiet, lamp.silent = advert_time, beacon.quiet, False
            first = lamp.present is None
            result = self._presence[lamp.profile.id].seen(beacon, advert_time)
            if result == RESTORED:
                self._lamp_got_power(lamp, now)
            elif lamp.present and was_quiet and not beacon.quiet:
                # Only mains power brings a lamp from quiet back to active mode: it was switched
                # off and on at the wall, too quickly to be seen as lost.
                _LOGGER.debug("%s: quiet → active, switched at the wall", lamp.mac)
                self._lamp_got_power(lamp, now)
            elif lamp.present and was_silent and not beacon.quiet:
                _LOGGER.debug("%s: silent for %.1f s in active mode", lamp.mac, self.power_cycle_gap)
                if not lamp.uncertain:
                    lamp.uncertain = True  # perhaps switched at the wall, perhaps a lost signal
                    self.lamp_changed(lamp.subentry_id)
            elif first:
                lamp.present = True
                if lamp.subentry_id in self._pending_restore and not beacon.quiet:
                    self._lamp_got_power(lamp, now)
                else:
                    # Powered while HA was down, perhaps switched at the wall: keep the stored state.
                    lamp.uncertain = lamp.uncertain or not beacon.quiet
                    self.lamp_changed(lamp.subentry_id)
            elif lamp.present is False:
                # A quiet beacon after a loss: bursts were missed, the lamp kept its power and state;
                # after a loss from before the entry started, how long it has had power is unknown.
                lamp.present = True
                lamp.before_loss = None
                lamp.uncertain = lamp.uncertain or lamp.lost_at_start
                self.lamp_changed(lamp.subentry_id)
            lamp.lost_at_start = False
        for tracker in self._presence.values():
            for mac in tracker.expired(now):
                for lamp in self.lamps.values():
                    if lamp.mac.upper() == mac and lamp.present is not False:
                        _LOGGER.debug("%s: no beacon for too long, counted as switched off", lamp.mac)
                        lamp.present = False
                        lamp.before_loss = lamp.state
                        self.lamp_changed(lamp.subentry_id)
        for sub_id, deadline in list(self._pending_restore.items()):
            if now > deadline:
                del self._pending_restore[sub_id]

    def _update_rssi(self, lamp: LampRuntime) -> None:
        """Keep the lamp's board: smoothed RSSI per board, BOARD_HYSTERESIS dB better and at
        least BOARD_MIN_DWELL on the current board before moving. Single readings swing by
        more than 10 dB; without this, two boards side by side swap all the time."""
        heard = self._heard_by_ready_boards(lamp.mac)
        for device_id, rssi in heard.items():
            key = (lamp.mac, device_id)
            previous = self._rssi_avg.get(key)
            self._rssi_avg[key] = rssi if previous is None else previous + RSSI_SMOOTHING * (rssi - previous)
        if not heard:
            return
        average = {device_id: self._rssi_avg[(lamp.mac, device_id)] for device_id in heard}
        best = max(average, key=average.__getitem__)
        now = monotonic()
        chosen = lamp.board
        if chosen not in average:
            chosen = best  # no board yet, or the current one is gone or no longer hears the lamp
        elif (
            best != chosen
            and average[best] - average[chosen] >= BOARD_HYSTERESIS
            and now - self._board_since.get(lamp.subentry_id, float("-inf")) >= BOARD_MIN_DWELL
        ):
            chosen = best
        if chosen != lamp.board:
            lamp.board = chosen
            self._board_since[lamp.subentry_id] = now
        lamp.rssi = round(average[chosen])

    def _lamp_got_power(self, lamp: LampRuntime, now: float) -> None:
        """Mains power just returned: the lamp is in its power-on state."""
        target = lamp.before_loss or lamp.state
        lamp.present = True
        lamp.state = power_restored(lamp.profile)
        lamp.uncertain = False
        lamp.before_loss = None
        self._restored_at[lamp.subentry_id] = now
        self.lamp_changed(lamp.subentry_id)
        if self._pending_restore.pop(lamp.subentry_id, None) is not None:
            self._restore(lamp, target)

    def _restore(self, lamp: LampRuntime, target: LampState) -> None:
        if target.on is None or (target.on and (target.color_temp is None or target.level is None)):
            return  # not fully known: leave the lamp in its power-on state
        if target.on:
            wanted = planner.Target(True, target.color_temp, target.level)
            job = self.async_set_target(lamp.subentry_id, wanted)
        else:
            job = self.async_power(lamp.subentry_id, False)

        async def _run() -> None:
            try:
                await job
            except HomeAssistantError as err:
                _LOGGER.warning("Could not restore %s after a power outage: %s", lamp.mac, err)

        self.entry.async_create_background_task(self.hass, _run(), f"ble_mesh_lights restore {lamp.mac}")

    @callback
    def _outage_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is not None and new.state == STATE_ON and (old is None or old.state != STATE_ON):
            self._outage_started()

    @callback
    def _outage_started(self) -> None:
        """Power came back after an outage: lamps set to "restore" go back to their last state.
        Deliberately simple until the outage detector defines the sensor's behaviour; its known
        gaps are in docs/KNOWN_ISSUES.md ("Outage restore is simple until the outage detector")."""
        now = monotonic()
        for lamp in self.lamps.values():
            if lamp.after_outage != AFTER_OUTAGE_RESTORE:
                continue
            self._pending_restore[lamp.subentry_id] = now + OUTAGE_WINDOW
            if lamp.present and lamp.quiet is False:
                self._lamp_got_power(lamp, now)

    # Remotes

    @callback
    def _handle_raw_adv(self, event: Event) -> None:
        try:
            raw = bytes.fromhex(event.data["raw"])
        except (KeyError, TypeError, ValueError):
            return
        now = monotonic()
        for old in [key for key, sent in self._sent_log.items() if now - sent > OWN_ECHO_WINDOW]:
            del self._sent_log[old]
        own = self.own_addresses()
        decoded: dict[int, list[MeshMessage]] = {}
        for profile in self.profiles.values():
            codec = self.codec(profile)
            if id(codec) not in decoded:
                decoded[id(codec)] = codec.decode(raw)
            for message in decoded[id(codec)]:
                if message.source in own:
                    self._check_collision(message)
                    continue
                tracker = self._trackers[profile.id]
                tracker.set_ignored(own)
                press = tracker.accept(message, now)
                if press is not None:
                    self._handle_press(profile, press, now)

    @callback
    def _check_collision(self, message: MeshMessage) -> None:
        if (message.source, message.sequence) in self._sent_log:
            return  # our own advert, heard through another board
        _LOGGER.warning("Another sender uses address 0x%04X (sequence %d)", message.source, message.sequence)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"address_collision_{message.source:04x}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="address_collision",
            translation_placeholders={"address": f"0x{message.source:04X}"},
        )

    @callback
    def _handle_press(self, profile: Profile, press: RemotePress, now: float) -> None:
        key = remote_key(profile.id, press.source)
        if press.source not in self.heard_sources:
            self.heard_sources.add(press.source)
            self.async_schedule_save()
        _LOGGER.debug(
            "Remote %s: %s (sequence %d, missed %d%s%s)%s",
            key,
            press.command,
            press.sequence,
            press.missed,
            ", repeat" if press.repeat else "",
            ", late: not applied" if press.late else "",
            "" if key in self.remotes else " — not bound to a lamp",
        )
        taken = self._resolve_waiters(profile, key, press)
        if press.command == profile.pairing.command and not press.repeat and not taken:
            self._rebind_after_power_on(profile, key, press.source, now)
        remote = self.remotes.get(key)
        if remote is None:
            self.unknown_remotes[key] = now
            # On every press, not only the first: a remote that was ignored and then un-ignored
            # must be reported again (creating an existing issue only updates it).
            if not taken and key not in self.ignored_remotes:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"new_remote_{key}",
                    is_fixable=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="new_remote",
                    translation_placeholders={"profile": profile.name, "address": f"0x{press.source:04X}"},
                    data={"entry_id": self.entry.entry_id, "key": key},
                )
        elif not press.late:
            remote.missed_total += press.missed
            remote.last_press = dt_util.utcnow()
            for sub_id in remote.lamps:
                lamp = self.lamps.get(sub_id)
                if lamp is None:
                    continue
                state, uncertain = apply_press(lamp.profile, lamp.state, press.command, press.missed)
                _LOGGER.debug(
                    "%s: %s → %s%s", lamp.mac, lamp.state, state, " (uncertain)" if uncertain else ""
                )
                self.cancel_pending(sub_id)  # the remote wins over a running chain, even a no-op press
                if state != lamp.state or uncertain != lamp.uncertain:
                    lamp.state, lamp.uncertain = state, uncertain
                    self.lamp_changed(sub_id)
        self.async_schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_REMOTE.format(key), press)

    def _rebind_after_power_on(self, profile: Profile, key: str, source: int, now: float) -> None:
        """A remote that starts pairing just after exactly one lamp got power has paired with it."""
        recent = [
            sub_id
            for sub_id, at in self._restored_at.items()
            if now - at <= REBIND_WINDOW and self.lamps[sub_id].profile.id == profile.id
        ]
        if len(recent) != 1:
            return
        remote = self.remotes.get(key)
        if remote is None:
            remote = self.remotes[key] = RemoteInfo(key, profile.id, source, [])
            self.unknown_remotes.pop(key, None)
            async_dispatcher_send(self.hass, SIGNAL_REMOTE_ADDED, key)
        if recent[0] not in remote.lamps:
            remote.lamps.append(recent[0])
            self.update_remote_device(key)
            async_dispatcher_send(self.hass, SIGNAL_REMOTE.format(key), None)
        self.async_schedule_save()

    def _resolve_waiters(self, profile: Profile, key: str, press: RemotePress) -> bool:
        """Hand a press to the wizard waiting for one; True if it took it."""
        if press.command == profile.pairing.command or press.late:
            return False
        taken = False
        for waiting_profile, future in self._press_waiters:
            if waiting_profile.id == profile.id and not future.done():
                future.set_result((key, press.command))
                taken = True
        return taken

    async def async_wait_for_remote_press(self, profile: Profile, timeout: float) -> tuple[str, str]:
        """(remote key, command) of the next press from any remote of this profile; pairing
        requests and late presses do not count."""
        future: asyncio.Future[tuple[str, str]] = self.hass.loop.create_future()
        waiter = (profile, future)
        self._press_waiters.append(waiter)
        try:
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._press_waiters.remove(waiter)

    async def async_add_remote_lamp(self, key: str, subentry_id: str) -> None:
        """The remote also controls this lamp; its other lamps stay."""
        remote = self.remotes.get(key)
        lamps = list(remote.lamps) if remote else []
        if subentry_id not in lamps:
            lamps.append(subentry_id)
        await self.async_bind_remote(key, lamps)

    def remote_lamp_names(self, key: str) -> list[str]:
        """Titles of the lamps this remote controls."""
        remote = self.remotes.get(key)
        if remote is None:
            return []
        return [
            self.entry.subentries[sub_id].title for sub_id in remote.lamps if sub_id in self.entry.subentries
        ]

    async def async_bind_remote(self, key: str, lamps: list[str]) -> None:
        """Bind a remote to these lamps (replacing its previous lamps)."""
        profile_id, source = key.split(":")
        remote = self.remotes.get(key)
        if remote is None:
            self.remotes[key] = RemoteInfo(key, profile_id, int(source, 16), list(lamps))
        else:
            remote.lamps = list(lamps)
        self.unknown_remotes.pop(key, None)
        ir.async_delete_issue(self.hass, DOMAIN, f"new_remote_{key}")
        if remote is None:
            async_dispatcher_send(self.hass, SIGNAL_REMOTE_ADDED, key)
        else:
            self.update_remote_device(key)
            async_dispatcher_send(self.hass, SIGNAL_REMOTE.format(key), None)
        await self._state_store.async_save(self._data_to_store())

    def remote_device_name(self, key: str) -> dict[str, str]:
        """Translation placeholders of a remote's device name: "Valen remote (Street, Hall)"."""
        remote = self.remotes[key]
        profile = self.profiles.get(remote.profile)
        name = profile.name if profile else remote.profile
        if profile:
            name = name.removeprefix(profile.manufacturer).removesuffix(" series").strip()
        titles = [self.entry.subentries[i].title for i in remote.lamps if i in self.entry.subentries]
        return {"name": name, "lamps": ", ".join(titles) or "-"}

    def update_remote_device(self, key: str) -> None:
        devices = dr.async_get(self.hass)
        if devices.async_get_device_by_identifier((DOMAIN, f"remote_{key}"), self.entry.entry_id):
            devices.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, f"remote_{key}")},
                translation_key="remote",
                translation_placeholders=self.remote_device_name(key),
            )

    async def async_remove_remote(self, key: str, *, remove_device: bool = True) -> None:
        self.remotes.pop(key, None)
        if remove_device:
            devices = dr.async_get(self.hass)
            if device := devices.async_get_device_by_identifier(
                (DOMAIN, f"remote_{key}"), self.entry.entry_id
            ):
                devices.async_remove_device(device.id)
        await self._state_store.async_save(self._data_to_store())

    @callback
    def async_ignore_remote(self, key: str, ignore: bool = True) -> None:
        if ignore:
            self.ignored_remotes.add(key)
            ir.async_delete_issue(self.hass, DOMAIN, f"new_remote_{key}")
        else:
            self.ignored_remotes.discard(key)
        self.async_schedule_save()
