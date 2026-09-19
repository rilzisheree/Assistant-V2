"""Windows audio bridge for a verified Messenger Web call.

The bridge deliberately does not create a Gemini session.  It only connects
the existing Live session's PCM queues to Windows audio endpoints after the
Messenger call has been visually verified as connected.

Signal path:

    Gemini 24 kHz mono PCM -> CABLE Input -> CABLE Output -> Messenger mic
    Messenger playback -> WASAPI loopback -> Gemini 16 kHz mono PCM

Device indexes are never persisted or hardcoded.  They are resolved from the
current PortAudio device list every time the bridge starts.
"""

from __future__ import annotations

import asyncio
import platform
import time
import threading
from typing import Any

import numpy as np


GEMINI_INPUT_RATE = 16_000
GEMINI_OUTPUT_RATE = 24_000
PCM_DTYPE = "int16"
CABLE_PLAYBACK_NAME = "CABLE Input (VB-Audio Virtual Cable)"
DEFAULT_CABLE_CAPTURE_NAME = "CABLE Output (VB-Audio Virtual Cable)"


class MessengerAudioBridgeError(RuntimeError):
    """Raised when the Windows audio bridge cannot be opened safely."""


def _api_name(apis: list[dict[str, Any]], device: dict[str, Any]) -> str:
    index = int(device.get("hostapi", -1))
    if 0 <= index < len(apis):
        return str(apis[index].get("name", ""))
    return ""


def _same_name(actual: str, wanted: str) -> bool:
    return str(actual).strip().casefold() == str(wanted).strip().casefold()


def _resample_mono_pcm(data: bytes, source_rate: int, target_rate: int) -> bytes:
    """Convert signed 16-bit mono PCM without hiding the rate conversion."""
    if not data or source_rate == target_rate:
        return data
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return b""
    target_size = max(1, int(round(samples.size * target_rate / source_rate)))
    source_positions = np.arange(target_size, dtype=np.float64) * (
        samples.size / target_size
    )
    left = np.floor(source_positions).astype(np.int64)
    right = np.minimum(left + 1, samples.size - 1)
    fraction = source_positions - left
    converted = (
        samples[left].astype(np.float64) * (1.0 - fraction)
        + samples[right].astype(np.float64) * fraction
    )
    return np.clip(np.rint(converted), -32768, 32767).astype(np.int16).tobytes()


