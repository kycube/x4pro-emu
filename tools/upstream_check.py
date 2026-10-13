#!/usr/bin/env python3
r"""What moved upstream since this repository's pins: a report, not a gate (docs/upstream.md).

  upstream_check.py [report] [--json | --markdown] [--save F] [--load F] [--github-output F]
                    [--backend auto|gh|git]
  upstream_check.py tests-summary --junit F [--junit F2] [--drift F] [--logs DIR]
                    [--instance-prefix P] [--title T] [--note TEXT]...

`report` (the default) compares, without building anything:

  - CrossPoint Reader (the `firmware/` submodule): its latest release against the gitlink this
    repository commits, the commits in between, its default branch, the freeink-sdk commit and the
    PlatformIO platform that release pins, and the pioarduino core its own CI installs;
  - Espressif QEMU `esp-develop` HEAD against the Makefile's QEMU_REF;
  - the pioarduino platform and core releases against what CrossPoint pins and the Makefile's
    PIO_CORE_VERSION installs (the one core pin: ci.yml and upstream.yml read it there);
  - this checkout (no network): a qemu/build configured against Homebrew Cellar paths that no
    longer exist (the 2026-10-12 libgcrypt case), venvs whose base interpreter is gone (.venv,
    .venv-pio, ~/.platformio/penv), and a firmware/ checked out away from the pin.

The stock firmware is not watched: its OTA packages are encrypted and reach the device only through
the device's own update prompt. The report says so every time.

The pins are read from this checkout (the Makefile, `git ls-tree HEAD firmware`); upstream is
asked through `gh api` when `gh` is installed and authenticated (GH_TOKEN in CI), else through
plain git (`git ls-remote`, and a treeless fetch into a temporary bare repository for commit
counts and gitlinks). A gh call that fails is retried through git.

Exit status: 0 = the report was written, whatever it says; 2 = some remote could not be read (the
report still prints and lists them under `errors`). `tests-summary` turns pytest's JUnit XML, the
`--golden-drift` file (tests/conftest.py) and the emulator consoles into Markdown for
$GITHUB_STEP_SUMMARY: exit 0, or 1 when a test failed or errored in its last attempt or a console
holds a crash marker.
"""
import argparse, base64, datetime, glob, json, os, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CROSSPOINT = 'crosspoint-reader/crosspoint-reader'
FREEINK = 'Free-Ink/freeink-sdk'
QEMU_REPO = 'espressif/qemu'
QEMU_BRANCH = 'esp-develop'
PIO_PLATFORM = 'pioarduino/platform-espressif32'
PIO_CORE = 'pioarduino/platformio-core'
QEMU_DEPTH = 400        # plain-git mode: how far back esp-develop is fetched to count commits

# ESP-IDF's panic and heap-check output. A healthy CrossPoint or stock boot prints none of these
# (checked over every .x4emu/*/console.log on the desk Mac, 2026-10-12: only a deliberately broken
# build had any). tests/conftest.py's --golden-drift option imports this list.
CRASH_MARKERS = (
    'Guru Meditation Error',
    'abort() was called',
    'Backtrace: 0x',
    'assert failed:',
    '***ERROR*** A stack overflow',
    'CORRUPT HEAP',
    'Stack smashing protect failure',
)

MANUAL_STOCK = ('Stock firmware (xteink_app): not watched. Its OTA packages are encrypted and arrive only '
                'through the device; check the device\'s own update prompt. A new version needs a backup '
                '(tools/device.py backup), then the re-baseline the 7.6.8 entry of docs/log.md describes.')


class NetError(Exception):
    pass


def _run(cmd, timeout=90, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NetError(f'{" ".join(cmd[:3])}: {e}')
    if r.returncode:
        last = (r.stderr or r.stdout).strip().splitlines()
        raise NetError(f'{" ".join(cmd[:4])}: {last[-1] if last else f"exit {r.returncode}"}')
    return r.stdout


# --- versions ---------------------------------------------------------------------------------------

def parse_version(tag):
    """'1.6.5' / 'v1.5.0' -> (1, 6, 5, 0); a numeric build suffix counts ('55.03.312-1' -> (55, 3, 312, 1));
    prereleases ('1.6.5rc', 'v1.5.0-rc-4') and other tags -> None."""
    m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-(\d+))?', (tag or '').strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def latest_stable_tag(tags, like=None):
    """The highest stable version tag of an iterable of tag names, or None. With `like` (a version
    string), only tags with the same first component: pioarduino's platform went from 2024.07.00 to
    51.x..55.x, so a plain maximum would pick the old scheme."""
    major = (parse_version(like) or (None,))[0] if like else None
    best = None
    for t in tags:
        v = parse_version(t)
        if v and (major is None or v[0] == major) and (best is None or v > best[0]):
            best = (v, t)
    return best[1] if best else None


