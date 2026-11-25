"""The native builds (Linux: Vulkan, no Wine; macOS: Metal) from your own disc, step by step. One driver for
both: linux/native/build.sh and mac/rebuild.sh are thin wrappers that pick the Python and the profile.

  python setup/native_build.py --profile linux|mac [step ...]

Steps, in order (default: all of them): deps venv game decrypt toolkit lift build

  deps     checks the build tools (Linux: clang, lld, cmake, ninja, SDL2, glslang / SPIRV-Tools, the Vulkan
           headers; macOS: Xcode's command-line tools and Homebrew's cmake, ninja, sdl2, glslang, spirv-cross).
           FFmpeg for the movies: only a warning without it
  venv     .venv from Python 3.10+ with setup/requirements.txt and pycryptodome (kept once complete)
  game     your disc image (DES_ISO; DES_KEY for a Redump image) -> game/ (setup/steps.py game). Skipped once
           game/ is extracted, so the image is needed only then
  decrypt  EBOOT.BIN -> EBOOT.elf without RPCS3 (tools/fetch_keys.py + tools/decrypt_self.py), checked against
           setup/setup.py's EBOOT_ELF_SHA256 (DES_ANY_EBOOT=1: a warning only)
  toolkit  deps/ps3recomp at the tested commit with patches/ps3recomp-local.patch
  lift     the recompiled PPU and SPU code (port/recompiled, port/spu) and patches/lifted-code.patch
  build    first the patches a source update changed (setup/steps.py sync, as setup/build.py does on
           Windows), then cmake + ninja -> port/build_linux or port/build_mac; incremental

Each step prints "==> <step>" first (the launchers' progress bars read them) and "==> done" at the end.
Everything it writes (game/, port/, deps/, .venv/) is game-derived or fetched, and .gitignore'd.

Environment (both):
  DES_ISO, DES_KEY  your disc image and (Redump) disc key
  DES_KEYS          the key file decrypt writes (default: ~/.local/share/... or ~/Library/Application Support/...
                    demons-souls-recomp/keys/self_keys.txt, outside the repository)
  DES_VENV          the Python environment (default .venv)
  DES_BUILD         the build folder (default port/build_linux, port/build_mac)
  DES_ANY_EBOOT=1   an EBOOT.elf that is not the tested one is only a warning
  JOBS              parallel compile jobs (ninja's -j; default: ninja's own)
Linux only (linux/native/build.sh has what each does):
  DES_LTO=1, DES_PGO=<file>|0, DES_MARCH=0, DES_AVX2=0, DES_DISTROBOX=<name>

Runs on any Python 3.8+ with nothing installed (it makes the venv); the steps that need the venv's packages
run in the venv's Python.
"""
import glob
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEPS = ('deps', 'venv', 'game', 'decrypt', 'toolkit', 'lift', 'build')
STEPS_PY = os.path.join(ROOT, 'setup', 'steps.py')
USRDIR = os.path.join(ROOT, 'game', 'PS3_GAME', 'USRDIR')
ELF = os.path.join(USRDIR, 'EBOOT.elf')


class Fail(Exception):
    pass


def say(msg):
    print('==> ' + msg, flush=True)


def out(msg):
    print(msg, flush=True)


def fail(msg):
    raise Fail(msg)


def have(tool):
    return shutil.which(tool) is not None


def run(argv, **kw):
    """A child with this process's stdout / stderr (the launchers read them); its exit code."""
    sys.stdout.flush()
    return subprocess.call(argv, cwd=ROOT, **kw)


