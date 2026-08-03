"""Windows Core Audio helpers for deterministic master-volume control."""

from __future__ import annotations

from contextlib import contextmanager
from ctypes import POINTER, c_float, c_int, c_uint, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD

from comtypes import (
    CLSCTX_ALL,
    COMMETHOD,
    GUID,
    HRESULT,
    CoCreateInstance,
    CoInitialize,
    CoUninitialize,
    IUnknown,
)


class SystemVolumeError(RuntimeError):
    pass


class _IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "Activate",
            (["in"], POINTER(GUID), "iid"),
            (["in"], DWORD, "dwClsCtx"),
            (["in"], c_void_p, "pActivationParams"),
            (["out", "retval"], POINTER(POINTER(IUnknown)), "ppInterface"),
        ),
    ]


class _IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "EnumAudioEndpoints",
            (["in"], c_int, "dataFlow"),
            (["in"], DWORD, "dwStateMask"),
            (["out"], POINTER(POINTER(IUnknown)), "ppDevices"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetDefaultAudioEndpoint",
            (["in"], c_int, "dataFlow"),
            (["in"], c_int, "role"),
            (["out", "retval"], POINTER(POINTER(_IMMDevice)), "ppEndpoint"),
        ),
    ]


class _IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], POINTER(IUnknown), "pNotify")),
        COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], POINTER(IUnknown), "pNotify")),
        COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pnChannelCount")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out", "retval"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMute", (["out", "retval"], POINTER(BOOL), "pbMute")),
    ]


_CLSID_MMDEVICE_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_RPC_E_CHANGED_MODE = -2147417850


def _error_code(error):
    code = getattr(error, "winerror", None)
    if code is None and getattr(error, "args", ()):
        code = error.args[0]
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


@contextmanager
def _com_apartment():
    """Initialize COM or reuse an apartment already initialized by the host."""
    initialized_here = False
    try:
        try:
            CoInitialize()
            initialized_here = True
        except OSError as error:
            if _error_code(error) != _RPC_E_CHANGED_MODE:
                raise
            # The frozen Eel worker can already be initialized as MTA. COM is
            # available in that case; only changing the apartment is rejected.
        yield
    finally:
        if initialized_here:
            CoUninitialize()


def _endpoint_volume():
    enumerator = CoCreateInstance(
        _CLSID_MMDEVICE_ENUMERATOR,
        interface=_IMMDeviceEnumerator,
        clsctx=CLSCTX_ALL,
    )
    device = enumerator.GetDefaultAudioEndpoint(0, 1)  # eRender, eMultimedia
    interface = device.Activate(_IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(_IAudioEndpointVolume))


def set_system_volume(percent: int) -> int:
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError("볼륨은 0부터 100 사이의 정수여야 합니다.")
    try:
        with _com_apartment():
            endpoint = _endpoint_volume()
            endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
            if percent > 0:
                endpoint.SetMute(False, None)
            return int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
    except Exception as error:
        raise SystemVolumeError(f"Windows 기본 출력 장치의 볼륨을 설정하지 못했습니다: {error}") from error


def adjust_system_volume(delta: int) -> tuple[int, int]:
    """Adjust master volume by a percentage-point delta and verify the result."""
    if (
        isinstance(delta, bool)
        or not isinstance(delta, int)
        or delta == 0
        or not -100 <= delta <= 100
    ):
        raise ValueError("볼륨 증감값은 -100부터 100 사이의 0이 아닌 정수여야 합니다.")
    try:
        with _com_apartment():
            endpoint = _endpoint_volume()
            before = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
            target = max(0, min(100, before + delta))
            endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
            if target > 0:
                endpoint.SetMute(False, None)
            applied = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
            return before, applied
    except Exception as error:
        raise SystemVolumeError(f"Windows 기본 출력 장치의 볼륨을 조절하지 못했습니다: {error}") from error


def set_system_muted(muted: bool) -> bool:
    try:
        with _com_apartment():
            endpoint = _endpoint_volume()
            endpoint.SetMute(bool(muted), None)
            return bool(endpoint.GetMute())
    except Exception as error:
        raise SystemVolumeError(f"Windows 기본 출력 장치의 음소거를 변경하지 못했습니다: {error}") from error


def get_system_volume_state() -> tuple[int, bool]:
    """Return master-volume percentage and mute state without changing either."""
    try:
        with _com_apartment():
            endpoint = _endpoint_volume()
            percent = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
            return percent, bool(endpoint.GetMute())
    except Exception as error:
        raise SystemVolumeError(f"Windows 기본 출력 장치의 상태를 읽지 못했습니다: {error}") from error
