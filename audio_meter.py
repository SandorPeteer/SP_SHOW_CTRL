"""Live mpv PCM meters: post filters/downmix, with mpv software gain applied.

The pass-through astats filter measures the running decoder's channel RMS and
sample peak. This is not a waveform estimate, true-peak meter or device-loopback.
mpv applies software volume after user audio filters; its gain is (volume/100)^3
times volume-gain and ReplayGain fallback. Active track/album ReplayGain is not
silently guessed. The UI displays A/B independently, never a fictitious sum.

Sources: https://mpv.io/manual/stable/#command-interface-af-metadata/<filter-label>
https://ffmpeg.org/ffmpeg-filters.html#astats
https://github.com/mpv-player/mpv/blob/v0.41.0/player/audio.c#L161-L170
https://mpv.io/manual/stable/#audio-filters-format
"""
import math


METER_LABEL = "sp-meter"
OUTPUT_FORMAT_LABEL = "sp-output"
METER_PROPERTY = "af-metadata/" + METER_LABEL
METER_FILTER = ("@" + METER_LABEL + ":lavfi=[astats=metadata=1:reset=1:"
                "measure_perchannel=RMS_level+Peak_level:measure_overall=none]")
METER_PROPERTIES = (METER_PROPERTY, "volume", "volume-gain", "mute", "pause",
                    "core-idle", "replaygain", "replaygain-fallback",
                    "audio-out-params", "af", "audio-device", "audio-channels")


def output_format_filter(channels=None):
    # Use mpv's own autoconverter, with its configured downmix coefficients.
    # The unconstrained form lets a new file/device negotiate its native layout.
    return "@" + OUTPUT_FORMAT_LABEL + ":format" + ("=channels=" + str(channels) if channels else "")


def metered_filters(filters=None, *, output_channels=None):
    return ((str(filters) + "," if filters else "") +
            output_format_filter(output_channels) + "," + METER_FILTER)


def empty_levels():
    return {"active": False, "available": False, "channels": [], "db": "", "muted": False}


def levels_from_telemetry(values, *, clock_current=True):
    metadata = values.get(METER_PROPERTY)
    if not isinstance(metadata, dict) or not metadata or not clock_current:
        return empty_levels()
    # Track/album ReplayGain includes file-dependent clipping prevention. This
    # application does not enable it; do not invent a level if external mpv
    # configuration enabled a gain that is not exposed as one scalar property.
    if values.get("replaygain", "no") not in ("no", False):
        return empty_levels()
    try:
        volume = float(values["volume"])
        extra_db = float(values.get("volume-gain") or 0) + float(values.get("replaygain-fallback") or 0)
        if not math.isfinite(volume) or not math.isfinite(extra_db):
            return empty_levels()
    except (KeyError, TypeError, ValueError):
        return empty_levels()
    muted = bool(values.get("mute")) or volume <= 0
    active = not (muted or values.get("pause") or values.get("core-idle") or values.get("eof-reached"))
    gain_db = 60 * math.log10(volume / 100) + extra_db if volume > 0 else -120
    channels = []
    index = 1
    while f"lavfi.astats.{index}.RMS_level" in metadata:
        try:
            rms = float(metadata[f"lavfi.astats.{index}.RMS_level"])
            peak = float(metadata[f"lavfi.astats.{index}.Peak_level"])
        except (KeyError, TypeError, ValueError):
            return empty_levels()
        if math.isnan(rms) or math.isnan(peak):
            return empty_levels()
        rms = max(-120, min(12, rms + gain_db)) if active else -120
        peak = max(-120, min(12, peak + gain_db)) if active else -120
        channels.append({"rmsDb": round(rms, 2), "peakDb": round(peak, 2),
                         "level": round(max(0, min(1, (rms + 60) / 60)), 4),
                         "peak": round(max(0, min(1, (peak + 60) / 60)), 4)})
        index += 1
    if not channels:
        return empty_levels()
    highest = max(channel["rmsDb"] for channel in channels)
    return {"active": bool(active), "available": True, "channels": channels,
            "db": f"{highest:.1f} dBFS" if highest > -120 else "−∞ dBFS", "muted": muted}
