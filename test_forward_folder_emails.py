import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import forward_folder_emails as app


APP_CONFIG = {
    "folder_id": "42",
    "receiver_email": ["recipient@example.com"],
    "cc": ["copy@example.com"],
}
RUNTIME_CONFIG = {
    "host": "mail.example.com",
    "email": "user@example.com",
    "password": "secret",
}


class ForwardFolderEmailsTests(unittest.TestCase):
    @staticmethod
    def make_client(message_ids):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.search_messages.return_value = SimpleNamespace(
            messages=tuple(SimpleNamespace(id=message_id) for message_id in message_ids)
        )
        return client

    def test_search_and_dry_run_do_not_send_or_save(self):
        client = self.make_client(["1", "2"])
        with patch.object(app, "load_config", return_value=APP_CONFIG):
            with patch.object(app, "load_runtime_config", return_value=RUNTIME_CONFIG):
                with patch.object(app, "load_forwarded_ids", return_value=set()):
                    with patch.object(app, "save_forwarded_ids") as save:
                        with patch.object(app, "ZimbraClient", return_value=client) as factory:
                            self.assertEqual(app.run(dry_run=True), 0)

        factory.assert_called_once_with({**RUNTIME_CONFIG, "verify_ssl": True})
        client.search_messages.assert_called_once_with(
            folder_id="42", limit=250, sort_by="dateAsc"
        )
        client.forward_message.assert_not_called()
        save.assert_not_called()

    def test_successful_forwarding_saves_each_message(self):
        client = self.make_client(["1", "2"])
        saved_states = []
        with patch.object(app, "load_config", return_value=APP_CONFIG):
            with patch.object(app, "load_runtime_config", return_value=RUNTIME_CONFIG):
                with patch.object(app, "load_forwarded_ids", return_value=set()):
                    with patch.object(
                        app,
                        "save_forwarded_ids",
                        side_effect=lambda ids: saved_states.append(set(ids)),
                    ):
                        with patch.object(app, "ZimbraClient", return_value=client):
                            self.assertEqual(app.run(), 0)

        self.assertEqual(
            client.forward_message.call_args_list,
            [
                call("1", to=APP_CONFIG["receiver_email"], cc=APP_CONFIG["cc"]),
                call("2", to=APP_CONFIG["receiver_email"], cc=APP_CONFIG["cc"]),
            ],
        )
        self.assertEqual(saved_states, [{"1"}, {"1", "2"}])

    def test_failure_preserves_successfully_forwarded_ids(self):
        client = self.make_client(["1", "2"])
        client.forward_message.side_effect = [None, RuntimeError("send failed")]
        saved_states = []
        with patch.object(app, "load_config", return_value=APP_CONFIG):
            with patch.object(app, "load_runtime_config", return_value=RUNTIME_CONFIG):
                with patch.object(app, "load_forwarded_ids", return_value=set()):
                    with patch.object(
                        app,
                        "save_forwarded_ids",
                        side_effect=lambda ids: saved_states.append(set(ids)),
                    ):
                        with patch.object(app, "ZimbraClient", return_value=client):
                            self.assertEqual(app.run(), 1)

        self.assertEqual(
            client.forward_message.call_args_list,
            [
                call("1", to=APP_CONFIG["receiver_email"], cc=APP_CONFIG["cc"]),
                call("2", to=APP_CONFIG["receiver_email"], cc=APP_CONFIG["cc"]),
            ],
        )
        self.assertEqual(saved_states, [{"1"}, {"1"}])


if __name__ == "__main__":
    unittest.main()
