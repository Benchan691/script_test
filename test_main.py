import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import main as app
from zimbra_client import Message


APP_CONFIG = {
    "folder_id": ["42", "99"],
    "official": {
        "to": ["recipient@example.com"],
        "cc": ["copy@example.com"],
    },
    "test": {
        "to": ["tester@example.com"],
        "cc": ["test-cc@example.com"],
    },
}
RUNTIME_CONFIG = {
    "host": "mail.example.com",
    "email": "user@example.com",
    "password": "secret",
}


def request_addresses(request, type_code):
    return [
        element.get("a")
        for element in request.iter()
        if element.tag.endswith("}e") and element.get("t") == type_code
    ]


class ForwardFolderEmailsTests(unittest.TestCase):
    @staticmethod
    def make_client(folder_messages):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        def search_messages(**kwargs):
            folder_id = kwargs["folder_id"]
            ids = folder_messages.get(folder_id, [])
            return SimpleNamespace(
                messages=tuple(SimpleNamespace(id=message_id) for message_id in ids)
            )

        client.search_messages.side_effect = search_messages
        messages_by_id = {
            message_id: Message(
                id=message_id,
                subject=f"PURE subject {message_id}",
                body_html="<p>Original message</p>",
            )
            for ids in folder_messages.values()
            for message_id in ids
        }
        client.get_message.side_effect = lambda message_id: messages_by_id[message_id]
        return client

    def _run(self, client, **kwargs):
        with patch.object(app, "load_config", return_value=APP_CONFIG):
            with patch.object(app, "load_runtime_config", return_value=RUNTIME_CONFIG):
                with patch.object(app, "ZimbraClient", return_value=client) as factory:
                    result = app.run(**kwargs)
        return result, factory

    def test_string_folder_id_is_accepted(self):
        folder_ids, official_to, official_cc, test_to, test_cc = app.validate_app_config(
            {
                "folder_id": "42",
                "official": {"to": "recipient@example.com", "cc": ["copy@example.com"]},
                "test": {"to": ["tester@example.com"], "cc": []},
            }
        )
        self.assertEqual(folder_ids, ["42"])
        self.assertEqual(official_to, ["recipient@example.com"])
        self.assertEqual(official_cc, ["copy@example.com"])
        self.assertEqual(test_to, ["tester@example.com"])
        self.assertEqual(test_cc, [])

    def test_official_run_forwards_unread_and_marks_read(self):
        client = self.make_client({"42": ["1"], "99": ["2"]})
        result, factory = self._run(client)

        self.assertEqual(result, 0)
        factory.assert_called_once_with({**RUNTIME_CONFIG, "verify_ssl": True})
        self.assertEqual(
            client.search_messages.call_args_list,
            [
                call(query="is:unread", folder_id="42", limit=250, sort_by="dateAsc"),
                call(query="is:unread", folder_id="99", limit=250, sort_by="dateAsc"),
            ],
        )
        self.assertEqual(client.get_message.call_args_list, [call("1"), call("2")])
        self.assertEqual(len(client.request.call_args_list), 2)
        request = client.request.call_args_list[0].args[0]
        message = next(element for element in request.iter() if element.tag.endswith("}m"))
        subject = next(element for element in message if element.tag.endswith("}su"))
        content = next(
            element
            for element in message.iter()
            if element.tag.endswith("}content")
        )
        self.assertEqual(subject.text, "Fwd: PURE subject 1")
        self.assertIn("Dear Cloudfall", content.text)
        self.assertIn("<p>Original message</p>", content.text)
        self.assertEqual(request_addresses(request, "t"), ["recipient@example.com"])
        self.assertEqual(request_addresses(request, "c"), ["copy@example.com"])
        self.assertEqual(client.mark_read.call_args_list, [call("1"), call("2")])

    def test_failure_marks_only_successfully_forwarded_messages_read(self):
        client = self.make_client({"42": ["1", "2"]})
        client.request.side_effect = [None, RuntimeError("send failed")]
        result, _ = self._run(client)

        self.assertEqual(result, 1)
        self.assertEqual(client.get_message.call_args_list, [call("1"), call("2")])
        self.assertEqual(len(client.request.call_args_list), 2)
        self.assertEqual(client.mark_read.call_args_list, [call("1")])

    def test_test_mode_sends_unread_to_test_recipients_without_marking_read(self):
        client = self.make_client({"42": ["1"], "99": ["2"]})
        result, _ = self._run(client, test=True)

        self.assertEqual(result, 0)
        self.assertEqual(
            client.search_messages.call_args_list,
            [
                call(query="is:unread", folder_id="42", limit=250, sort_by="dateAsc"),
                call(query="is:unread", folder_id="99", limit=250, sort_by="dateAsc"),
            ],
        )
        self.assertEqual(len(client.request.call_args_list), 2)
        request = client.request.call_args_list[0].args[0]
        self.assertEqual(request_addresses(request, "t"), ["tester@example.com"])
        self.assertEqual(request_addresses(request, "c"), ["test-cc@example.com"])
        client.mark_read.assert_not_called()

    def test_dry_run_sends_first_message_per_folder_to_test_without_marking_read(self):
        client = self.make_client({"42": ["1"], "99": ["2"]})
        result, _ = self._run(client, dry_run=True)

        self.assertEqual(result, 0)
        self.assertEqual(
            client.search_messages.call_args_list,
            [
                call(folder_id="42", limit=1, sort_by="dateAsc"),
                call(folder_id="99", limit=1, sort_by="dateAsc"),
            ],
        )
        self.assertEqual(client.get_message.call_args_list, [call("1"), call("2")])
        self.assertEqual(len(client.request.call_args_list), 2)
        request = client.request.call_args_list[0].args[0]
        self.assertEqual(request_addresses(request, "t"), ["tester@example.com"])
        self.assertEqual(request_addresses(request, "c"), ["test-cc@example.com"])
        client.mark_read.assert_not_called()

    def test_dry_run_and_test_cannot_be_combined(self):
        client = self.make_client({"42": ["1"]})
        with self.assertRaises(ValueError):
            self._run(client, dry_run=True, test=True)
        client.search_messages.assert_not_called()
        client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