def first_line(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
        return ((r.stdout or r.stderr).splitlines() or [''])[0].strip()
    except OSError:
        return ''


def env_flag(name, default=''):
    return os.environ.get(name, default)


# --------------------------------------------------------------------------------------------------------
# The two platforms: what differs between them. Everything else is in Build below.
# --------------------------------------------------------------------------------------------------------

class Profile:
    name = ''
    build_dir = ''
    keys_default = ''
    venv_hint = ''
    python_hint = ''
    git_hint = ''
    script = ''                 # what to tell the player to run

    def __init__(self, b):
        self.b = b

    def deps(self):
        raise NotImplementedError

    def build(self):
        raise NotImplementedError

    def built(self):
        return [os.path.join(self.b.build, 'DemonsSoulsRecomp')]


class Linux(Profile):
    name = 'linux'
    build_dir = os.path.join('port', 'build_linux')
    keys_default = os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'),
                                'demons-souls-recomp', 'keys', 'self_keys.txt')
    venv_hint = ' (Debian / Ubuntu: sudo apt install python3-venv)'
    python_hint = 'no Python 3.10 or newer (python3)'
    git_hint = 'git is missing'
    script = 'linux/native/build.sh'

    def __init__(self, b):
        super().__init__(b)
        self.box = self.container_name()

    # The container the compile runs in, if any: none inside one already, DES_DISTROBOX if given, else
    # des-build on a host without clang (SteamOS) when distrobox has it.
    @staticmethod
    def in_container():
        return os.path.isfile('/run/.containerenv') or os.path.isfile('/.dockerenv') or \
            bool(os.environ.get('CONTAINER_ID'))

    def container_name(self):
        if self.in_container():
            return ''
        if os.environ.get('DES_DISTROBOX'):
            return os.environ['DES_DISTROBOX']
        if not have('clang') and have('distrobox'):
            r = subprocess.run(['distrobox', 'list'], capture_output=True, text=True)
            names = [ln.split('|')[1].strip() for ln in r.stdout.splitlines() if ln.count('|') >= 2]
            if 'des-build' in names:
                return 'des-build'
        return ''

    def in_box(self, step, extra_env=()):
        """This step again, inside the container, with its "==> " lines left out (this run prints its own)."""
        argv = ['distrobox', 'enter', self.box, '--', 'env', 'DES_DISTROBOX=', 'DES_NESTED=1', *extra_env,
                'bash', os.path.join(ROOT, 'linux', 'native', 'build.sh'), step]
        sys.stdout.flush()
        p = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, text=True, errors='replace', bufsize=1)
        for line in p.stdout:
            if not line.startswith('==> '):
                sys.stdout.write(line)
                sys.stdout.flush()
        if p.wait():
            raise SystemExit(1)

    def deps(self):
        if self.box:
            out("  the compile runs in the distrobox container '%s'; checking the tools there" % self.box)
            self.in_box('deps')
            return
        missing = [t for t in ('clang', 'clang++', 'ld.lld', 'cmake', 'ninja', 'glslangValidator') if not have(t)]
        for h in ('vulkan/vulkan.h', 'SDL2/SDL.h', 'glslang/Public/ShaderLang.h', 'spirv-tools/libspirv.h'):
            if not any(os.path.isfile(os.path.join(inc, h)) for inc in ('/usr/include', '/usr/local/include')):
                missing.append('<%s>' % h)
        if sys.version_info < (3, 10):
            missing.append('python3 (3.10 or newer)')
        if missing:
            fail('missing: %s. Arch: sudo pacman -S clang lld cmake ninja sdl2 glslang spirv-tools vulkan-headers '
                 'ffmpeg python git\n'
                 '  Debian / Ubuntu: sudo apt install clang lld cmake ninja-build libsdl2-dev glslang-dev '
                 'glslang-tools spirv-tools libvulkan-dev libavcodec-dev python3-venv git\n'
                 '  Fedora: sudo dnf install clang lld cmake ninja-build SDL2-devel glslang-devel spirv-tools-devel '
                 'vulkan-loader-devel ffmpeg-free-devel python3 git\n'
                 '  On SteamOS: make the build container once (docs/LINUX_NATIVE.md) and run this again.'
                 % ' '.join(missing))
        if not (os.path.isfile('/usr/include/libavcodec/avcodec.h') or
                glob.glob('/usr/include/*/libavcodec/avcodec.h')):
            out('  no FFmpeg headers (libavcodec): the game runs, but its movies are skipped')
        out('  %s, cmake %s, ninja %s, %s' % (
            first_line(['clang', '--version']), first_line(['cmake', '--version']).split(' ')[-1],
            first_line(['ninja', '--version']), first_line([self.b.py, '--version'])))

    def pgo(self):
        p = env_flag('DES_PGO')
        if not p:
            d = os.path.join(ROOT, 'port', 'pgo', 'des.profdata')
            p = d if os.path.isfile(d) else ''
        return '' if p == '0' else p

    @staticmethod
    def cpu_flags():
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if line.startswith('flags'):
                        return set(line.split(':', 1)[1].split())
        except OSError:
            pass
        return None     # unknown: assume a current CPU

    def build(self):
        if self.box:
            out("  compiling in the distrobox container '%s'" % self.box)
            self.in_box('build', ['DES_BUILD=' + self.b.build, 'DES_LTO=' + env_flag('DES_LTO'),
                                  'DES_PGO=' + self.pgo(), 'JOBS=' + env_flag('JOBS')])
            with open(os.path.join(self.b.build, '.distrobox'), 'w') as f:
                f.write(self.box + '\n')
            return
        if not have('clang'):
            fail('clang is missing: run the deps step for what to install')
        flags, lflags = [], ['-fuse-ld=lld']
        if env_flag('DES_LTO', '0') == '1':
            flags.append('-flto=thin')
            lflags += ['-flto=thin', '-Wl,--thinlto-cache-dir=' + os.path.join(self.b.build, 'ltocache')]
        pgo = self.pgo()
        if pgo:
            if not os.path.isfile(pgo):
                fail('DES_PGO=%s: no such profile' % pgo)
            flags += ['-fprofile-use=' + pgo, '-Wno-profile-instr-unprofiled', '-Wno-profile-instr-out-of-date',
                      '-Wno-backend-plugin']
        # DS_AVX2: the SPU and VMX helpers' AVX2 / FMA paths, on CPUs that have them (every x86-64 since ~2013;
        # the Steam Deck's too). DES_AVX2=0 builds for an older CPU.
        cpu = self.cpu_flags()
        avx = env_flag('DES_AVX2', '1') != '0' and (cpu is None or 'avx2' in cpu)
        # x86-64-v3 (BMI2 shifts, LZCNT, MOVBE, ...) on a CPU that has all of it -- every AVX2 CPU in practice:
        # +3.7% in Boletaria on the Deck. -ffp-contract=off keeps FMA from fusing a multiply and an add the
        # game does separately (the PPU code already had it; this covers the SPU code too). DES_MARCH=0: off.
        if avx and env_flag('DES_MARCH', '1') != '0' and cpu is not None and \
                all(f in cpu for f in ('avx2', 'bmi1', 'bmi2', 'f16c', 'fma', 'abm', 'movbe')):
            flags += ['-march=x86-64-v3', '-ffp-contract=off']
        f = ' '.join(flags)
        self.b.cmake(['-DCMAKE_C_COMPILER=' + shutil.which('clang'), '-DCMAKE_CXX_COMPILER=' + shutil.which('clang++'),
                      '-DCMAKE_C_FLAGS=' + f, '-DCMAKE_CXX_FLAGS=' + f, '-DCMAKE_EXE_LINKER_FLAGS=' + ' '.join(lflags),
                      '-DDS_AVX2=' + ('ON' if avx else 'OFF'), '-DPS3RECOMP_BUILD_TESTS=OFF',
                      '-DPS3RECOMP_RSX_VULKAN=ON'])
        self.b.ninja(['DemonsSoulsRecomp'])
        if not self.in_container():
            try:
                os.remove(os.path.join(self.b.build, '.distrobox'))
            except OSError:
                pass

    def nested(self):
        return bool(os.environ.get('DES_NESTED'))


