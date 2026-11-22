"""Public management-page reads must report transport failures honestly."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from smart_home.manager import SmartHomeManager
from smart_home.adapters.network import RouterAdapter


class NetworkReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_scrape_distinguishes_page_read_from_settings_access(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = SmartHomeManager({'data_dir': folder})
            manager._scan = {'devices': [{'id': 'router:192.0.2.1', 'protocol': 'router',
                                         'ip': '192.0.2.1', 'ports': [80], 'fields': {}}]}
            page = {'url': 'http://192.0.2.1:80/', 'status': 200, 'server': 'test',
                    'text': '<title>Router Login</title><form><input name="password" value="secret"></form>'}
            try:
                with patch('smart_home.adapters.network._page', new=AsyncMock(return_value=page)):
                    result = await manager.control('router:192.0.2.1', 'scrape')
                self.assertIs(result.get('ok'), True)
                self.assertIs(result.get('public_only'), True)
                self.assertEqual(result['http_status'], 200)
                self.assertEqual(result['title'], 'Router Login')
                self.assertNotIn('secret', str(result))
            finally:
                await manager.close()

    async def test_unreachable_or_auth_required_is_not_success(self):
        adapter = RouterAdapter(SimpleNamespace(ip='192.0.2.1', ports=[80]))
        for status in (None, 401, 403, 503):
            with self.subTest(status=status):
                page = None if status is None else {
                    'url': 'http://192.0.2.1:80/', 'status': status, 'server': '', 'text': 'Login required'}
                with patch('smart_home.adapters.network._page', new=AsyncMock(return_value=page)):
                    result = await adapter.scrape()
                self.assertIs(result.get('ok'), False)
                self.assertTrue(result.get('error'))
                self.assertIs(result.get('public_only'), True)

    async def test_page_capability_is_readable_structured_data(self):
        caps = await RouterAdapter(SimpleNamespace(ip='192.0.2.1', ports=[80])).list_capabilities()
        self.assertEqual(caps[0]['kind'], 'json')
        self.assertIs(caps[0].get('readable'), True)
        self.assertIs(caps[0]['writable'], False)


if __name__ == '__main__':
    unittest.main()
