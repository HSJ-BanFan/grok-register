from email.message import EmailMessage
from unittest import TestCase
from unittest.mock import patch

import email_register


class FakeImapClient:
    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.logged_in = None
        self.selected = None
        self.searched = None

    def login(self, user, password):
        self.logged_in = (user, password)
        return "OK", []

    def select(self, mailbox):
        self.selected = mailbox
        return "OK", []

    def search(self, charset, *criteria):
        self.searched = (charset, criteria)
        return "OK", [b"42"]

    def fetch(self, msg_id, parts):
        message = EmailMessage()
        message["From"] = "info@x.ai"
        message["Subject"] = "Your xAI verification code"
        message.set_content("Your verification code is MM0-SF3")
        return "OK", [(b"42 (RFC822 {100}", message.as_bytes())]

    def logout(self):
        return "OK", []


class ImapEmailTests(TestCase):
    def test_get_email_and_token_returns_configured_imap_account(self):
        with (
            patch.object(
                email_register,
                "IMAP_CONFIG",
                {
                    "enabled": True,
                    "user": "nhuh72123@gmail.com",
                    "password": "app-password",
                },
                create=True,
            ),
            patch.object(
                email_register,
                "create_temp_email",
                side_effect=AssertionError("temp mail API should not be used"),
            ),
        ):
            self.assertEqual(
                email_register.get_email_and_token(),
                ("nhuh72123@gmail.com", "imap"),
            )

    def test_wait_for_code_via_imap_extracts_verification_code(self):
        clients = []

        def client_factory(server, port):
            client = FakeImapClient(server, port)
            clients.append(client)
            return client

        with patch.object(
            email_register,
            "IMAP_CONFIG",
            {
                "enabled": True,
                "server": "imap.gmail.com",
                "port": 993,
                "user": "nhuh72123@gmail.com",
                "password": "app-password",
                "search_filter": '(FROM "info@x.ai")',
            },
            create=True,
        ):
            code = email_register.wait_for_code_via_imap(
                timeout=1,
                client_factory=client_factory,
            )

        self.assertEqual(code, "MM0-SF3")
        self.assertEqual(clients[0].server, "imap.gmail.com")
        self.assertEqual(clients[0].port, 993)
        self.assertEqual(clients[0].logged_in, ("nhuh72123@gmail.com", "app-password"))
        self.assertEqual(clients[0].selected, "INBOX")
        self.assertTrue(clients[0].searched[1])

    def test_get_oai_code_strips_hyphen_from_imap_code(self):
        with (
            patch.object(email_register, "wait_for_code_via_imap", lambda timeout: "MM0-SF3", create=True),
            patch.object(
                email_register,
                "wait_for_verification_code",
                side_effect=AssertionError("temp mail API should not be used"),
            ),
            patch.object(
                email_register,
                "IMAP_CONFIG",
                {
                    "enabled": True,
                    "user": "nhuh72123@gmail.com",
                    "password": "app-password",
                },
                create=True,
            ),
        ):
            self.assertEqual(
                email_register.get_oai_code("imap", "nhuh72123@gmail.com"),
                "MM0SF3",
            )
