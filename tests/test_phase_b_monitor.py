"""Disposable-process monitor regression; all attempted writes are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhaseBMonitorTests(unittest.TestCase):
    def child(self, body):
        with tempfile.TemporaryDirectory(prefix="015-monitor-regression-") as temp:
            prefix = """
import json, os, socket, sqlite3, subprocess, sys
from pathlib import Path
from tools.phase_b_monitor import PhaseBMonitor, PhaseBStop
root = Path(sys.argv[1])
repo = Path(sys.argv[2])
"""
            environment = dict(os.environ)
            result = subprocess.run([sys.executable, "-B", "-c",
                                     textwrap.dedent(prefix) + textwrap.dedent(body),
                                     temp, str(ROOT)], cwd=ROOT, env=environment,
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_git_devnull_and_standard_devices_no_false_positive(self):
        result = self.child("""
command = ('git', '-C', str(repo), 'rev-parse', 'HEAD')
monitor = PhaseBMonitor(allowed_git_command=command, scope='REGRESSION').install()
head = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True).strip()
for device in ('/dev/urandom', '/dev/random', '/dev/zero'):
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    os.read(fd, 1)
    os.close(fd)
for flags in (os.O_RDONLY, os.O_WRONLY, os.O_RDWR):
    fd = os.open('/dev/null', flags)
    os.close(fd)
print(json.dumps({'head_length': len(head), 'stop': monitor.stop_event}))
""")
        self.assertEqual(result, {"head_length": 40, "stop": None})

    def test_exact_allowlisted_creation_and_write(self):
        result = self.child("""
directory = root / 'allowed'
target = directory / 'synthetic.json'
monitor = PhaseBMonitor(allowed_directories=[directory], allowed_files=[target],
                        scope='REGRESSION').install()
directory.mkdir()
fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
os.close(fd)
target.write_text('{"synthetic": true}')
print(json.dumps({'value': json.loads(target.read_text()), 'stop': monitor.stop_event}))
""")
        self.assertEqual(result, {"value": {"synthetic": True}, "stop": None})

    def test_unauthorized_write_blocked_before_creation_and_latched(self):
        result = self.child("""
target = root / 'unauthorized-synthetic.txt'
recorded = []
monitor = PhaseBMonitor(scope='REGRESSION', event_sink=recorded.append).install()
try:
    target.write_text('synthetic')
except RuntimeError:
    pass  # Model the CLI's generic exception handler.
try:
    monitor.raise_if_stopped()
except PhaseBStop as error:
    original = str(error)
print(json.dumps({'exists': target.exists(), 'original': original,
                  'stop': monitor.stop_event, 'recorded': recorded}))
""")
        self.assertFalse(result["exists"])
        self.assertEqual(result["original"], "UNEXPECTED_WRITE")
        self.assertEqual(result["recorded"], [result["stop"]])
        self.assertTrue(result["stop"]["expected_negative_test"])
        self.assertEqual(result["stop"]["scope"], "REGRESSION")

    def test_device_write_or_creation_flags_are_not_blanket_allowed(self):
        # Emit the real audit event shape without invoking any device mutation.
        for expression in ("sys.audit('open', '/dev/null', None, os.O_WRONLY | os.O_CREAT)",
                           "sys.audit('open', '/dev/null', None, os.O_WRONLY | os.O_TRUNC)",
                           "sys.audit('open', '/dev/zero', None, os.O_WRONLY)",
                           "sys.audit('open', '/dev/urandom', None, os.O_RDWR)",
                           "sys.audit('open', '/dev/015-never-created', None, os.O_WRONLY | os.O_CREAT)"):
            with self.subTest(expression=expression):
                result = self.child(f"""
monitor = PhaseBMonitor(scope='REGRESSION').install()
try:
    {expression}
except PhaseBStop:
    pass
print(json.dumps(monitor.stop_event))
""")
                self.assertEqual(result["trigger"], "UNEXPECTED_WRITE")

    def test_allowlisted_name_does_not_allow_symlink_escape(self):
        result = self.child("""
directory = root / 'synthetic-real-dir'
directory.mkdir()
alias = root / 'alias'
alias.symlink_to(directory, target_is_directory=True)
target = alias / 'not-created.txt'
monitor = PhaseBMonitor(allowed_files=[target], scope='REGRESSION').install()
try:
    target.write_text('synthetic')
except PhaseBStop:
    pass