def parse_ls_remote(text):
    """`git ls-remote` output -> {ref: sha}; a peeled `^{}` line (annotated tag) wins for its tag."""
    refs = {}
    for line in text.splitlines():
        if '\t' not in line:
            continue
        sha, ref = line.split('\t', 1)
        if ref.endswith('^{}'):
            refs[ref[:-3]] = sha
        else:
            refs.setdefault(ref, sha)
    return refs


def platform_from_ini(text):
    """The `platform =` line of a platformio.ini -> {'spec', 'version'} (version None if not a
    pioarduino release URL or an `espressif32@X` spec)."""
    m = re.search(r'^\s*platform\s*=\s*(\S+)', text or '', re.M)
    if not m:
        return None
    spec = m.group(1)
    v = re.search(r'/download/([^/]+)/platform-espressif32\.zip', spec) or re.search(r'@\s*([\w.\-]+)$', spec)
    return {'spec': spec, 'version': v.group(1) if v else None}


def pio_core_from_workflow(text):
    """The pioarduino core version a GitHub workflow or a Makefile installs (`.../platformio-core/
    archive/refs/tags/vX.zip`, a `PIO_CORE_VERSION: vX` env line or a `PIO_CORE_VERSION ?= vX`
    assignment), or None."""
    m = re.search(r'platformio-core/archive/refs/tags/(v?[\d.]+)\.zip', text or '') or \
        re.search(r'^\s*PIO_CORE_VERSION\s*(?::|\??=)\s*(v?[\d.]+)', text or '', re.M)
    return m.group(1) if m else None


# --- the two ways of asking GitHub ------------------------------------------------------------------

class GitBackend:
    """Plain git: ls-remote, plus treeless fetches into temporary bare repositories."""
    name = 'git'

    def __init__(self):
        self._tmp = None
        self._bare = {}
        self._fetched = {}

    @staticmethod
    def url(repo):
        return f'https://github.com/{repo}.git'

    def close(self):
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def ls_remote(self, repo, *patterns, opts=()):
        return parse_ls_remote(_run(['git', 'ls-remote', *opts, self.url(repo), *patterns], timeout=60))

    def latest_release(self, repo, like=None):
        refs = self.ls_remote(repo, opts=('--tags',))
        tags = {r[len('refs/tags/'):]: s for r, s in refs.items() if r.startswith('refs/tags/')}
        tag = latest_stable_tag(tags, like)
        if not tag:
            raise NetError(f'{repo}: no X.Y.Z tag')
        return {'tag': tag, 'sha': tags[tag], 'published': None}

    def head(self, repo, branch):
        refs = self.ls_remote(repo, f'refs/heads/{branch}')
        if f'refs/heads/{branch}' not in refs:
            raise NetError(f'{repo}: no branch {branch}')
        return refs[f'refs/heads/{branch}']

    def default_branch(self, repo):
        out = _run(['git', 'ls-remote', '--symref', self.url(repo), 'HEAD'], timeout=60)
        m = re.search(r'^ref: refs/heads/(\S+)\s+HEAD', out, re.M)
        if not m:
            raise NetError(f'{repo}: no symbolic HEAD')
        return m.group(1)

    def _repo(self, repo):
        if repo not in self._bare:
            if not self._tmp:
                self._tmp = tempfile.mkdtemp(prefix='upstream-check-')
            d = os.path.join(self._tmp, repo.replace('/', '_') + '.git')
            _run(['git', 'init', '-q', '--bare', d])
            _run(['git', '-C', d, 'remote', 'add', 'origin', self.url(repo)])
            self._bare[repo] = d
        return self._bare[repo]

    def _fetch(self, repo, ref, depth=None):
        """Fetch one ref (a tag or branch name, or a commit SHA) treeless; -> its commit SHA."""
        key = (repo, ref, depth)
        if key in self._fetched:
            return self._fetched[key]
        d = self._repo(repo)
        if re.fullmatch(r'[0-9a-f]{40}', ref):
            spec = ref
        else:
            spec = f'+{ref}:refs/upstream-check/{len(self._fetched)}'
        cmd = ['git', '-C', d, 'fetch', '-q', '--no-tags', '--filter=tree:0', 'origin', spec]
        if depth:
            cmd[4:4] = [f'--depth={depth}']
        _run(cmd, timeout=180)
        sha = _run(['git', '-C', d, 'rev-parse', 'FETCH_HEAD^{commit}']).strip()
        self._fetched[key] = sha
        return sha

    def resolve(self, repo, ref):
        return self._fetch(repo, ref)

    def compare(self, repo, base, head, depth=None):
        d = self._repo(repo)
        b = self._fetch(repo, base, depth=1 if depth else None)
        h = self._fetch(repo, head, depth=depth)
        if depth:
            # A shallow history: count only if the base lies inside what was fetched.
            if b not in _run(['git', '-C', d, 'rev-list', h]).split():
                return {'status': 'ahead', 'ahead_by': None, 'behind_by': None,
                        'note': f'more than {depth} commits apart (plain git, shallow)'}
            n = int(_run(['git', '-C', d, 'rev-list', '--count', f'{b}..{h}']).strip())
            return {'status': 'identical' if n == 0 else 'ahead', 'ahead_by': n, 'behind_by': 0,
                    'note': 'plain git, shallow: approximate across merges'}
        out = _run(['git', '-C', d, 'rev-list', '--left-right', '--count', f'{b}...{h}']).split()
        behind, ahead = int(out[0]), int(out[1])
        status = 'identical' if not (ahead or behind) else 'ahead' if not behind else \
            'behind' if not ahead else 'diverged'
        return {'status': status, 'ahead_by': ahead, 'behind_by': behind}

    def gitlink(self, repo, ref, path):
        sha = self._fetch(repo, ref)
        out = _run(['git', '-C', self._repo(repo), 'ls-tree', sha, path]).split()
        if len(out) < 3 or out[1] != 'commit':
            raise NetError(f'{repo}@{ref}: {path} is not a submodule')
        return out[2]

    def file(self, repo, ref, path):
        sha = self._fetch(repo, ref)
        return _run(['git', '-C', self._repo(repo), 'show', f'{sha}:{path}'], timeout=120)


