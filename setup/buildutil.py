"""Helpers the setup and the builds share on every system (setup/setup.py, setup/update.py, windows/build.py):
the console tweak and the CPU ramp on Windows, and how many parallel jobs this PC takes.
"""
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def no_quickedit():
    """Windows consoles with QuickEdit on (the default) pause any program that writes to them while text is selected
    -- one stray click in the window and the setup or the build seems to hang until a key or a right-click ends the
    selection. Turn it off for this window (the console's own setting, only this window)."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)   # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if h and k.GetConsoleMode(h, ctypes.byref(mode)):
            # ENABLE_EXTENDED_FLAGS (0x80) must be set for the QuickEdit bit (0x40) to be changed
            k.SetConsoleMode(h, (mode.value & ~0x40) | 0x80)
    except (OSError, AttributeError, ValueError):
        pass


_RAMP = {'job': None, 'cores': None, 'gen': 0, 'off': False}


def cpu_ramp(first=4, secs=6.0):
    """Start the next heavy step (the lift's 32 workers, the 32 parallel compiles) on a few cores and widen to all of
    them over `secs`, so the CPU's power draw rises over seconds instead of jumping from idle to full in an instant.
    A PC whose power supply trips on sudden load steps switched itself off at the start of a lift (the GPU then had
    the same problem: NOTES fix 203). Windows: this process and everything it starts are put in a job object once,
    and the job's CPU affinity (which also moves processes already running) is widened step by step; the work and
    its total time stay the same apart from the first seconds. DS_CPU_RAMP=0 turns it off; other systems: nothing."""
    if os.name != 'nt' or _RAMP['off'] or os.environ.get('DS_CPU_RAMP') == '0':
        return
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32

        class BASIC(ctypes.Structure):   # JOBOBJECT_BASIC_LIMIT_INFORMATION
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        if _RAMP['job'] is None:
            proc, system = ctypes.c_size_t(), ctypes.c_size_t()
            k.GetCurrentProcess.restype = wintypes.HANDLE
            me = wintypes.HANDLE(k.GetCurrentProcess())
            if not k.GetProcessAffinityMask(me, ctypes.byref(proc), ctypes.byref(system)) or not proc.value:
                raise OSError('no affinity mask')
            cores = [1 << i for i in range(ctypes.sizeof(ctypes.c_size_t) * 8) if proc.value >> i & 1]
            if len(cores) <= first:
                _RAMP['off'] = True   # nothing to ramp on a small CPU
                return
            k.CreateJobObjectW.restype = wintypes.HANDLE
            job = k.CreateJobObjectW(None, None)
            if not job or not k.AssignProcessToJobObject(wintypes.HANDLE(job), me):
                raise OSError('job object refused')
            _RAMP['job'], _RAMP['cores'] = job, cores

        job, cores = _RAMP['job'], _RAMP['cores']
        _RAMP['gen'] += 1
        gen = _RAMP['gen']

        def set_cores(n):
            info = BASIC()
            info.LimitFlags = 0x10   # JOB_OBJECT_LIMIT_AFFINITY
            info.Affinity = sum(cores[:n])
            return k.SetInformationJobObject(wintypes.HANDLE(job), 2, ctypes.byref(info), ctypes.sizeof(info))

        if not set_cores(first):
            raise OSError('affinity refused')

        def widen():
            steps = 12
            for i in range(1, steps + 1):
                time.sleep(secs / steps)
                if _RAMP['gen'] != gen:
                    return   # a newer step started its own ramp
                set_cores(max(first, round(first + (len(cores) - first) * i / steps)))

        threading.Thread(target=widen, daemon=True).start()
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        _RAMP['off'] = True   # no job objects here (or nested jobs refused): run as before


def job_cap():
    """A per-PC limit on parallel work (compiles and the lift's workers): DS_MAX_JOBS, else "max_jobs" in
    setup/local_config.json (this PC's own file, never in git or a release). For a PC whose power supply switches it
    off under full CPU load: fewer threads at once means less power. None = no limit."""
    v = os.environ.get('DS_MAX_JOBS')
    if not v:
        try:
            with open(os.path.join(ROOT, 'setup', 'local_config.json')) as f:
                v = json.load(f).get('max_jobs')
        except (OSError, ValueError, AttributeError):
            v = None
    try:
        return max(1, int(v)) if v else None
    except (TypeError, ValueError):
        return None


def default_jobs():
    """One compile per core, but no more than ~1 per 2.5 GB of free memory: the biggest lifted files need
    about that much (a Steam Deck has 16 GB for 8 threads; Windows Sandbox gets every host thread but its own
    RAM). Windows: 1 per 2 GB of installed RAM; Linux: 1 per 2.5 GB of MemAvailable."""
    n = os.cpu_count() or 4
    if os.name == 'nt':
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                # by installed RAM (2 GB per compile): the page file grows as needed and most files need far
                # less than the biggest, so free memory right now would be too strict (a 64 GB PC: 32 jobs)
                n = max(1, min(n, int(st.ullTotalPhys / (2 * 1024 ** 3))))
        except (OSError, AttributeError, ValueError):
            pass
    else:
        try:
            with open('/proc/meminfo') as f:
                kb = next(int(l.split()[1]) for l in f if l.startswith('MemAvailable:'))
            n = max(1, min(n, int(kb / (2.5 * 1024 * 1024))))
        except (OSError, StopIteration, ValueError):
            pass
    cap = job_cap()
    return min(n, cap) if cap else n