print(json.dumps({'exists': (directory / 'not-created.txt').exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result["exists"])
        self.assertEqual(result["stop"]["trigger"], "UNEXPECTED_WRITE")

    def test_database_network_and_subprocess_fail_closed(self):
        for operation, trigger in (
                ("sqlite3.connect(':memory:')", "UNEXPECTED_WRITE"),
                ("socket.socket()", "NETWORK_CALL_DETECTED"),
                ("subprocess.run(['git', '-C', str(repo), 'status'])", "UNEXPECTED_WRITE"),
                ("sys.audit('import', 'apscheduler.synthetic', None, None, None, None)",
                 "SCHEDULER_STARTED"),
                ("sys.audit('import', 'openai.synthetic', None, None, None, None)",
                 "LLM_CALL_DETECTED")):
            with self.subTest(operation=operation):
                result = self.child(f"""
monitor = PhaseBMonitor(allowed_git_command=('git', '-C', str(repo), 'rev-parse', 'HEAD'),
                        scope='REGRESSION').install()
try:
    {operation}
except PhaseBStop:
    pass
print(json.dumps(monitor.stop_event))
""")
                self.assertEqual(result["trigger"], trigger)
                self.assertTrue(result["expected_negative_test"])

    def test_dotdot_path_cannot_hide_symlink_ancestor(self):
        result = self.child("""
other = root / 'other'
other.mkdir()
(other / 'child').mkdir()
(root / 'alias').symlink_to(other / 'child', target_is_directory=True)
monitor = PhaseBMonitor(allowed_files=[root / 'synthetic.txt'], scope='REGRESSION').install()
try:
    (root / 'alias' / '..' / 'synthetic.txt').write_text('synthetic')
except PhaseBStop:
    pass
print(json.dumps({'exists': (other / 'synthetic.txt').exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result["exists"])
        self.assertEqual(result["stop"]["trigger"], "UNEXPECTED_WRITE")

    def test_sink_failure_cannot_erase_original_stop(self):
        result = self.child("""
def broken_sink(event):
    (root / 'sink-not-authorized.txt').write_text('synthetic')
monitor = PhaseBMonitor(scope='REGRESSION', event_sink=broken_sink).install()
try:
    socket.socket()
except PhaseBStop:
    pass
print(json.dumps({'exists': (root / 'sink-not-authorized.txt').exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result["exists"])
        self.assertEqual(result["stop"]["trigger"], "NETWORK_CALL_DETECTED")
        self.assertTrue(result["stop"]["event_sink_failed"])

    def test_relative_open_cannot_escape_through_directory_fd(self):
        result = self.child("""
other = root / 'other'
other.mkdir()
os.chdir(root)
fd = os.open(other, os.O_RDONLY | os.O_DIRECTORY)
monitor = PhaseBMonitor(allowed_files=[root / 'synthetic.txt'], scope='REGRESSION').install()
try:
    os.open('synthetic.txt', os.O_WRONLY | os.O_CREAT, dir_fd=fd)
except PhaseBStop:
    pass
os.close(fd)
print(json.dumps({'exists': (other / 'synthetic.txt').exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result['exists'])
        self.assertEqual(result['stop']['trigger'], 'UNEXPECTED_WRITE')

    def test_git_environment_override_is_rejected(self):
        result = self.child("""
command = ('git', '-C', str(repo), 'rev-parse', 'HEAD')
monitor = PhaseBMonitor(allowed_git_command=command, scope='REGRESSION').install()
os.environ['GIT_TRACE'] = str(root / 'never-created-git-trace')
try:
    subprocess.check_output(command, stderr=subprocess.DEVNULL)
except PhaseBStop:
    pass
print(json.dumps({'exists': (root / 'never-created-git-trace').exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result["exists"])
        self.assertEqual(result["stop"]["trigger"], "UNEXPECTED_WRITE")

    def test_benign_git_pager_and_disabled_prompt_do_not_false_positive(self):
        result = self.child("""
command = ('git', '-C', str(repo), 'rev-parse', 'HEAD')
os.environ['GIT_PAGER'] = 'cat'
os.environ['GIT_TERMINAL_PROMPT'] = '0'
monitor = PhaseBMonitor(allowed_git_command=command, scope='REGRESSION').install()
head = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True).strip()
print(json.dumps({'head_length': len(head), 'stop': monitor.stop_event}))
""")
        self.assertEqual(result, {"head_length": 40, "stop": None})

    def test_unknown_directory_and_followup_operations_stay_stopped(self):
        result = self.child("""
approved = root / 'approved'
monitor = PhaseBMonitor(allowed_directories=[approved], scope='REGRESSION').install()
for target in (root / 'unauthorized', approved):
    try:
        target.mkdir()
    except PhaseBStop:
        pass
print(json.dumps({'created': approved.exists(), 'stop': monitor.stop_event}))
""")
        self.assertFalse(result["created"])
        self.assertEqual(result["stop"]["trigger"], "UNKNOWN_FILE_CREATED")


if __name__ == "__main__":
    unittest.main()
