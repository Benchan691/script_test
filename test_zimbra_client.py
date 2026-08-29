import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import search_zimbra_folder
import send_weekday_gmail


CONFIG = {
    "host": "mail.example.com",
    "email": "user@example.com",
    "password": "secret",
}


class ZimbraClientMigrationTests(unittest.TestCase):
    def make_client(self, folders=(), messages=()):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.list_folders.return_value = folders
        client.search_messages.return_value = SimpleNamespace(messages=messages)
        return client

    def test_target_message_window(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        now = dt.datetime(2026, 8, 29, 12, 0, tzinfo=timezone)
        messages = (
            SimpleNamespace(date=dt.datetime(2026, 8, 28, 20, 0, tzinfo=timezone)),
            SimpleNamespace(date=dt.datetime(2026, 8, 29, 10, 0, tzinfo=timezone)),
        )
        client = self.make_client(
            folders=(SimpleNamespace(id="42", name=search_zimbra_folder.TARGET_FOLDER_NAME),),
            messages=messages,
        )

        with patch.object(search_zimbra_folder, "load_runtime_config", return_value=CONFIG):
            with patch.object(search_zimbra_folder, "ZimbraClient", return_value=client) as factory:
                self.assertTrue(search_zimbra_folder.has_target_email(now=now))

        factory.assert_called_once_with({**CONFIG, "verify_ssl": True})
        client.search_messages.assert_called_once_with(folder_id="42", limit=250)

    def test_message_outside_window_returns_false(self):
        timezone = dt.timezone(dt.timedelta(hours=8))
        now = dt.datetime(2026, 8, 29, 12, 0, tzinfo=timezone)
        client = self.make_client(
            folders=(SimpleNamespace(id="42", name=search_zimbra_folder.TARGET_FOLDER_NAME),),
            messages=(SimpleNamespace(date=dt.datetime(2026, 8, 29, 10, 0, tzinfo=timezone)),),
        )

        with patch.object(search_zimbra_folder, "load_runtime_config", return_value=CONFIG):
            with patch.object(search_zimbra_folder, "ZimbraClient", return_value=client):
                self.assertFalse(search_zimbra_folder.has_target_email(now=now))

    def test_missing_folder_returns_false(self):
        client = self.make_client(folders=(SimpleNamespace(id="7", name="Other"),))

        with patch.object(search_zimbra_folder, "load_runtime_config", return_value=CONFIG):
            with patch.object(search_zimbra_folder, "ZimbraClient", return_value=client):
                self.assertFalse(search_zimbra_folder.has_target_email())

        client.search_messages.assert_not_called()

    def test_missing_config_does_not_create_client(self):
        missing_config = {"host": "", "email": "", "password": ""}

        with patch.object(search_zimbra_folder, "load_runtime_config", return_value=missing_config):
            with patch.object(search_zimbra_folder, "ZimbraClient") as factory:
                self.assertFalse(search_zimbra_folder.has_target_email())

        factory.assert_not_called()

    def test_send_and_dry_run_use_html_and_expected_recipients(self):
        client = self.make_client()
        message = {
            "to": ["recipient@example.com"],
            "cc": ["copy@example.com"],
            "subject": "Subject",
            "body": "<p>Body</p>",
        }

        with patch.object(send_weekday_gmail, "load_runtime_config", return_value=CONFIG):
            with patch.object(send_weekday_gmail, "ZimbraClient", return_value=client) as factory:
                send_weekday_gmail.send_message(message)
                send_weekday_gmail.send_dry_run_message(message)

        factory.assert_has_calls([call({**CONFIG, "verify_ssl": True})] * 2)
        self.assertEqual(
            client.send_message.call_args_list,
            [
                call(
                    to=["recipient@example.com"],
                    cc=["copy@example.com"],
                    subject="Subject",
                    html="<p>Body</p>",
                ),
                call(
                    to=[send_weekday_gmail.DRY_RUN_TEST_RECIPIENT],
                    cc=[],
                    subject="Subject [TEST]",
                    html="<p>Body</p>",
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