class GhBackend:
    """The GitHub REST API through `gh api` (authenticated: 5000 requests an hour)."""
    name = 'gh'

    def close(self):
        pass

    @staticmethod
    def available():
        if not shutil.which('gh'):
            return False
        if os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'):
            return True
        try:
            return subprocess.run(['gh', 'auth', 'token'], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def api(self, path, jq=None, accept=None):
        cmd = ['gh', 'api', path]
        if accept:
            cmd += ['-H', f'Accept: {accept}']
        if jq:
            cmd += ['--jq', jq]
        return _run(cmd, timeout=60)

    def latest_release(self, repo, like=None):
        d = json.loads(self.api(f'repos/{repo}/releases/latest', jq='{tag: .tag_name, published: .published_at}'))
        d['published'] = (d.get('published') or '')[:10] or None
        d['sha'] = self.resolve(repo, d['tag'])
        return d

    def head(self, repo, branch):
        return self.api(f'repos/{repo}/branches/{branch}', jq='.commit.sha').strip()

    def default_branch(self, repo):
        return self.api(f'repos/{repo}', jq='.default_branch').strip()

    def resolve(self, repo, ref):
        return self.api(f'repos/{repo}/commits/{ref}', accept='application/vnd.github.sha').strip()

    def compare(self, repo, base, head, depth=None):
        return json.loads(self.api(f'repos/{repo}/compare/{base}...{head}',
                                   jq='{status, ahead_by, behind_by}'))

    def gitlink(self, repo, ref, path):
        d = json.loads(self.api(f'repos/{repo}/contents/{path}?ref={ref}', jq='{type, sha}'))
        if d.get('type') != 'submodule':
            raise NetError(f'{repo}@{ref}: {path} is not a submodule')
        return d['sha']

    def file(self, repo, ref, path):
        return base64.b64decode(self.api(f'repos/{repo}/contents/{path}?ref={ref}', jq='.content')).decode()


class Remote:
    """gh first when it is usable, git as the fallback; every failure lands in `errors`."""

    def __init__(self, backend='auto'):
        self.git = GitBackend()
        use_gh = backend == 'gh' or (backend == 'auto' and GhBackend.available())
        self.gh = GhBackend() if use_gh else None
        self.errors, self.notes = [], []

    @property
    def name(self):
        return self.gh.name if self.gh else self.git.name

    def close(self):
        self.git.close()

    def ask(self, what, method, *args, **kw):
        """`method` of the gh backend, else (or on its failure) of the git one; None on failure."""
        if self.gh:
            try:
                return getattr(self.gh, method)(*args, **kw)
            except (NetError, ValueError, KeyError) as e:
                self.notes.append(f'{what}: gh failed ({e}), asked git')
        try:
            return getattr(self.git, method)(*args, **kw)
        except (NetError, ValueError, KeyError) as e:
            self.errors.append(f'{what}: {e}')
            return None


# --- this checkout ----------------------------------------------------------------------------------

def _read(path):
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _git(*args, cwd=ROOT):
    try:
        r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def local_pins(root=ROOT):
    mk = _read(os.path.join(root, 'Makefile')) or ''
    m = re.search(r'^QEMU_REF\s*\??=\s*([0-9a-f]{7,40})', mk, re.M)
    tree = (_git('ls-tree', 'HEAD', 'firmware', cwd=root) or '').split()
    return {
        'qemu_ref': m.group(1) if m else None,
        'crosspoint': tree[2] if len(tree) >= 3 and tree[1] == 'commit' else None,
        'pio_core': pio_core_from_workflow(mk),
    }


def _firmware_has(sha, root=ROOT):
    fw = os.path.join(root, 'firmware')
    return os.path.exists(os.path.join(fw, '.git')) and _git('cat-file', '-e', f'{sha}^{{commit}}', cwd=fw) is not None


def stale_cellar_paths(build_ninja_text):
    """Homebrew keg paths (`<prefix>/Cellar/<formula>/<version>`) a configured build refers to that
    no longer exist: Homebrew removed that version, the next link or reconfigure fails."""
    kegs = set(re.findall(r'(/(?:opt/homebrew|usr/local|home/linuxbrew/\.linuxbrew)/Cellar/[^/\s\'"]+/[^/\s\'"]+)',
                          build_ninja_text or ''))
    return sorted(k for k in kegs if not os.path.isdir(k))


def venv_base(cfg_text):
    m = re.search(r'^home\s*=\s*(.+)$', cfg_text or '', re.M)
    return m.group(1).strip() if m else None


def venvs(root=ROOT):
    """(pyvenv.cfg, how to recreate it) for every venv `make` relies on."""
    return ((os.path.join(root, '.venv', 'pyvenv.cfg'), 'rm -rf .venv && make setup'),
            (os.path.join(root, '.venv-pio', 'pyvenv.cfg'), 'rm -rf .venv-pio && make firmware (its rule recreates it)'),
            (os.path.expanduser('~/.platformio/penv/pyvenv.cfg'), 'delete ~/.platformio/penv; the next pio run rebuilds it'))


def local_state(root=ROOT, pins=None):
    """Drift on this machine (no network): what `make` would trip over next."""
    pins = pins or local_pins(root)
    out = {'qemu_build_stale_kegs': [], 'broken_venvs': [], 'firmware_checkout': None}
    bn = os.path.join(root, 'qemu', 'build', 'build.ninja')
    if os.path.exists(bn):
        out['qemu_build_stale_kegs'] = stale_cellar_paths(_read(bn))
    for cfg, fix in venvs(root):
        home = venv_base(_read(cfg))
        if home and not os.path.isdir(home):
            out['broken_venvs'].append({'venv': os.path.dirname(cfg), 'missing_base': home, 'fix': fix})
    fw = os.path.join(root, 'firmware')
    if os.path.exists(os.path.join(fw, '.git')):
        head = _git('rev-parse', 'HEAD', cwd=fw)
        out['firmware_checkout'] = {'head': head, 'at_pin': bool(head and head == pins.get('crosspoint'))}
    return out


# --- the report -------------------------------------------------------------------------------------

def gather(remote, root=ROOT):
    pins = local_pins(root)
    rep = {'checked_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'backend': remote.name, 'pins': pins}

    # CrossPoint Reader
    cp = {'repo': CROSSPOINT, 'pin': pins['crosspoint']}
    pin = pins['crosspoint']
    if pin and _firmware_has(pin, root):
        cp['pin_describe'] = _git('describe', '--tags', pin, cwd=os.path.join(root, 'firmware'))
        tree = (_git('ls-tree', pin, 'freeink-sdk', cwd=os.path.join(root, 'firmware')) or '').split()
        sdk_pin = tree[2] if len(tree) >= 3 else None
        ini_pin = _git('show', f'{pin}:platformio.ini', cwd=os.path.join(root, 'firmware'))
    elif pin:
        sdk_pin = remote.ask('freeink-sdk at the pin', 'gitlink', CROSSPOINT, pin, 'freeink-sdk')
        ini_pin = remote.ask('platformio.ini at the pin', 'file', CROSSPOINT, pin, 'platformio.ini')
    else:
        remote.errors.append('CrossPoint pin: `git ls-tree HEAD firmware` gave no gitlink')
        sdk_pin = ini_pin = None
    rel = remote.ask('CrossPoint latest release', 'latest_release', CROSSPOINT)
    cp['latest_release'] = rel
    branch = remote.ask('CrossPoint default branch', 'default_branch', CROSSPOINT)
    if branch:
        cp['default_branch'] = {'name': branch, 'sha': remote.ask(f'CrossPoint {branch}', 'head', CROSSPOINT, branch)}
    if pin and rel:
        cp['release_vs_pin'] = remote.ask('CrossPoint release vs pin', 'compare', CROSSPOINT, pin, rel['tag'])
    if pin and branch:
        cp['branch_vs_pin'] = remote.ask(f'CrossPoint {branch} vs pin', 'compare', CROSSPOINT, pin, branch)
    sdk = {'repo': FREEINK, 'pin': sdk_pin}
    if rel:
        sdk['at_release'] = remote.ask('freeink-sdk at the release', 'gitlink', CROSSPOINT, rel['tag'], 'freeink-sdk')
        if sdk_pin and sdk.get('at_release'):
            sdk['moved'] = sdk['at_release'] != sdk_pin
            if sdk['moved']:
                sdk['release_vs_pin'] = remote.ask('freeink-sdk release vs pin', 'compare', FREEINK,
                                                   sdk_pin, sdk['at_release'])
    cp['freeink_sdk'] = sdk
    plat = {'pin': platform_from_ini(ini_pin)}
    if rel:
        plat['at_release'] = platform_from_ini(remote.ask('platformio.ini at the release', 'file',
                                                          CROSSPOINT, rel['tag'], 'platformio.ini'))
        cp['ci_pio_core_at_release'] = pio_core_from_workflow(
            remote.ask("CrossPoint's CI at the release", 'file', CROSSPOINT, rel['tag'], '.github/workflows/ci.yml'))
    cp['platformio_platform'] = plat
    rep['crosspoint'] = cp

    # Espressif QEMU
    q = {'repo': QEMU_REPO, 'branch': QEMU_BRANCH, 'pin': pins['qemu_ref']}
    q['head'] = remote.ask(f'QEMU {QEMU_BRANCH}', 'head', QEMU_REPO, QEMU_BRANCH)
    if q['head'] and q['pin']:
        q['moved'] = not q['head'].startswith(q['pin']) and not q['pin'].startswith(q['head'])
        if q['moved']:
            q['head_vs_pin'] = remote.ask(f'QEMU {QEMU_BRANCH} vs QEMU_REF', 'compare', QEMU_REPO,
                                          q['pin'], q['head'], depth=QEMU_DEPTH)
    rep['qemu'] = q

    # PlatformIO (pioarduino)
    pr = remote.ask('pioarduino platform latest release', 'latest_release', PIO_PLATFORM,
                    like=(plat.get('pin') or {}).get('version'))
    cr = remote.ask('pioarduino core latest release', 'latest_release', PIO_CORE)
    rep['platformio'] = {
        'platform': {'repo': PIO_PLATFORM, 'pin': (plat.get('pin') or {}).get('version'),
                     'latest': pr['tag'] if pr else None, 'published': pr.get('published') if pr else None},
        'core': {'repo': PIO_CORE, 'pin': pins['pio_core'], 'crosspoint_ci': cp.get('ci_pio_core_at_release'),
                 'latest': cr['tag'] if cr else None},
    }

    rep['local'] = local_state(root, pins)
    rep['manual'] = [MANUAL_STOCK]
    rep['errors'] = list(remote.errors)
    rep['notes'] = list(remote.notes)
    rep['findings'] = findings(rep)
    return rep


def _s(sha, n=8):
    return sha[:n] if sha else '?'


def findings(rep):
    """The report's conclusions, each {'level': drift|info|action|ok, 'area', 'text', 'action'}."""
    out = []

    def add(level, area, text, action=None):
        out.append({'level': level, 'area': area, 'text': text, 'action': action})

    cp = rep.get('crosspoint') or {}
    rel, cmp_ = cp.get('latest_release'), cp.get('release_vs_pin')
    if rel and cmp_:
        n = cmp_.get('ahead_by')
        when = f' ({rel["published"]})' if rel.get('published') else ''
        pin = f'{_s(cp.get("pin"))}' + (f' = {cp["pin_describe"]}' if cp.get('pin_describe') else '')
        if n:
            add('drift', 'crosspoint',
                f'CrossPoint {rel["tag"]}{when} is {n} commits past the pin {pin}.',
                'Read the crosspoint-latest job: golden drift there is upstream\'s own UI, a failure is the '
                'emulator. Moving the pin needs new device captures (docs/upstream.md).')
        elif cmp_.get('status') in ('identical', 'behind'):
            add('ok', 'crosspoint', f'The pin {pin} contains the latest release {rel["tag"]}.')
    br, bcmp = cp.get('default_branch'), cp.get('branch_vs_pin')
    if br and bcmp and bcmp.get('ahead_by'):
        add('info', 'crosspoint', f'CrossPoint {br["name"]} is {bcmp["ahead_by"]} commits past the pin (unreleased work).')
    sdk = cp.get('freeink_sdk') or {}
    if sdk.get('moved'):
        n = (sdk.get('release_vs_pin') or {}).get('ahead_by')
        add('info', 'crosspoint',
            f'freeink-sdk moves with that release: {_s(sdk.get("pin"))} -> {_s(sdk.get("at_release"))}'
            + (f' ({n} commits).' if n is not None else '.'))
    plat = cp.get('platformio_platform') or {}
    p0, p1 = (plat.get('pin') or {}).get('version'), (plat.get('at_release') or {}).get('version')
    if p0 and p1 and p0 != p1:
        add('info', 'platformio', f'The release builds with platform-espressif32 {p1} (the pin: {p0}).')
    q = rep.get('qemu') or {}
    if q.get('moved'):
        n = (q.get('head_vs_pin') or {}).get('ahead_by')
        add('drift', 'qemu',
            f'Espressif QEMU {q["branch"]} moved: {_s(q.get("pin"))} -> {_s(q.get("head"))}'
            + (f' ({n} commits).' if n is not None else '.'),
            'Read the qemu-head job: whether qemu-patches/ apply to the new head, build and boot. '
            'Rebase the series and bump QEMU_REF in one commit (docs/upstream.md).')
    elif q.get('moved') is False:
        add('ok', 'qemu', f'Espressif QEMU {q["branch"]} is still at QEMU_REF {_s(q.get("pin"))}.')
    pio = rep.get('platformio') or {}
    pl = pio.get('platform') or {}
    if pl.get('latest') and pl.get('pin') and pl['latest'] != pl['pin']:
        add('info', 'platformio',
            f'pioarduino platform-espressif32 {pl["latest"]} is out; CrossPoint pins {pl["pin"]} '
            '(it moves when CrossPoint moves it).')
    core = pio.get('core') or {}
    if core.get('crosspoint_ci') and core.get('pin') and core['crosspoint_ci'] != core['pin']:
        add('action', 'platformio',
            f'CrossPoint\'s CI at the release installs pioarduino core {core["crosspoint_ci"]}; the Makefile pins {core["pin"]}.',
            'Move the Makefile\'s PIO_CORE_VERSION when the firmware pin moves (CI reads it there).')
    elif core.get('latest') and core.get('pin') and core['latest'] != core['pin']:
        add('info', 'platformio',
            f'pioarduino core {core["latest"]} is out; the Makefile pins {core["pin"]}, as CrossPoint\'s own CI does.')
    loc = rep.get('local') or {}
    if loc.get('qemu_build_stale_kegs'):
        add('action', 'local',
            f'qemu/build was configured against Homebrew kegs that are gone: {", ".join(loc["qemu_build_stale_kegs"][:4])}'
            + (' ...' if len(loc['qemu_build_stale_kegs']) > 4 else ''),
            'rm -rf qemu/build && make qemu (a fresh configure; the Makefile line has --disable-gnutls).')
    for v in loc.get('broken_venvs') or []:
        add('action', 'local', f'{v["venv"]}: its base interpreter {v["missing_base"]} is gone.',
            f'Recreate it: {v.get("fix") or "delete it and rebuild it"}.')
    fc = loc.get('firmware_checkout')
    if fc and fc.get('head') and not fc.get('at_pin'):
        add('info', 'local', f'firmware/ is checked out at {_s(fc["head"])}, not the pin '
            f'{_s((rep.get("pins") or {}).get("crosspoint"))}.',
            'git submodule update --init --recursive firmware restores the pin.')
    return out


# --- output -----------------------------------------------------------------------------------------

LEVEL_MARK = {'drift': 'DRIFT', 'action': 'ACTION', 'info': 'info', 'ok': 'ok'}


def render_text(rep):
    lines = [f'upstream check {rep.get("checked_at", "")} (via {rep.get("backend")})']
    cp, q, pio = rep.get('crosspoint') or {}, rep.get('qemu') or {}, rep.get('platformio') or {}
    rel = cp.get('latest_release') or {}
    lines.append(f'  CrossPoint  pin {_s(cp.get("pin"))} {cp.get("pin_describe") or ""}'.rstrip()
                 + f'  latest release {rel.get("tag", "?")}  '
                 + f'+{(cp.get("release_vs_pin") or {}).get("ahead_by", "?")} commits')
    sdk = cp.get('freeink_sdk') or {}
    lines.append(f'  freeink-sdk pin {_s(sdk.get("pin"))}  at release {_s(sdk.get("at_release"))}')
    lines.append(f'  QEMU        QEMU_REF {_s(q.get("pin"))}  {q.get("branch", "")} {_s(q.get("head"))}')
    pl, co = pio.get('platform') or {}, pio.get('core') or {}
    lines.append(f'  PlatformIO  platform {pl.get("pin")} (latest {pl.get("latest")})  '
                 f'core {co.get("pin")} (CrossPoint CI {co.get("crosspoint_ci")}, latest {co.get("latest")})')
    lines.append('')
    for f in rep.get('findings') or []:
        lines.append(f'{LEVEL_MARK.get(f["level"], f["level"]):>6}  {f["text"]}')
        if f.get('action'):
            lines.append(f'        -> {f["action"]}')
    for m in rep.get('manual') or []:
        lines.append(f'manual  {m}')
    for e in rep.get('errors') or []:
        lines.append(f' ERROR  {e}')
    return '\n'.join(lines)


def _md_link(repo, ref, text=None):
    """A commit SHA -> [abcdef12](.../commit/SHA); a tag or branch -> [name](.../tree/name)."""
    if not ref:
        return '?'
    if re.fullmatch(r'[0-9a-f]{40}', ref):
        return f'[{text or _s(ref)}](https://github.com/{repo}/commit/{ref})'
    return f'[{text or ref}](https://github.com/{repo}/tree/{ref})'


def render_markdown(rep):
    cp, q, pio = rep.get('crosspoint') or {}, rep.get('qemu') or {}, rep.get('platformio') or {}
    rel = cp.get('latest_release') or {}
    sdk = cp.get('freeink_sdk') or {}
    pl, co = pio.get('platform') or {}, pio.get('core') or {}
    rvp = cp.get('release_vs_pin') or {}
    qvp = q.get('head_vs_pin') or {}

    def n(x):
        return '?' if x is None else str(x)

    lines = [f'## Upstream watch — {rep.get("checked_at", "")[:10]}', '',
             '| Component | Pinned here | Upstream | Moved |', '|---|---|---|---|']
    lines.append(f'| CrossPoint Reader | {_md_link(CROSSPOINT, cp.get("pin"))} {cp.get("pin_describe") or ""} '
                 f'| release {_md_link(CROSSPOINT, rel.get("tag"), rel.get("tag"))} {rel.get("published") or ""} '
                 f'| {n(rvp.get("ahead_by"))} commits |')
    lines.append(f'| freeink-sdk | {_md_link(FREEINK, sdk.get("pin"))} | at the release {_md_link(FREEINK, sdk.get("at_release"))} '
                 f'| {n((sdk.get("release_vs_pin") or {}).get("ahead_by", 0 if sdk.get("moved") is False else None))} commits |')
    lines.append(f'| Espressif QEMU `{q.get("branch")}` | {_md_link(QEMU_REPO, q.get("pin"))} | {_md_link(QEMU_REPO, q.get("head"))} '
                 f'| {"no" if q.get("moved") is False else n(qvp.get("ahead_by")) + " commits"} |')
    lines.append(f'| platform-espressif32 | {pl.get("pin") or "?"} | latest {pl.get("latest") or "?"} | '
                 f'{"no" if pl.get("pin") == pl.get("latest") else "yes"} |')
    lines.append(f'| pioarduino core (Makefile) | {co.get("pin") or "?"} | CrossPoint CI {co.get("crosspoint_ci") or "?"}, '
                 f'latest {co.get("latest") or "?"} | {"no" if co.get("pin") == co.get("latest") else "yes"} |')
    lines.append('')
    icon = {'drift': '🟠 drift', 'action': '🔴 action', 'info': 'ℹ️ info', 'ok': '🟢 ok'}
    if rep.get('findings'):
        lines.append('### Findings')
        for f in rep['findings']:
            lines.append(f'- **{icon.get(f["level"], f["level"])}** {f["text"]}'
                         + (f' — *{f["action"]}*' if f.get('action') else ''))
        lines.append('')
    lines.append('### Stays manual')
    for m in rep.get('manual') or []:
        lines.append(f'- {m}')
    if rep.get('errors'):
        lines += ['', '### Could not be read']
        lines += [f'- {e}' for e in rep['errors']]
    if rep.get('notes'):
        lines += ['', '<details><summary>Notes</summary>', '']
        lines += [f'- {x}' for x in rep['notes']]
        lines += ['', '</details>']
    lines.append('')
    lines.append(f'<sub>tools/upstream_check.py via {rep.get("backend")} · docs/upstream.md says how to act on each line</sub>')
    return '\n'.join(lines) + '\n'


def github_outputs(rep):
    cp, q = rep.get('crosspoint') or {}, rep.get('qemu') or {}
    rel = cp.get('latest_release') or {}
    ahead = (cp.get('release_vs_pin') or {}).get('ahead_by')
    return {
        'crosspoint_tag': rel.get('tag') or '',
        'crosspoint_behind': 'true' if ahead else 'false',
        'qemu_head': q.get('head') or '',
        'qemu_moved': 'true' if q.get('moved') else 'false',
    }


# --- tests-summary ----------------------------------------------------------------------------------

def crash_markers(console_path):
    """['<marker>: <line>', ...] for every crash marker in one console log (missing file -> [])."""
    text = _read(console_path)
    if not text:
        return []
    hits = []
    for line in text.splitlines():
        for m in CRASH_MARKERS:
            if m in line:
                hits.append(line.strip()[:160])
                break
    return hits


def parse_junit(path):
    """JUnit XML -> {nodeid-ish: {'outcome': passed|failed|error|skipped|xfailed, 'message'}}."""
    out = {}
    root = ET.parse(path).getroot()
    for tc in root.iter('testcase'):
        name = f'{tc.get("classname", "")}::{tc.get("name", "")}'
        outcome, msg = 'passed', ''
        for child in tc:
            if child.tag in ('failure', 'error'):
                outcome, msg = ('failed' if child.tag == 'failure' else 'error'), child.get('message') or (child.text or '')
                break
            if child.tag == 'skipped':
                xf = child.get('type') == 'pytest.xfail'
                outcome, msg = ('xfailed' if xf else 'skipped'), child.get('message') or ''
        out[name] = {'outcome': outcome, 'message': msg.strip()}
    return out


def read_drift(path):
    rows = []
    for line in (_read(path) or '').splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def summarize_tests(junits, drift_path=None, logs=None, prefix='', title='Tests', notes=()):
    """(markdown, exit status) for one job's pytest run(s): later JUnit files are retries and win."""
    results, retried = {}, set()
    present = [j for j in junits if j and os.path.exists(j)]
    for j in present:
        for k, v in parse_junit(j).items():
            if k in results:
                retried.add(k)
            results[k] = v
    count = {}
    for v in results.values():
        count[v['outcome']] = count.get(v['outcome'], 0) + 1
    rows = read_drift(drift_path) if drift_path else []
    drift = [r for r in rows if r.get('kind') == 'golden-drift']
    unhealthy = [r for r in rows if r.get('kind') == 'unhealthy']
    crashes = []
    if logs:
        for c in sorted(glob.glob(os.path.join(logs, '*', 'console.log'))):
            inst = os.path.basename(os.path.dirname(c))
            if inst.startswith(prefix):
                crashes += [(inst, h) for h in crash_markers(c)]
    bad = [(k, v) for k, v in results.items() if v['outcome'] in ('failed', 'error')]
    flaky = [k for k in retried if results[k]['outcome'] == 'passed']

    lines = [f'### {title}', '']
    lines += [f'- {n}' for n in notes]
    if not present:
        lines.append('- **No test results**: the build or the test step did not run (see the log above).')
    else:
        order = ('passed', 'failed', 'error', 'xfailed', 'skipped')
        lines.append('- pytest: ' + ', '.join(f'{count[o]} {"golden drift (xfail)" if o == "xfailed" else o}'
                                              for o in order if count.get(o)))
    if flaky:
        lines.append(f'- passed only on the retry (inputs are fixed at the root since 2026-10-13, '
                     f'docs/x4emu.md "Input delivery": worth a look): {", ".join(sorted(flaky))}')
    if drift:
        lines += ['', '#### Golden drift — review, not a failure', '',
                  'The emulator ran healthy (no crash marker, no unknown panel command); only a comparison '
                  'against a device-made golden differs. Screenshots are in the job\'s artifact.', '',
                  '| Test | What differed |', '|---|---|']
        for r in drift:
            lines.append(f'| `{r.get("test")}` | {(r.get("message") or "").splitlines()[0][:200] if r.get("message") else ""} |')
    if bad:
        lines += ['', '#### Failures — the emulator, or a test that no longer fits this firmware', '']
        for k, v in sorted(bad):
            first = next((x for x in v['message'].splitlines() if x.strip()), '')[:240]
            lines.append(f'- `{k}` ({v["outcome"]}): {first}')
    if unhealthy:
        lines += ['', '#### Passing tests whose emulator was unhealthy', '']
        lines += [f'- `{r.get("test")}`: {r.get("message")}' for r in unhealthy]
    if crashes:
        lines += ['', '#### Crash markers in emulator consoles', '']
        lines += [f'- `{i}`: `{h}`' for i, h in crashes[:20]]
    failed = bool(bad or crashes or (not present))
    verdict = ('**Verdict: emulator-level failure** (see above).' if bad or crashes else
               '**Verdict: no test results.**' if not present else
               f'**Verdict: emulator OK**' + (f'; {len(drift)} golden(s) drifted.' if drift else '.'))
    lines += ['', verdict, '']
    return '\n'.join(lines), 1 if failed else 0


# --- main -------------------------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ('report', 'tests-summary', '-h', '--help'):
        argv.insert(0, 'report')
    ap = argparse.ArgumentParser(prog='upstream_check.py', description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    r = sub.add_parser('report', help='what moved upstream (default)')
    fmt = r.add_mutually_exclusive_group()
    fmt.add_argument('--json', action='store_true', help='print the report as one JSON object')
    fmt.add_argument('--markdown', action='store_true', help='print Markdown (for $GITHUB_STEP_SUMMARY)')
    r.add_argument('--save', metavar='FILE', help='also write the JSON report to FILE')
    r.add_argument('--load', metavar='FILE', help='render a saved JSON report instead of asking upstream')
    r.add_argument('--github-output', metavar='FILE', help='append crosspoint_tag/_behind, qemu_head/_moved as key=value')
    r.add_argument('--backend', choices=('auto', 'gh', 'git'), default='auto')
    t = sub.add_parser('tests-summary', help='Markdown summary of a pytest run (JUnit XML)')
    t.add_argument('--junit', action='append', default=[], metavar='FILE', help='repeatable; later files are retries')
    t.add_argument('--drift', metavar='FILE', help="tests/conftest.py's --golden-drift file")
    t.add_argument('--logs', metavar='DIR', help='scan DIR/*/console.log for crash markers (.x4emu)')
    t.add_argument('--instance-prefix', default='', metavar='P', help='only instances whose name starts with P')
    t.add_argument('--title', default='Tests')
    t.add_argument('--note', action='append', default=[], metavar='TEXT', help='a bullet line above the counts')
    a = ap.parse_args(argv)

    if a.cmd == 'tests-summary':
        md, rc = summarize_tests(a.junit, a.drift, a.logs, a.instance_prefix, a.title, a.note)
        sys.stdout.write(md)
        return rc

    if a.load:
        with open(a.load) as f:
            rep = json.load(f)
    else:
        remote = Remote(a.backend)
        try:
            rep = gather(remote)
        finally:
            remote.close()
    if a.save:
        with open(a.save, 'w') as f:
            json.dump(rep, f, indent=2)
            f.write('\n')
    if a.github_output:
        with open(a.github_output, 'a') as f:
            for k, v in github_outputs(rep).items():
                f.write(f'{k}={v}\n')
    if a.json:
        print(json.dumps(rep, indent=2))
    elif a.markdown:
        sys.stdout.write(render_markdown(rep))
    else:
        print(render_text(rep))
    return 2 if rep.get('errors') else 0


if __name__ == '__main__':
    sys.exit(main())
