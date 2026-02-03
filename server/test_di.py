import ctypes, os

dll_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "di_ffb.dll"))
print("dll:", dll_path)
dll = ctypes.CDLL(dll_path)

dll.di_init.argtypes = [ctypes.c_void_p]; dll.di_init.restype = ctypes.c_int
dll.di_start_sine.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]; dll.di_start_sine.restype = ctypes.c_int

r_init = dll.di_init(None)
print("init:", r_init)          # expect 0; -1 init fail; -2 no device
r_start = dll.di_start_sine(4000, 10000, 10)  # 4s, max nominal mag, 10 Hz
print("start:", r_start)        # expect 0
input("Press Enter to stop...")
dll.di_stop()
dll.di_shutdown()



