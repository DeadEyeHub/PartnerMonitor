import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from partner_monitor.web_logging import WebLog, read_events, export_log
from partner_monitor.web_media import post_json, ProviderError


class WebLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_live_request_response_retry_redaction_and_html_escape(self):
        logger=WebLog(self.root,'testjob')
        success=Mock(status_code=200)
        success.json.return_value={'usage':{'total_tokens':123,'cost':0.001},'content':'<script>alert(1)</script> secret-value','api_key':'another-secret'}
        def send(*args,**kwargs):
            # The request is already durable and visible before transport starts.
            self.assertTrue((logger.folder/'index.html').exists())
            self.assertEqual(read_events(logger.path)[-1]['event'],'HTTP_REQUEST')
            return responses.pop(0)
        responses=[Mock(status_code=429),success]
        with redirect_stdout(io.StringIO()),patch.dict(os.environ,{'TAVILY_API_KEY':'secret-value'}),logger.scope(company='123',stage='tavily'),patch('partner_monitor.web_media.requests.post',side_effect=send),patch('partner_monitor.web_media.time.sleep'):
            output=post_json('https://api.tavily.com/search','secret-value',{'query':'Example','authorization':'secret-value'})
        self.assertEqual(output['usage']['total_tokens'],123)
        events=read_events(logger.path)
        self.assertEqual([e['event'] for e in events],['HTTP_REQUEST','HTTP_RESPONSE','HTTP_RETRY','HTTP_REQUEST','HTTP_RESPONSE','PROVIDER_RESULT'])
        stored=logger.path.read_text(encoding='utf-8')
        self.assertNotIn('secret-value',stored);self.assertNotIn('another-secret',stored)
        self.assertEqual(events[-1]['response']['usage']['cost'],0.001)
        self.assertIn('duration_ms',events[1])
        html=(logger.folder/'000006.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)

    def test_resume_preserves_timeline_and_can_rebuild_html(self):
        with redirect_stdout(io.StringIO()):
            first=WebLog(self.root,'job')
            first.emit('PASS_STARTED')
            with first.path.open('a',encoding='utf-8') as stream:stream.write('{"incomplete":')
            resumed=WebLog(self.root,'job',self.root/'other')
            resumed.emit('PASS_FINISHED')
        self.assertEqual([e['sequence'] for e in read_events(first.path)],[1,2])
        self.assertTrue((resumed.folder/'000001.html').exists())
        rebuilt=export_log(self.root,'job',self.root/'rebuilt')
        self.assertIn('PASS_FINISHED',rebuilt.read_text(encoding='utf-8'))

    def test_transport_error_is_logged_without_exception_secret(self):
        import requests
        logger=WebLog(self.root,'job')
        with redirect_stdout(io.StringIO()),logger.scope(stage='openrouter'),patch('partner_monitor.web_media.requests.post',side_effect=requests.Timeout('sensitive-value')),patch('partner_monitor.web_media.time.sleep'),self.assertRaises(ProviderError):
            post_json('https://openrouter.ai/api/v1/chat/completions','key',{})
        events=read_events(logger.path)
        self.assertEqual(sum(e['event']=='HTTP_NETWORK_ERROR' for e in events),3)
        self.assertNotIn('sensitive-value',logger.path.read_text(encoding='utf-8'))

    def test_invalid_json_response_is_logged(self):
        logger=WebLog(self.root,'job')
        response=Mock(status_code=200);response.json.side_effect=ValueError('unsafe provider body')
        with redirect_stdout(io.StringIO()),logger.scope(),patch('partner_monitor.web_media.requests.post',return_value=response),self.assertRaises(ProviderError):
            post_json('https://api.tavily.com/search','key',{})
        self.assertEqual(read_events(logger.path)[-1]['event'],'INVALID_RESPONSE_JSON')
        self.assertNotIn('unsafe provider body',logger.path.read_text(encoding='utf-8'))