class Mac(Profile):
    name = 'mac'
    build_dir = os.path.join('port', 'build_mac')
    keys_default = os.path.expanduser('~/Library/Application Support/demons-souls-recomp/keys/self_keys.txt')
    venv_hint = ''
    python_hint = 'no Python 3.10 or newer (brew install python@3.12)'
    git_hint = "git is missing (Apple's command-line tools have it)"
    script = 'mac/rebuild.sh'

    def brew(self):
        return os.environ.get('DES_BREW') or os.environ.get('HOMEBREW_PREFIX') or '/opt/homebrew'

    def deps(self):
        if subprocess.call(['xcode-select', '-p'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
            fail("Apple's command-line tools are missing: run xcode-select --install")
        missing = [t for t in ('cmake', 'ninja', 'glslangValidator', 'spirv-cross') if not have(t)]
        if not os.path.exists(os.path.join(self.brew(), 'lib', 'libSDL2.dylib')) and \
                not first_line(['brew', '--prefix', 'sdl2']):
            missing.append('sdl2')
        if sys.version_info < (3, 10):
            missing.append('python@3.12')
        if missing:
            missing = ['glslang' if m == 'glslangValidator' else m for m in missing]
            fail('missing: %s. Install them with: brew install %s' % (' '.join(missing), ' '.join(missing)))
        if not glob.glob(os.path.join(self.brew(), 'lib', 'libavcodec.[0-9]*.dylib')):
            out('  no FFmpeg (brew install ffmpeg): the game runs, but its movies are skipped')
        out('  cmake %s, ninja %s, %s' % (first_line(['cmake', '--version']).split(' ')[-1],
                                          first_line(['ninja', '--version']), first_line([self.b.py, '--version'])))

    def built(self):
        return super().built() + [os.path.join(self.b.build, 'DemonsSoulsRecomp_window')]

    def build(self):
        # DS_AVX2=OFF: on arm64 the SPU and VMX helpers take their exact scalar paths
        self.b.cmake(['-DDS_AVX2=OFF', '-DPython3_EXECUTABLE=' + self.b.vpy])
        self.b.ninja([])

    def after_build(self):
        # The launcher window (DemonsSoulsLauncher.app next to mac/). Never stops the build: mac/play.sh
        # plays without it.
        if run(['zsh', os.path.join(ROOT, 'mac', 'launcher_native', 'build.sh')]):
            out('  DemonsSoulsLauncher.app not built; mac/play.sh starts the game')

    def nested(self):
        return False


# --------------------------------------------------------------------------------------------------------
# The steps
# --------------------------------------------------------------------------------------------------------

class Build:
    def __init__(self, profile):
        self.py = sys.executable
        self.venv = os.environ.get('DES_VENV') or os.path.join(ROOT, '.venv')
        self.vpy = os.path.join(self.venv, 'bin', 'python')
        self.build = os.environ.get('DES_BUILD') or os.path.join(ROOT, profile.build_dir)
        self.p = profile(self)
        self.keys = os.environ.get('DES_KEYS') or self.p.keys_default

    def need_venv(self):
        if not os.access(self.vpy, os.X_OK):
            fail('no Python environment in %s yet: run the venv step first' % self.venv)

    def steps_py(self, *args):
        return run([self.vpy, '-u', STEPS_PY, *args])

    # -- deps ------------------------------------------------------------------------------------------
    def step_deps(self):
        say('deps (the build tools)')
        self.p.deps()

    # -- venv ------------------------------------------------------------------------------------------
    def vpy_ok(self, code):
        return os.access(self.vpy, os.X_OK) and subprocess.call(
            [self.vpy, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0

    def step_venv(self):
        say('venv (the Python environment)')
        if sys.version_info < (3, 10):
            fail(self.p.python_hint)
        with open(os.path.join(ROOT, 'setup', 'requirements.txt'), 'rb') as f:
            req = hashlib.sha256(f.read()).hexdigest()[:16]
        ready = os.path.join(self.venv, '.des-ready')
        try:
            with open(ready) as f:
                current = f.read().strip() == req
        except OSError:
            current = False
        if not current or not self.vpy_ok('import sys, Crypto, cryptography; assert sys.version_info >= (3, 10)'):
            # (a system Python update -- SteamOS 3.13 -> 3.14 -- leaves the old environment without pip: make
            # it anew)
            if not self.vpy_ok('import sys; assert sys.version_info >= (3, 10)') or \
                    not self.vpy_ok('import pip'):
                shutil.rmtree(self.venv, ignore_errors=True)
                if run([self.py, '-m', 'venv', self.venv]):
                    fail('could not make the Python environment in %s%s' % (self.venv, self.p.venv_hint))
            if run([self.vpy, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r',
                    os.path.join(ROOT, 'setup', 'requirements.txt'), 'pycryptodome']):
                fail('could not install the Python packages (pip). Check the internet connection and run this '
                     'again.')
            with open(ready, 'w') as f:
                f.write(req + '\n')
        out('  %s in %s' % (first_line([self.vpy, '--version']), self.venv))

    # -- game ------------------------------------------------------------------------------------------
    def step_game(self):
        say('game (the game files from your disc image)')
        self.need_venv()
        if self.steps_py('game', os.environ.get('DES_ISO', ''), '--key', os.environ.get('DES_KEY', '')):
            raise SystemExit(1)

    # -- decrypt ---------------------------------------------------------------------------------------
    def step_decrypt(self):
        say('decrypt (EBOOT.BIN -> EBOOT.elf)')
        self.need_venv()
        if not os.path.isfile(ELF):
            if not os.path.isfile(os.path.join(USRDIR, 'EBOOT.BIN')):
                fail('no game/PS3_GAME/USRDIR/EBOOT.BIN: run the game step first')
            r = subprocess.run([self.vpy, os.path.join(ROOT, 'tools', 'fetch_keys.py'), '--out', self.keys],
                               cwd=ROOT, stdout=subprocess.PIPE, text=True)
            if r.returncode:
                fail('could not get the decryption keys (they are downloaded once from RPCS3\'s source on '
                     'GitHub). Check the internet connection and run this again.')
            keys = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else self.keys
            part = ELF + '.partial'
            if os.path.exists(part):
                os.remove(part)
            if run([self.vpy, os.path.join(ROOT, 'tools', 'decrypt_self.py'), os.path.join(USRDIR, 'EBOOT.BIN'),
                    '-o', part, '--keys', keys]):
                if os.path.exists(part):
                    os.remove(part)
                fail('could not decrypt game/PS3_GAME/USRDIR/EBOOT.BIN (is it Demon\'s Souls, BLUS-30443 or '
                     'BLES-00932?)')
            os.replace(part, ELF)
        argv = [self.vpy, STEPS_PY, 'check-elf'] + (['--any'] if os.environ.get('DES_ANY_EBOOT') else [])
        if run(argv):
            raise SystemExit(1)

    # -- toolkit / lift --------------------------------------------------------------------------------
    def step_toolkit(self):
        say('toolkit (ps3recomp at the tested commit, with this port\'s patch)')
        self.need_venv()
        if not have('git'):
            fail(self.p.git_hint)
        if self.steps_py('toolkit'):
            raise SystemExit(1)

    def step_lift(self):
        say('lift (the game\'s PPU and SPU code to C/C++)')
        self.need_venv()
        if self.steps_py('lift'):
            raise SystemExit(1)

    # -- build -----------------------------------------------------------------------------------------
    def cmake(self, extra):
        argv = ['cmake', '-S', os.path.join(ROOT, 'project'), '-B', self.build, '-G', 'Ninja',
                '-DCMAKE_BUILD_TYPE=Fast', '-DDS_BOOT_HARNESS=ON', '-DPS3RECOMP_FFMPEG_RUNTIME=ON',
                '-DPS3RECOMP_DIR=' + os.path.join(ROOT, 'deps', 'ps3recomp'),
                '-DRECOMP_DIR=' + os.path.join(ROOT, 'port', 'recompiled'),
                '-DSPU_DIR=' + os.path.join(ROOT, 'port', 'spu')] + extra
        if run(argv):
            fail('cmake could not configure the build (see the lines above)')

    def ninja(self, targets):
        jobs = os.environ.get('JOBS', '')
        argv = ['ninja', '-C', self.build] + (['-j', jobs] if jobs and jobs != '0' else []) + targets
        if run(argv):
            fail('the build failed (see the lines above)')
        missing = [p for p in self.p.built() if not os.access(p, os.X_OK)]
        if missing:
            fail('the build ended without ' + ' and '.join(missing))
        out('  built: ' + self.p.built()[0])

    def step_build(self):
        rel = os.path.relpath(self.build, ROOT)
        say('build (cmake + ninja: %s)' % (rel if not rel.startswith('..') else self.build))
        nested = self.p.nested()
        if not os.path.isfile(os.path.join(ROOT, 'port', '.lifted')):
            fail('nothing lifted yet (port/recompiled): run the lift step first')
        if not nested:
            # A source update can change either patch without changing the build scripts: apply it before
            # compiling, just as setup/build.py does on Windows. (A nested run inside the build container has
            # just had this from the run on the host.)
            self.need_venv()
            if self.steps_py('sync'):
                fail('the generated sources need updating: run %s toolkit lift build' % self.p.script)
        for d in ('hdd0', 'hdd1', 'no_flash', 'logs'):
            os.makedirs(os.path.join(ROOT, d), exist_ok=True)
        os.makedirs(self.build, exist_ok=True)
        self.p.build()
        if nested:
            return
        # The keyboard / mouse button icons, from this copy of the game (NOTES fixes 91, 134). Never stops the
        # build: without them the game shows the PS3 buttons.
        if run([self.vpy, os.path.join(ROOT, 'tools', 'kbm_icons.py'), USRDIR, os.path.join(ROOT, 'textures', 'kbm'),
                '--if-stale']):
            out('  keyboard / mouse button icons not made; the game shows the PS3 buttons')
        if hasattr(self.p, 'after_build'):
            self.p.after_build()


PROFILES = {'linux': Linux, 'mac': Mac}


def main(argv):
    if len(argv) < 2 or argv[0] != '--profile' or argv[1] not in PROFILES:
        print(__doc__)
        return 2
    steps = argv[2:] or list(STEPS)
    for s in steps:
        if s not in STEPS:
            print("unknown step '%s' (%s)" % (s, ' '.join(STEPS)), file=sys.stderr)
            return 2
    os.chdir(ROOT)
    b = Build(PROFILES[argv[1]])
    try:
        for s in steps:
            getattr(b, 'step_' + s)()
    except Fail as e:
        out('ERROR: %s' % e)
        return 1
    say('done')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
