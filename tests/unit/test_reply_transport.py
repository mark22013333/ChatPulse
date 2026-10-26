import json
import unittest
from unittest.mock import Mock, patch

import requests
from google.oauth2.credentials import Credentials

from core.chat_client import GoogleChatClient
from core.errors import ChatPulseError


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result.headers['Content-Type'] = 'application/json'
    return result


def credentials():
    result = Credentials(token='synthetic-access-token')
    result.refresh = Mock(side_effect=lambda request: setattr(result, 'token', 'synthetic-renewed-token'))
    return result


class ReplyTransportTest(unittest.TestCase):
    def test_single_send_does_not_replay_after_unauthorized(self):
        creds = credentials()
        persisted = Mock()
        client = GoogleChatClient(creds, on_token_refresh=persisted)
        with patch.object(requests.Session, 'request', side_effect=[
            response(401, {'error': {'code': 401, 'status': 'UNAUTHENTICATED'}}),
            response(200, {'name': 'spaces/target/messages/must-not-send'}),
        ]) as transport:
            with self.assertRaises(ChatPulseError):
                client.send_message('spaces/target', '合成回話', single_attempt=True)
        self.assertEqual(transport.call_count, 1)
        creds.refresh.assert_not_called()
        persisted.assert_not_called()

    def test_single_send_does_not_follow_transport_redirects(self):
        class RedirectAdapter(requests.adapters.BaseAdapter):
            def __init__(self, status):
                self.status, self.calls = status, []

            def send(self, request, **kwargs):
                self.calls.append((request.method, request.url, request.body))
                result = response(self.status if len(self.calls) == 1 else 200, {})
                result.request, result.url = request, request.url
                result._content_consumed = True
                result.headers['Location'] = 'https://chat.googleapis.com/v1/spaces/target/messages'
                return result

            def close(self):
                pass

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                client = GoogleChatClient(credentials())
                adapter = RedirectAdapter(status)
                client._reply_session.mount('https://', adapter)
                with self.assertRaises(ChatPulseError):
                    client.send_message('spaces/target', '合成回話', single_attempt=True)
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(adapter.calls[0][0], 'POST')

    def test_legacy_read_retains_authentication_recovery(self):
        creds = credentials()
        persisted = Mock()
        client = GoogleChatClient(creds, on_token_refresh=persisted)
        with patch.object(requests.Session, 'request', side_effect=[
            response(401, {'error': {'code': 401, 'status': 'UNAUTHENTICATED'}}),
            response(200, {'name': 'spaces/target/messages/fake'}),
        ]) as transport:
            result = client.get_message('spaces/target/messages/fake')
        self.assertEqual(result['name'], 'spaces/target/messages/fake')
        self.assertEqual(transport.call_count, 2)
        creds.refresh.assert_called_once()
        persisted.assert_called_once()

    def test_recent_query_sends_only_the_selected_thread(self):
        client = GoogleChatClient(credentials())
        with patch.object(requests.Session, 'request', return_value=response(200, {'messages': []})) as transport:
            self.assertEqual(client.read_reply_recent('spaces/target', 50, thread_name='spaces/target/threads/q'), [])
        self.assertEqual(transport.call_args.kwargs['params']['filter'], 'thread.name = "spaces/target/threads/q"')
        self.assertEqual(transport.call_count, 1)
