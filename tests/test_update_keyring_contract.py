"""Ensure OTA trust roots are placed where Fedora 44 systemd-sysupdate reads them."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR_RING = "mkosi.extra/usr/lib/systemd/import-pubring.pgp"


class UpdateKeyringContractTests(unittest.TestCase):
    def test_release_build_exports_correct_vendor_keyring(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn(VENDOR_RING, workflow)
        self.assertIn("gpg --batch --export", workflow)
        self.assertNotIn("import-pubring.gpg", workflow)

    def test_installable_live_iso_exports_same_pinned_key(self):
        workflow = (ROOT / ".github/workflows/native-live-iso.yml").read_text()
        export = (ROOT / "scripts/export-bootstrap-public-keys.sh").read_text()
        self.assertIn("scripts/export-bootstrap-public-keys.sh", workflow)
        self.assertIn(VENDOR_RING, workflow)
        self.assertIn(VENDOR_RING, export)
        self.assertIn('export_one "$VEYRA_UPDATE_SIGNING_KEY" update', export)
        self.assertNotIn("import-pubring.gpg", export)

    def test_signature_verification_is_never_disabled(self):
        definitions = ROOT / "mkosi.extra/usr/lib/sysupdate.d"
        transfers = sorted(definitions.glob("*.transfer"))
        self.assertTrue(transfers)
        for transfer in transfers:
            contents = transfer.read_text()
            self.assertIn("Verify=yes", contents, transfer.name)
            self.assertNotIn("Verify=no", contents, transfer.name)


if __name__ == "__main__":
    unittest.main()