class MessengerAudioBridge:
    """Bidirectional PCM transport for one already-connected Messenger call."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        input_queue: asyncio.Queue,
        messenger_playback_device: str = "",
        loopback_device: str = "",
        logger=None,
    ) -> None:
        self._loop = loop
        self._input_queue = input_queue
        self._messenger_playback_device = str(messenger_playback_device or "").strip()
        self._loopback_device = str(loopback_device or "").strip()
        self._logger = logger or (lambda message: print(f"[AudioBridge] {message}"))
        self._output_stream = None
        self._loopback_stream = None
        self._output_rate = GEMINI_OUTPUT_RATE
        self._capture_rate = GEMINI_INPUT_RATE
        self._output_channels = 1
        self._capture_channels = 1
        self._output_host_api = ""
        self._capture_host_api = ""
        self._output_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active = False
        self._stopping = False
        self._last_callback_error = ""

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "active": self._active,
                "output_rate": self._output_rate,
                "capture_rate": self._capture_rate,
                "output_channels": self._output_channels,
                "capture_channels": self._capture_channels,
                "output_host_api": self._output_host_api,
                "capture_host_api": self._capture_host_api,
                "dtype": PCM_DTYPE,
                "output_device": CABLE_PLAYBACK_NAME,
                "loopback_device": self._loopback_device,
                "last_callback_error": self._last_callback_error,
            }

    @staticmethod
    def _sounddevice():
        if platform.system() != "Windows":
            raise MessengerAudioBridgeError(
                "Messenger/Gemini audio bridging requires Windows WASAPI."
            )
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise MessengerAudioBridgeError(
                "sounddevice is required for the Messenger/Gemini audio bridge."
            ) from exc
        return sd

    @staticmethod
    def _devices(sd):
        devices = list(sd.query_devices())
        try:
            apis = list(sd.query_hostapis())
        except Exception:
            apis = []
        return devices, apis

    def _named_candidates(
        self,
        sd,
        *,
        wanted: str,
        direction: str,
        host_api: str | None = None,
    ):
        devices, apis = self._devices(sd)
        channel_key = (
            "max_output_channels" if direction == "output" else "max_input_channels"
        )
        for index, device in enumerate(devices):
            if int(device.get(channel_key, 0) or 0) <= 0:
                continue
            if wanted and not _same_name(device.get("name", ""), wanted):
                continue
            if host_api and host_api.casefold() not in _api_name(apis, device).casefold():
                continue
            yield index, device, apis

    def _open_cable_output(self, sd):
        errors = []
        candidates = list(
            self._named_candidates(
                sd,
                wanted=CABLE_PLAYBACK_NAME,
                direction="output",
            )
        )
        if not candidates:
            raise MessengerAudioBridgeError(
                "Gemini → Messenger audio device unavailable: "
                "CABLE Input not found."
            )

        for index, device, apis in candidates:
            rates = []
            for rate in (
                GEMINI_OUTPUT_RATE,
                int(round(float(device.get("default_samplerate", 0) or 0))),
                48_000,
                44_100,
                16_000,
            ):
                if rate > 0 and rate not in rates:
                    rates.append(rate)
            for rate in rates:
                stream = None
                try:
                    stream = sd.RawOutputStream(
                        samplerate=rate,
                        channels=1,
                        dtype=PCM_DTYPE,
                        blocksize=1024,
                        device=index,
                    )
                    stream.start()
                    self._logger(
                        "[AUDIO] CABLE Input found: "
                        f"{device.get('name')} "
                        f"(host={_api_name(apis, device) or 'unknown'}, rate={rate})"
                    )
                    return stream, rate, device, apis
                except Exception as exc:
                    errors.append(
                        f"{device.get('name')} @ {rate} Hz: {exc}"
                    )
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
        detail = "; ".join(errors[-3:]) or "no compatible format"
        raise MessengerAudioBridgeError(
            "Gemini → Messenger audio device unavailable: "
            f"CABLE Input could not be opened ({detail})."
        )

    def _default_loopback_name(self, sd) -> str:
        if self._loopback_device and self._loopback_device.casefold() != "system default":
            return self._loopback_device
        if (
            self._messenger_playback_device
            and self._messenger_playback_device.casefold() != "system default"
        ):
            return self._messenger_playback_device
        try:
            default_output = sd.default.device[1]
            devices, _ = self._devices(sd)
            if isinstance(default_output, int) and 0 <= default_output < len(devices):
                return str(devices[default_output].get("name", "")).strip()
        except Exception:
            pass
        return ""

    def _open_loopback(self, sd):
        wanted = self._default_loopback_name(sd)
        candidates = list(
            self._named_candidates(
                sd,
                wanted=wanted,
                direction="output",
                host_api="WASAPI",
            )
        )
        if not candidates and not wanted:
            devices, apis = self._devices(sd)
            candidates = [
                (index, device, apis)
                for index, device in enumerate(devices)
                if int(device.get("max_output_channels", 0) or 0) > 0
                and "wasapi" in _api_name(apis, device).casefold()
            ][:1]
        if not candidates:
            detail = (
                f"named output '{wanted}'"
                if wanted
                else "the Windows default output"
            )
            raise MessengerAudioBridgeError(
                "Messenger → Gemini audio capture unavailable: "
                f"WASAPI loopback source for {detail} was not found."
            )

        errors = []
        for index, device, apis in candidates:
            channels = max(1, min(2, int(device.get("max_output_channels", 1) or 1)))
            rates = []
            for rate in (
                int(round(float(device.get("default_samplerate", 0) or 0))),
                48_000,
                44_100,
                32_000,
                GEMINI_INPUT_RATE,
            ):
                if rate > 0 and rate not in rates:
                    rates.append(rate)
            for rate in rates:
                stream = None
                try:
                    settings = sd.WasapiSettings(loopback=True)
                    # PortAudio may invoke the callback immediately after
                    # start(), before _open_loopback returns.
                    self._capture_rate = rate
                    self._capture_channels = channels
                    self._loopback_device = str(device.get("name", "")).strip()
                    stream = sd.InputStream(
                        samplerate=rate,
                        channels=channels,
                        dtype=PCM_DTYPE,
                        blocksize=1024,
                        device=index,
                        extra_settings=settings,
                        callback=self._on_loopback_audio,
                    )
                    stream.start()
                    self._logger(
                        "[AUDIO] Messenger loopback device selected: "
                        f"{device.get('name')} "
                        f"(host={_api_name(apis, device) or 'unknown'}, "
                        f"rate={rate}, channels={channels}, dtype={PCM_DTYPE})"
                    )
                    return stream, rate, channels, device, apis
                except Exception as exc:
                    errors.append(f"{device.get('name')} @ {rate} Hz: {exc}")
                    if stream is not None:
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass
        detail = "; ".join(errors[-3:]) or "no compatible loopback format"
        raise MessengerAudioBridgeError(
            "Messenger → Gemini audio capture unavailable: "
            f"could not open WASAPI loopback for "
            f"'{wanted or 'default output'}' ({detail})."
        )

    def start(self) -> dict[str, Any]:
        """Open both endpoints. On any failure, close everything opened so far."""
        with self._state_lock:
            if self._active:
                return self.status
            self._stopping = False
        sd = self._sounddevice()
        try:
            (
                self._output_stream,
                self._output_rate,
                output_device,
                output_apis,
            ) = self._open_cable_output(sd)
            self._output_channels = 1
            self._output_host_api = _api_name(output_apis, output_device)
            # The diagnostic normally performs this check before the browser
            # call. Recheck it here as well: an endpoint can disappear between
            # the diagnostic and the verified call, and starting a "connected"
            # bridge without Messenger's virtual microphone is a false success.
            cable_input_index, cable_input_device, cable_input_apis = (
                _find_cable_capture_device(sd)
            )
            self._logger(
                "[AUDIO] CABLE Output found: "
                f"{cable_input_device.get('name')} "
                f"(host={_api_name(cable_input_apis, cable_input_device) or 'unknown'}, "
                f"input endpoint index resolved at runtime)"
            )
            (
                self._loopback_stream,
                self._capture_rate,
                self._capture_channels,
                loopback_device,
                loopback_apis,
            ) = self._open_loopback(sd)
            self._capture_host_api = _api_name(loopback_apis, loopback_device)
            with self._state_lock:
                self._active = True
            self._logger(
                "[AUDIO] Gemini → Messenger stream started "
                f"({self._output_rate} Hz, {self._output_channels} channel, "
                f"{PCM_DTYPE})"
            )
            self._logger(
                "[AUDIO] Messenger → Gemini loopback started "
                f"({self._capture_rate} Hz, {self._capture_channels} channels, "
                f"{PCM_DTYPE})"
            )
            self._logger("[AUDIO] Bidirectional audio bridge ACTIVE")
            return self.status
        except Exception:
            self.stop()
            raise

    def _on_loopback_audio(self, indata, frames, time_info, status) -> None:
        if status:
            self._logger(f"WASAPI loopback status: {status}")
        try:
            samples = np.asarray(indata, dtype=np.int16)
            if samples.ndim > 1:
                samples = np.rint(samples.astype(np.float32).mean(axis=1)).astype(np.int16)
            data = _resample_mono_pcm(
                samples.reshape(-1).tobytes(),
                self._capture_rate,
                GEMINI_INPUT_RATE,
            )
            if data:
                self._loop.call_soon_threadsafe(self._enqueue_input, data)
        except Exception as exc:
            with self._state_lock:
                self._last_callback_error = str(exc)
                self._active = False
            self._logger(f"[AUDIO] Loopback callback stopped: {exc}")

    def _enqueue_input(self, data: bytes) -> None:
        if self._stopping or not self.active:
            return
        item = {"data": data, "mime_type": "audio/pcm"}
        try:
            self._input_queue.put_nowait(item)
        except asyncio.QueueFull:
            # Keep real-time audio moving instead of building unbounded latency.
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._input_queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def write_gemini_audio(self, data: bytes) -> None:
        """Write existing Gemini output PCM to the VB-CABLE playback endpoint."""
        if not data or not self.active:
            return
        converted = _resample_mono_pcm(
            data,
            GEMINI_OUTPUT_RATE,
            self._output_rate,
        )
        try:
            with self._output_lock:
                self._output_stream.write(converted)
        except Exception as exc:
            with self._state_lock:
                self._last_callback_error = str(exc)
            raise MessengerAudioBridgeError(
                f"VB-CABLE playback stopped: {exc}"
            ) from exc

    def stop(self) -> None:
        with self._state_lock:
            had_streams = (
                self._active
                or self._output_stream is not None
                or self._loopback_stream is not None
            )
            self._stopping = True
            self._active = False
        for attr in ("_loopback_stream", "_output_stream"):
            stream = getattr(self, attr, None)
            if stream is None:
                continue
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
            setattr(self, attr, None)
        if had_streams:
            self._logger("[AUDIO] Bidirectional audio bridge stopped")


def _find_cable_capture_device(sd):
    """Resolve CABLE Output as an input without retaining a capture stream."""
    devices = list(sd.query_devices())
    try:
        apis = list(sd.query_hostapis())
    except Exception:
        apis = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0) or 0) <= 0:
            continue
        if _same_name(device.get("name", ""), DEFAULT_CABLE_CAPTURE_NAME):
            return index, device, apis
    raise MessengerAudioBridgeError(
        "Messenger microphone device unavailable: CABLE Output not found."
    )


def _open_cable_capture(sd):
    """Open CABLE Output as a real input endpoint for the no-call diagnostic."""
    devices = list(sd.query_devices())
    try:
        apis = list(sd.query_hostapis())
    except Exception:
        apis = []
    candidates = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0) or 0) <= 0:
            continue
        if _same_name(device.get("name", ""), DEFAULT_CABLE_CAPTURE_NAME):
            candidates.append((index, device, apis))
    if not candidates:
        _find_cable_capture_device(sd)
    errors = []
    for index, device, apis in candidates:
        rates = []
        for rate in (
            int(round(float(device.get("default_samplerate", 0) or 0))),
            48_000,
            GEMINI_INPUT_RATE,
        ):
            if rate > 0 and rate not in rates:
                rates.append(rate)
        stream = None
        for rate in rates:
            try:
                count = [0]

                def _count(indata, frames, time_info, status):
                    count[0] += int(frames)

                stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,
                    dtype=PCM_DTYPE,
                    blocksize=1024,
                    device=index,
                    callback=_count,
                )
                stream.start()
                return stream, count, rate, device, apis
            except Exception as exc:
                errors.append(f"{device.get('name')} @ {rate} Hz: {exc}")
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                stream = None
    detail = "; ".join(errors[-3:]) or "no compatible input format"
    raise MessengerAudioBridgeError(
        "Messenger microphone device unavailable: "
        f"CABLE Output could not be opened ({detail})."
    )


def diagnose_local_audio(
    *,
    messenger_playback_device: str = "",
    loopback_device: str = "",
    duration_seconds: float = 0.5,
) -> dict[str, Any]:
    """Check all local endpoints without opening Messenger or calling anyone.

    The diagnostic writes only silence and reports frame counts, never raw
    conversation audio. It is intentionally separate from ``start`` so it can
    be run before the irreversible browser call.
    """
    if platform.system() != "Windows":
        return {
            "ok": False,
            "detail": (
                "Local Messenger audio diagnostic unavailable: "
                "Windows WASAPI is required."
            ),
        }
    sd = MessengerAudioBridge._sounddevice()
    loop = asyncio.new_event_loop()
    bridge = MessengerAudioBridge(
        loop=loop,
        input_queue=asyncio.Queue(),
        messenger_playback_device=messenger_playback_device,
        loopback_device=loopback_device,
        logger=lambda message: print(f"[AudioBridge] {message}", flush=True),
    )
    cable_capture = None
    try:
        cable_capture, cable_count, cable_rate, cable_device, cable_apis = (
            _open_cable_capture(sd)
        )
        print(
            "[AUDIO] CABLE Output found "
            f"(host={_api_name(cable_apis, cable_device) or 'unknown'}, "
            f"rate={cable_rate} Hz, channels=1, dtype={PCM_DTYPE})",
            flush=True,
        )
        status = bridge.start()
        print(
            "[AUDIO] streams started "
            f"(CABLE Input {status['output_rate']} Hz, "
            f"loopback {status['capture_rate']} Hz)",
            flush=True,
        )
        silence = bytes(
            max(1, int(status["output_rate"] * max(0.1, duration_seconds))) * 2
        )
        bridge.write_gemini_audio(silence)
        time.sleep(max(0.1, duration_seconds))
        result = {
            "ok": True,
            "detail": "Local audio diagnostic passed; no call was made.",
            "cable_output_frames": cable_count[0],
            "loopback": bridge.status,
        }
        print("[AUDIO] local diagnostic PASSED; no Messenger call made", flush=True)
        return result
    except Exception as exc:
        print(f"[AUDIO] local diagnostic FAILED: {exc}", flush=True)
        return {"ok": False, "detail": str(exc)}
    finally:
        if cable_capture is not None:
            try:
                cable_capture.stop()
                cable_capture.close()
            except Exception:
                pass
        bridge.stop()
        loop.close()


if __name__ == "__main__":
    import json

    print(json.dumps(diagnose_local_audio(), ensure_ascii=False, indent=2))