"""Real Streamlit AppTest; optional locally, mandatory in Runtime CI."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

AVAILABLE = importlib.util.find_spec('streamlit') is not None
if os.environ.get('REQUIRE_STREAMLIT') == '1' and not AVAILABLE:
    raise RuntimeError('Streamlit runtime dependency required for E6')


@unittest.skipUnless(AVAILABLE, 'Streamlit unavailable; E6 UI NOT_VERIFIED locally')
class StreamlitRuntimeTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        from test_dashboard_runtime import runtime_inputs
        from windows_compat import use_simulated_directory_fsync
        use_simulated_directory_fsync(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment, self.record = runtime_inputs(temporary.name)
        runtime_environment = patch.dict(os.environ, environment)
        runtime_environment.start()
        self.addCleanup(runtime_environment.stop)
        self.at = AppTest.from_file(Path(__file__).resolve().parents[1] / 'dashboard' / 'app.py', default_timeout=60)
        self.at.run()
        self.assertFalse(self.at.exception)
        self.runtime = self.at.session_state['runtime']
        self.db = self.runtime.db
        self.addCleanup(self.db.close)

    def test_status_quality_evidence_prompt_and_explicit_prepare_button(self):
        at = self.at.run()
        self.assertFalse(at.exception)
        at.text_input(key='evidence_id').input(self.record.evidence_id)
        next(b for b in at.button if b.label == '准备分析').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(self.runtime.status, 'READY_FOR_REVIEW')
        self.assertEqual([m.value for m in at.metric], ['PARTIAL', '1'])
        self.assertIn(self.runtime.prompt.user_prompt, at.code[0].value)
        self.assertEqual(self.runtime.executor.calls, [])

    def test_human_buttons_persist_review_and_archive(self):
        from test_dashboard_runtime import confirm_items
        self.runtime.prepare(self.record.evidence_id)
        self.at.session_state['actor'] = 'test-reviewer'
        at = self.at.run()
        next(b for b in at.button if b.label == '生成报告').click().run()
        self.assertFalse(at.exception)
        next(b for b in at.button if b.label.startswith('人工确认 ') and not b.disabled).click().run()
        self.assertFalse(at.exception)
        self.assertEqual(len(self.runtime.flow.events), 1)
        confirm_items(self.runtime)
        at.run()
        next(b for b in at.button if b.label == '准备归档快照').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)
        next(b for b in at.button if b.label == '人工确认并归档').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(self.runtime.status, 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 1)

    def test_unconfigured_app_is_partial_and_confirmation_disabled(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        with patch.dict(os.environ, {'V1_RUNTIME_FACTORY': ''}):
            at = AppTest.from_file(Path(__file__).resolve().parents[1] / 'dashboard' / 'app.py').run()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, 'PARTIAL')
        self.assertTrue(at.button[0].disabled)
