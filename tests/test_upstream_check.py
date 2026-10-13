"""tools/upstream_check.py without the network: parsing, findings, rendering, the tests summary, and
the --golden-drift oracle table in conftest.py (docs/upstream.md). The 2026-10-12 manual check is the
fixture: CrossPoint 1.6.5 93 commits past the pin, freeink-sdk 108, QEMU unmoved."""
import json, os, re, subprocess
import pytest
from conftest import ROOT, GOLDEN_ORACLES
import upstream_check as uc                      # tools/, which conftest puts on sys.path

PIN = '7db14a015ca4b1d74d3cbed131a748f3029a4517'
REL = '93e98bb78702e29868a16a13b80c40e6b36ccdff'
SDK_PIN = 'cb9167d541c0f6e9d57cf8eae1f564a939883ecc'
SDK_REL = '111fdcc7f0176c3ee38391a160ee296bf492dbd8'
QREF = 'febae182e132e4055529be423a818225ebddaa3a'
INI = 'platform = https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip\n'
CI = '  uv pip install --system -U https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.19.zip\n'


def test_versions_and_tags():
    assert uc.parse_version('1.6.5') == (1, 6, 5, 0)
    assert uc.parse_version('v1.5.0') == (1, 5, 0, 0)
    assert uc.parse_version('55.03.312-1') == (55, 3, 312, 1)
    for pre in ('1.6.5rc', 'v1.5.0-rc-4', '1.6.0rc', 'stable', 'c2-skeleton'):
        assert uc.parse_version(pre) is None, pre
    # CrossPoint's real tag list (git ls-remote, 2026-10-12), annotated tags peeled
    ls = ('9e04eec072b1db251cca6dc570b619c3e39b5c36\trefs/tags/1.0.0\n'
          'e4c06c25e58966e579178a4855ca0bafbc7742bb\trefs/tags/1.3.0\n'
          '8959836e462d4deff7fddb92b82a9b10c34ccddf\trefs/tags/1.3.0^{}\n'
          '54337e6d73fc628f4ba523ddc89a743ca8c6e4c5\trefs/tags/1.6.0\n'
          f'{REL}\trefs/tags/1.6.5\n'
          'a1ceb6333f907856e4dcde21bd0a94f5be84452b\trefs/tags/1.6.5rc\n'
          'e00f5958dfeea2a3e640c39eb78186fd20996f4b\trefs/tags/v1.5.0\n'
          'af9d2620970a6e75036b84a4b49c189475a6108b\trefs/tags/v1.5.0-rc-4\n')
    refs = uc.parse_ls_remote(ls)
    assert refs['refs/tags/1.3.0'] == '8959836e462d4deff7fddb92b82a9b10c34ccddf'    # the commit, not the tag object
    tags = [r[len('refs/tags/'):] for r in refs]
    assert uc.latest_stable_tag(tags) == '1.6.5'
    # pioarduino's platform: the old 2024.07.00 scheme must not beat 55.x
    assert uc.latest_stable_tag(['2024.07.00', '55.03.311', '55.03.312-1', '55.03.312'], like='55.03.311') == '55.03.312-1'


def test_pins_from_files():
    assert uc.platform_from_ini('[platformio]\n' + INI) == {
        'spec': INI.split('= ')[1].strip(), 'version': '55.03.311'}
    assert uc.platform_from_ini('platform = espressif32@6.9.0\n')['version'] == '6.9.0'
    assert uc.platform_from_ini('no platform here') is None
    assert uc.pio_core_from_workflow(CI) == 'v6.1.19'
    assert uc.pio_core_from_workflow('env:\n      PIO_CORE_VERSION: v6.2.0\n') == 'v6.2.0'
    assert uc.pio_core_from_workflow('PIO_CORE_VERSION ?= v6.1.19\nPIO_CORE ?= x/$(PIO_CORE_VERSION).zip\n') == 'v6.1.19'
    pins = uc.local_pins(ROOT)            # this checkout: the Makefile and the gitlink
    assert re.fullmatch(r'[0-9a-f]{40}', pins['qemu_ref'])
    assert re.fullmatch(r'[0-9a-f]{40}', pins['crosspoint'])
    assert pins['pio_core'] and pins['pio_core'].startswith('v')


