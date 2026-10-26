from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/mirror-package-site.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_package_mirror", SourceFileLoader("veyra_package_mirror", str(SCRIPT))
)
assert SPEC and SPEC.loader
mirror = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror)


class PackageMirrorTests(unittest.TestCase):
    def test_reads_primary_location_and_checksum_from_signed_metadata(self) -> None:
        repomd = b'''<repomd xmlns="http://linux.duke.edu/metadata/repo">
          <data type="primary">
            <checksum type="sha256">deadbeef</checksum>
            <location href="repodata/a-primary.xml.gz"/>
          </data>
        </repomd>'''
        self.assertEqual(
            mirror.parse_primary_location(repomd),
            ("repodata/a-primary.xml.gz", "sha256", "deadbeef"),
        )

    def test_accepts_a_veyra_rpm_record(self) -> None:
        primary = b'''<metadata xmlns="http://linux.duke.edu/metadata/common">
          <package type="rpm">
            <name>veyra-shell-tools</name>
            <checksum type="sha256">012345</checksum>
            <location href="veyra-shell-tools-0.1.0-1.fc44.x86_64.rpm"/>
          </package>
        </metadata>'''
        self.assertEqual(
            mirror.parse_primary_metadata(primary),
            [("veyra-shell-tools", "veyra-shell-tools-0.1.0-1.fc44.x86_64.rpm", "sha256", "012345")],
        )

    def test_rejects_non_veyra_and_traversing_rpm_records(self) -> None:
        for name, href in (("bash", "bash.rpm"), ("veyra-bad", "../outside.rpm")):
            with self.subTest(name=name), self.assertRaises(mirror.MirrorError):
                primary = f'''<metadata xmlns="http://linux.duke.edu/metadata/common">
                  <package type="rpm"><name>{name}</name>
                    <checksum type="sha256">012345</checksum>
                    <location href="{href}"/>
                  </package>
                </metadata>'''.encode()
                mirror.parse_primary_metadata(primary)

    def test_rejects_non_normalized_repository_paths(self) -> None:
        with self.assertRaises(mirror.MirrorError):
            mirror.safe_relative_href("repodata//primary.xml.gz")

    def test_requires_every_rpm_signature_line_to_end_in_exact_ok(self) -> None:
        self.assertTrue(mirror.rpm_signatures_are_valid(
            "Header V4 RSA/SHA256 Signature, key ID abcd: OK\n"
            "Header SHA256 digest: OK\nPayload SHA256 digest: OK\n"
        ))
        self.assertFalse(mirror.rpm_signatures_are_valid(
            "Header V4 RSA/SHA256 Signature, key ID abcd: NOT OK\n"
        ))
        self.assertFalse(mirror.rpm_signatures_are_valid(
            "Header V4 RSA/SHA256 Signature, key ID abcd: OK\n"
            "Payload V4 RSA/SHA256 Signature: BAD\n"
        ))

    @patch.object(mirror, "fetch", side_effect=mirror.NotPublished("missing"))
    def test_reports_a_channel_without_repository_metadata(self, _mocked_fetch) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(
                mirror.download_channel("https://example.invalid/site", "main", Path(temporary))
            )

    @patch.object(mirror, "verify_signature")
    @patch.object(mirror, "fetch")
    def test_preserves_a_published_but_empty_channel(self, mocked_fetch, _mocked_verify) -> None:
        repomd = b'''<repomd xmlns="http://linux.duke.edu/metadata/repo">
          <data type="primary">
            <checksum type="sha256">{}</checksum>
            <location href="repodata/primary.xml"/>
          </data>
        </repomd>'''
        primary = b'<metadata xmlns="http://linux.duke.edu/metadata/common"/>'
        import hashlib
        repomd = repomd.replace(b"{}", hashlib.sha256(primary).hexdigest().encode())
        mocked_fetch.side_effect = [repomd, b"test-signature", primary]
        with tempfile.TemporaryDirectory() as temporary:
            count = mirror.download_channel(
                "https://example.invalid/site", "main", Path(temporary)
            )
            self.assertEqual(count, 0)

    @patch.object(mirror.subprocess, "run")
    def test_lists_primary_key_fingerprints_without_subkeys(self, mocked_run) -> None:
        mocked_run.return_value.stdout = "\n".join([
            "pub:-:3072:1:1234567890ABCDEF:0::::::scESC:",
            "fpr:::::::::AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:",
            "sub:-:3072:1:1234567890ABCDEF:0::::::e:",
            "fpr:::::::::BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:",
        ])
        self.assertEqual(
            mirror.list_primary_key_fingerprints(),
            {"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        )


if __name__ == "__main__":
    unittest.main()
