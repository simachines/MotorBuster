#include <windows.h>
#include <dinput.h>

static LPDIRECTINPUT8 g_di = nullptr;
static LPDIRECTINPUTDEVICE8 g_dev = nullptr;
static LPDIRECTINPUTEFFECT g_eff = nullptr;

BOOL CALLBACK EnumCb(const DIDEVICEINSTANCE* inst, VOID*) {
    if (SUCCEEDED(g_di->CreateDevice(inst->guidInstance, &g_dev, nullptr)))
        return DIENUM_STOP;
    return DIENUM_CONTINUE;
}

extern "C" __declspec(dllexport) int di_init(HWND hwnd) {
    if (FAILED(DirectInput8Create(GetModuleHandle(nullptr), DIRECTINPUT_VERSION, IID_IDirectInput8, (VOID**)&g_di, nullptr)))
        return -1;
    g_di->EnumDevices(DI8DEVCLASS_GAMECTRL, EnumCb, nullptr, DIEDFL_ATTACHEDONLY);
    if (!g_dev) return -2;

    g_dev->SetDataFormat(&c_dfDIJoystick2);
    HWND h = hwnd ? hwnd : GetConsoleWindow();
    HRESULT hr = g_dev->SetCooperativeLevel(h, DISCL_EXCLUSIVE | DISCL_FOREGROUND);
    if (FAILED(hr)) hr = g_dev->SetCooperativeLevel(h, DISCL_EXCLUSIVE | DISCL_BACKGROUND);
    if (FAILED(hr)) hr = g_dev->SetCooperativeLevel(h, DISCL_NONEXCLUSIVE | DISCL_BACKGROUND);
    if (FAILED(hr)) return (int)hr;

    return g_dev->Acquire();
}

extern "C" __declspec(dllexport) int di_start_sine(DWORD duration_ms, DWORD magnitude, DWORD freq_hz) {
    if (!g_dev) return -1;
    if (g_eff) { g_eff->Stop(); g_eff->Release(); g_eff = nullptr; }

    DIPERIODIC per{};
    per.dwMagnitude = magnitude;                  // 0..10000 nominal
    per.lOffset = 0;
    per.dwPhase = 0;
    per.dwPeriod = freq_hz ? (1000000 / freq_hz) : 100000; // 100 ns units

    LONG dir[1] = { 0 };        // polar direction
    DWORD axis = DIJOFS_X;      // X axis

    DIEFFECT e{};
    e.dwSize = sizeof(e);
    e.dwFlags = DIEFF_POLAR;
    e.dwDuration = duration_ms ? duration_ms * 1000 : INFINITE; // 100 ns units
    e.dwGain = DI_FFNOMINALMAX;
    e.cAxes = 1;
    e.rgdwAxes = &axis;
    e.rglDirection = dir;
    e.cbTypeSpecificParams = sizeof(per);
    e.lpvTypeSpecificParams = &per;

    if (FAILED(g_dev->CreateEffect(GUID_Sine, &e, &g_eff, nullptr))) return -2;
    return g_eff->Start(1, 0);
}

extern "C" __declspec(dllexport) void di_stop() {
    if (g_eff) { g_eff->Stop(); g_eff->Release(); g_eff = nullptr; }
}

extern "C" __declspec(dllexport) void di_shutdown() {
    di_stop();
    if (g_dev) { g_dev->Unacquire(); g_dev->Release(); g_dev = nullptr; }
    if (g_di) { g_di->Release(); g_di = nullptr; }
}