def test_the_makefile_is_the_only_pin_file():
    """QEMU_REF and the PlatformIO core are pinned once, in the Makefile: the workflows read both
    through .github/scripts/ci.sh, which must agree with what this report reads, and carry no
    version of their own that could drift from it."""
    out = subprocess.run(['bash', os.path.join(ROOT, '.github', 'scripts', 'ci.sh'), 'pins'], cwd=ROOT,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = dict(line.split('=', 1) for line in out.stdout.split())
    pins = uc.local_pins(ROOT)
    assert got == {'qemu_ref': pins['qemu_ref'], 'pio_core': pins['pio_core']}
    for wf in ('ci.yml', 'upstream.yml'):
        text = open(os.path.join(ROOT, '.github', 'workflows', wf)).read()
        assert pins['pio_core'] not in text and pins['qemu_ref'][:8] not in text, wf
        assert not re.search(r'^\s*(PIO_CORE|PIO_CORE_VERSION|QEMU_REF):', text, re.M), wf   # no env pin


def test_local_drift_checks():
    gone = '/opt/homebrew/Cellar/libgcrypt/0.0.0-x4pro-test-gone'
    text = f'build x: CC\n  FLAGS = -I{gone}/include -I/usr/include -L{gone}/lib\n'
    assert uc.stale_cellar_paths(text) == [gone]
    assert uc.stale_cellar_paths('-I/usr/include -I/opt/homebrew/include') == []
    assert uc.venv_base('home = /opt/homebrew/opt/python@3.14/bin\nversion = 3.14.7\n') == '/opt/homebrew/opt/python@3.14/bin'


def test_a_venv_with_a_vanished_base_is_reported_with_its_own_fix(tmp_path):
    """.venv-pio (the Makefile's PlatformIO venv) goes stale exactly as .venv does when Homebrew
    replaces the Python it was made from; each broken venv names the command that rebuilds it."""
    gone = str(tmp_path / 'Cellar' / 'python@3.14' / 'gone' / 'bin')
    for venv in ('.venv', '.venv-pio'):
        (tmp_path / venv).mkdir()
        (tmp_path / venv / 'pyvenv.cfg').write_text(f'home = {gone}\nversion = 3.14.0\n')
    st = uc.local_state(str(tmp_path), pins={'crosspoint': None})
    broken = {os.path.basename(v['venv']): v for v in st['broken_venvs']}
    assert broken['.venv']['missing_base'] == gone and 'make setup' in broken['.venv']['fix']
    assert broken['.venv-pio']['missing_base'] == gone and 'make firmware' in broken['.venv-pio']['fix']
    texts = [f['action'] for f in uc.findings({'local': st}) if f['area'] == 'local']
    assert any('rm -rf .venv-pio' in t for t in texts) and any('rm -rf .venv &&' in t for t in texts)


class FakeBackend:
    """Answers as GitHub did on 2026-10-12."""
    name = 'fake'

    def __init__(self, qemu_head=QREF, fail=()):
        self.qemu_head, self.fail, self.calls = qemu_head, set(fail), []

    def close(self):
        pass

    def _f(self, what):
        self.calls.append(what)
        if what in self.fail:
            raise uc.NetError(f'{what}: offline')

    def latest_release(self, repo, like=None):
        self._f('latest_release:' + repo)
        return {uc.CROSSPOINT: {'tag': '1.6.5', 'published': '2026-09-27', 'sha': REL},
                uc.PIO_PLATFORM: {'tag': '55.03.312-1', 'published': '2026-09-22', 'sha': 'a' * 40},
                uc.PIO_CORE: {'tag': 'v6.2.0', 'published': '2026-09-15', 'sha': 'b' * 40}}[repo]

    def default_branch(self, repo):
        self._f('default_branch')
        return 'develop'

    def head(self, repo, branch):
        self._f('head:' + repo)
        return self.qemu_head if repo == uc.QEMU_REPO else 'ba39565ce7b3a8aab14ca86da4fa4c3ca140f007'

    def compare(self, repo, base, head, depth=None):
        self._f('compare:' + repo)
        n = {uc.CROSSPOINT: 93 if head == '1.6.5' else 156, uc.FREEINK: 108, uc.QEMU_REPO: 12}[repo]
        return {'status': 'ahead', 'ahead_by': n, 'behind_by': 0}

    def gitlink(self, repo, ref, path):
        self._f('gitlink')
        return SDK_REL if ref == '1.6.5' else SDK_PIN

    def file(self, repo, ref, path):
        self._f('file:' + path)
        return CI if path.endswith('ci.yml') else INI


def gathered(monkeypatch, backend, gh=None):
    monkeypatch.setattr(uc, 'local_pins', lambda root=None: {'qemu_ref': QREF, 'crosspoint': PIN, 'pio_core': 'v6.1.19'})
    monkeypatch.setattr(uc, '_firmware_has', lambda sha, root=None: False)     # ask the remote, not firmware/
    monkeypatch.setattr(uc, 'local_state', lambda root=None, pins=None: {
        'qemu_build_stale_kegs': ['/opt/homebrew/Cellar/libgcrypt/1.11.0'], 'broken_venvs': [], 'firmware_checkout': None})
    remote = uc.Remote('git')
    remote.git, remote.gh = backend, gh
    return uc.gather(remote)


def test_report_of_the_2026_10_12_state(monkeypatch, tmp_path, capsys):
    rep = gathered(monkeypatch, FakeBackend())
    assert rep['errors'] == []
    cp = rep['crosspoint']
    assert cp['latest_release']['tag'] == '1.6.5' and cp['release_vs_pin']['ahead_by'] == 93
    assert cp['freeink_sdk'] == {'repo': uc.FREEINK, 'pin': SDK_PIN, 'at_release': SDK_REL, 'moved': True,
                                 'release_vs_pin': {'status': 'ahead', 'ahead_by': 108, 'behind_by': 0}}
    assert rep['qemu']['moved'] is False and 'head_vs_pin' not in rep['qemu']
    assert rep['platformio']['core'] == {'repo': uc.PIO_CORE, 'pin': 'v6.1.19', 'crosspoint_ci': 'v6.1.19', 'latest': 'v6.2.0'}
    levels = [(f['level'], f['area']) for f in rep['findings']]
    assert ('drift', 'crosspoint') in levels and ('ok', 'qemu') in levels and ('action', 'local') in levels
    assert not any(f['area'] == 'qemu' and f['level'] == 'drift' for f in rep['findings'])
    assert uc.github_outputs(rep) == {'crosspoint_tag': '1.6.5', 'crosspoint_behind': 'true',
                                      'qemu_head': QREF, 'qemu_moved': 'false'}
    md = uc.render_markdown(rep)
    assert '| CrossPoint Reader |' in md and '93 commits' in md and '108 commits' in md
    assert 'Stays manual' in md and 'encrypted' in md and 'rm -rf qemu/build' in md
    txt = uc.render_text(rep)
    assert 'DRIFT' in txt and 'manual' in txt and 'ERROR' not in txt
    # --save / --load round trip through main(), exit 0 with no errors; --github-output appends
    saved = tmp_path / 'r.json'
    saved.write_text(json.dumps(rep))
    out = tmp_path / 'gh_output'
    out.write_text('earlier=1\n')
    assert uc.main(['--load', str(saved), '--json', '--github-output', str(out)]) == 0
    assert json.loads(capsys.readouterr().out)['crosspoint']['pin'] == PIN
    assert out.read_text().splitlines() == ['earlier=1', 'crosspoint_tag=1.6.5', 'crosspoint_behind=true',
                                            f'qemu_head={QREF}', 'qemu_moved=false']


def test_qemu_moved_and_network_failures(monkeypatch, tmp_path, capsys):
    head = '0123456789abcdef0123456789abcdef01234567'
    rep = gathered(monkeypatch, FakeBackend(qemu_head=head, fail={'latest_release:' + uc.PIO_CORE}))
    assert rep['qemu']['moved'] is True and rep['qemu']['head_vs_pin']['ahead_by'] == 12
    assert any(f['area'] == 'qemu' and f['level'] == 'drift' and '12 commits' in f['text'] for f in rep['findings'])
    assert uc.github_outputs(rep)['qemu_moved'] == 'true'
    assert len(rep['errors']) == 1 and 'pioarduino core' in rep['errors'][0]
    saved = tmp_path / 'r.json'
    saved.write_text(json.dumps(rep))
    assert uc.main(['--load', str(saved), '--markdown']) == 2      # a remote could not be read
    assert 'Could not be read' in capsys.readouterr().out


def test_gh_failure_falls_back_to_git(monkeypatch):
    gh = FakeBackend(fail={'latest_release:' + uc.CROSSPOINT})
    git = FakeBackend()
    rep = gathered(monkeypatch, git, gh=gh)
    assert rep['errors'] == [] and rep['crosspoint']['latest_release']['tag'] == '1.6.5'
    assert any('gh failed' in n for n in rep['notes'])
    assert git.calls == ['latest_release:' + uc.CROSSPOINT]           # only the failed question went to git


JUNIT = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.test_boot" name="test_boot_to_home_and_press_right" time="20"/>
<testcase classname="tests.test_device_screenshot" name="test_home_screen_matches_device_screenshot" time="30">
  <skipped type="pytest.xfail" message="golden drift: 0.505% of pixels differ (1939)"/></testcase>
<testcase classname="tests.test_replay" name="test_record_replay_same_screen" time="60">
  <failure message="AssertionError: x4emu ('tap',) failed (1): no refresh within 30s">trace</failure></testcase>
<testcase classname="tests.test_stock" name="test_stock_home" time="0">
  <skipped type="pytest.skip" message="device dump not available"/></testcase>
<testcase classname="tests.test_touch_reader" name="test_battery_property" time="5"/>
</testsuite></testsuites>'''


def test_tests_summary(tmp_path):
    j1 = tmp_path / 'junit-1.xml'
    j1.write_text(JUNIT)
    drift = tmp_path / 'drift.jsonl'
    drift.write_text(json.dumps({'kind': 'golden-drift', 'test': 'tests/test_device_screenshot.py::'
                                 'test_home_screen_matches_device_screenshot',
                                 'message': '0.505% of pixels differ (1939)'}) + '\n')
    logs = tmp_path / 'x4emu'
    for inst, text in (('pytest-up-ok', 'Entering activity: Home\n'), ('other-crashed', 'Guru Meditation Error: x\n')):
        (logs / inst).mkdir(parents=True)
        (logs / inst / 'console.log').write_text(text)
    md, rc = uc.summarize_tests([str(j1)], str(drift), str(logs), 'pytest-up-', 'CrossPoint 1.6.5', ['firmware 93e98bb7'])
    assert rc == 1                                           # the replay failure is not drift
    assert '2 passed, 1 failed, 1 golden drift (xfail), 1 skipped' in md
    assert '0.505% of pixels differ' in md and 'test_record_replay_same_screen' in md
    assert 'emulator-level failure' in md and 'Guru' not in md           # other-crashed is outside the prefix
    # the retry passes: the failure becomes "passed only on the retry", the verdict green
    j2 = tmp_path / 'junit-2.xml'
    j2.write_text('<testsuites><testsuite><testcase classname="tests.test_replay" '
                  'name="test_record_replay_same_screen"/></testsuite></testsuites>')
    md, rc = uc.summarize_tests([str(j1), str(j2)], str(drift), str(logs), 'pytest-up-', 'CrossPoint 1.6.5')
    assert rc == 0 and 'passed only on the retry' in md and '1 golden(s) drifted' in md
    # a crash marker in a console in scope fails the summary even when every test passed
    md, rc = uc.summarize_tests([str(j2)], None, str(logs), '', 'x')
    assert rc == 1 and 'Guru Meditation Error' in md
    # no JUnit at all (the build failed): said, and not green
    md, rc = uc.summarize_tests([str(tmp_path / 'missing.xml')], None, None, '', 'x')
    assert rc == 1 and 'No test results' in md


def test_crash_markers_are_the_idf_panic_lines(tmp_path):
    log = tmp_path / 'console.log'
    log.write_text('rst:0x5 (DSLEEP)\nEntering activity: Home\n'
                   'assert failed: 0x40384078 <cached disabled>:141 (<cached disabled>)\n'
                   'Backtrace: 0x40383b55:0x3fca0310 0x40383b21:0x3fca0330\n')
    assert [h.split(':')[0] for h in uc.crash_markers(str(log))] == ['assert failed', 'Backtrace']
    assert uc.crash_markers(str(tmp_path / 'none.log')) == []


def test_golden_oracles_name_real_tests_and_match_their_messages():
    """--golden-drift (conftest.py) keys on test names and assert messages: a rename must fail here,
    not silently turn a golden mismatch into a failure (or the other way round) in the upstream job."""
    sources = ''.join(open(os.path.join(ROOT, 'tests', f), errors='replace').read()
                      for f in os.listdir(os.path.join(ROOT, 'tests')) if f.startswith('test_') and f.endswith('.py'))
    for name in GOLDEN_ORACLES:
        assert re.search(rf'^def {name}\(', sources, re.M), f'{name} is not a test any more'
    home = GOLDEN_ORACLES['test_home_screen_matches_device_screenshot']
    assert re.search(home, 'screenshot home-63.png\n0.505% of pixels differ (1939)\nassert 0.505 < 0.2')
    assert not re.search(home, "x4emu ('wait-text', 'Entering activity: Home') failed (1)")
    reader = GOLDEN_ORACLES['test_reader_pages_match_device_screenshots']
    assert re.search(reader, 'page 1 differs from the device screenshot in 12 pixels outside the clock\nassert 12 == 0')
    assert re.search(reader, "assert 9000 > 10000\n +  where 9000 = base_frame_diff(PosixPath('p2.png'), 'x.bmp')")
    assert not re.search(reader, 'the book-row tap drew nothing twice')
    # ...and the texts those patterns match are still the ones the code prints
    cmds = open(os.path.join(ROOT, 'x4emu', 'commands.py'), errors='replace').read()
    assert "% of pixels differ (" in cmds, 'x4emu screenshot --diff changed its output; update GOLDEN_ORACLES'
    oracle = open(os.path.join(ROOT, 'tests', 'test_device_screenshot.py'), errors='replace').read()
    assert 'differs from the device screenshot' in oracle and 'base_frame_diff(' in oracle
